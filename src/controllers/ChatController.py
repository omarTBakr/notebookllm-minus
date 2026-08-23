"""Turns a question into an answer, grounded in the chat's documents when it has any.

The one piece the project was missing: retrieval already worked and the chat
client was already built, but nothing fed the passages to the model.
"""

from collections.abc import AsyncIterator

from enums import ChatRole
from factories.llmchatting import LLMChattingInterface
from templates import TemplateParser

from .BaseController import BaseController
from .NLPController import NLPController


class ChatController(BaseController):
    """Retrieval + prompt assembly + streaming generation.

    Deliberately does not touch MongoDB. The route persists the turns; keeping
    that out of here means the whole answering path can be exercised with a
    fake generation client and no database.
    """

    def __init__(
        self,
        generation_client: LLMChattingInterface,
        nlp_controller: NLPController,
    ) -> None:

        super().__init__()

        self.generation_client = generation_client

        self.nlp = nlp_controller

    # --- groundedness ---------------------------------------------------------

    async def is_grounded(self, chat_id: str) -> bool:
        """Whether this chat has vectors to answer from.

        Read from the vector index rather than from Chat.has_documents: an
        upload that failed after chunking but before indexing would set the
        flag while leaving nothing to retrieve, and the answer would then claim
        sources it never had.
        """
        info = await self.nlp.get_index_info(chat_id)

        if not info["exists"]:
            return False

        return bool(info["info"].get("points_count"))

    # --- prompt assembly ------------------------------------------------------

    def build_prompt(
        self, question: str, hits: list[dict], lang: str
    ) -> tuple[str, str]:
        """Return ``(system_prompt, user_prompt)`` for this question.

        With hits, the grounded instructions plus the numbered documents. With
        none, an ordinary assistant and the bare question — the same code path
        either way, which is what lets one endpoint serve both kinds of chat.
        """
        parser = TemplateParser(lang=lang, default_lang=self.settings.DEFAULT_LANG)

        if not hits:
            return parser.get("chat", "system_prompt"), question

        documents = [
            parser.get(
                "rag",
                "document_prompt",
                {
                    # 1-based so it lines up with the [1] the model is told to cite.
                    "num": number,
                    "source": (hit.get("metadata") or {}).get("source") or "unknown",
                    "content": hit.get("text") or "",
                },
            )
            for number, hit in enumerate(hits, start=1)
        ]

        footer = parser.get("rag", "footer_prompt", {"question": question})

        user_prompt = "\n\n".join(documents + [footer])

        return parser.get("rag", "system_prompt"), user_prompt

    @staticmethod
    def to_citations(hits: list[dict], names: dict[str, str] | None = None) -> list[dict]:
        """The parts of a hit worth showing and storing next to an answer.

        ``names`` maps asset_id to the source's current name. The vector
        payload carries a copy of the name from index time, which goes stale
        the moment someone renames a source, so the live name wins when the
        caller supplies one. A plain dict rather than a model keeps this class
        free of MongoDB, as promised above.
        """
        citations = []
        names = names or {}

        for number, hit in enumerate(hits, start=1):
            metadata = hit.get("metadata") or {}
            asset_id = metadata.get("asset_id")

            citations.append(
                {
                    "num": number,
                    # Falls back to the indexed copy for an asset that has
                    # since been deleted — a stale name beats "unknown".
                    "source": names.get(asset_id) or metadata.get("source") or "unknown",
                    "asset_id": asset_id,
                    "chunk_order": metadata.get("chunk_order"),
                    # `is not None`, not truthiness: 0.0 is a real score — an
                    # orthogonal match — and reporting it as "no score"
                    # loses the one number that says the hit was poor.
                    "score": (
                        round(hit["score"], 4) if hit.get("score") is not None else None
                    ),
                }
            )

        return citations

    # --- answering ------------------------------------------------------------

    async def answer_stream(
        self,
        chat_id: str,
        question: str,
        lang: str,
        history: list[dict] | None = None,
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        asset_ids: list[str] | None = None,
        source_names: dict[str, str] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield the answer as a sequence of events.

        ``meta`` first (so the UI can show sources before any text arrives),
        then ``thinking`` and ``delta`` pieces as they come, then ``done``.
        The caller assembles the deltas — and only the deltas — to persist the
        finished answer; the scratchpad is shown live and not stored.
        """
        hits: list[dict] = []

        # An empty list is not the same as None: it means every source was
        # switched off, so there is nothing to retrieve and the answer should
        # be ungrounded rather than searching all of them.
        if asset_ids != [] and await self.is_grounded(chat_id):
            hits = await self.nlp.search(chat_id, question, limit=top_k, asset_ids=asset_ids)

        system_prompt, user_prompt = self.build_prompt(question, hits, lang)

        citations = self.to_citations(hits, source_names)

        self.logger.info(
            "Answering chat %r (grounded=%s, hits=%d, lang=%s, history=%d, sources=%s)",
            chat_id,
            bool(hits),
            len(hits),
            lang,
            len(history or []),
            "all" if asset_ids is None else len(asset_ids),
        )

        yield {"type": "meta", "grounded": bool(hits), "citations": citations}

        # The system turn leads the history so the provider's _split_system can
        # lift it out for the backends that want it separately.
        messages = [
            self.generation_client.construct_message(ChatRole.SYSTEM, system_prompt)
        ]
        messages.extend(history or [])

        # None for either falls through to the provider's configured default,
        # so a chat that was never tuned follows .env.
        async for piece in self.generation_client.stream_text(
            prompt=user_prompt,
            chat_history=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            # Reasoning is surfaced under its own event type so the UI can show
            # it while waiting and keep it out of the stored answer.
            if piece["kind"] == "thinking":
                yield {"type": "thinking", "text": piece["text"]}
            else:
                yield {"type": "delta", "text": piece["text"]}

        yield {"type": "done"}
