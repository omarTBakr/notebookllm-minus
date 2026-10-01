"""Turns a question into an answer, grounded in the chat's documents when it has any.

The one piece the project was missing: retrieval already worked and the chat
client was already built, but nothing fed the passages to the model.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter

from application.prompts import TemplateParser
from application.providers.chatting import LLMChattingInterface
from shared.enums import ChatRole
from shared.utils.metrics import ANSWERS, RETRIEVAL_HITS, RETRIEVAL_SECONDS

from ..core.BaseService import BaseService
from ..memory.MemoryService import MemoryService
from .NLPService import NLPService
from .QueryDecomposer import decompose_query


class ChatService(BaseService):
    """Retrieval + prompt assembly + streaming generation.

    Deliberately does not touch MongoDB. The route persists the turns; keeping
    that out of here means the whole answering path can be exercised with a
    fake generation client and no database.
    """

    def __init__(
        self,
        generation_client: LLMChattingInterface,
        nlp_controller: NLPService,
        memory_controller: MemoryService | None = None,
        decompose_client: LLMChattingInterface | None = None,
    ) -> None:

        super().__init__()

        self.generation_client = generation_client

        self.nlp = nlp_controller
        # None for a caller that doesn't want personal facts folded in (e.g.
        # a test exercising retrieval alone) -- answer_stream treats it the
        # same as a user with no stored facts.
        self.memory = memory_controller
        # A second client, built with thinking=False, for QueryDecomposer's
        # generate_structured call -- see that module's docstring for why
        # generation_client (possibly a reasoning model) cannot be reused for
        # it: a reasoning model asked for structured output leaves `content`
        # empty and puts everything in `reasoning_content`, which fails
        # validation on every retry. None falls back to generation_client
        # itself, which is fine for a caller (e.g. a test) that does not care
        # about that failure mode -- decompose_query degrades to a single
        # search on the raw question if it ever hits it, same as omitting
        # decomposition entirely. dependencies.chat_service always supplies a
        # dedicated client in production, the same way it does for
        # MemoryService's chat_client.
        self.decompose_client = decompose_client or generation_client

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
        self,
        question: str,
        hits: list[dict],
        memory_hits: list[dict],
        lang: str,
    ) -> tuple[str, str]:
        """Return ``(system_prompt, user_prompt)`` for this question.

        Three shapes, not two:
          - Neither documents nor memories: an ordinary assistant, the bare
            question. Unchanged from before memory existed.
          - Document hits (memories optional): the grounded rag prompt, with a
            "what you know about this user" block appended when there are
            memory hits.
          - Memory hits but no document hits: still the *ordinary* system
            prompt — it must never claim to cite documents it wasn't given —
            with just the memory block ahead of the question.

        `memory_hits` is what MemoryService.search returned: the same
        closest-facts-to-this-question hits the "▸ Memory (n)" block in the
        UI is built from (see to_citations), so what the model was actually
        given and what the reader is shown never drift apart. They are never
        numbered or cited *inline* the way document hits are, though — the
        UI list is a display affordance, not an instruction to the model.
        See memory_fact_prompt's docstring for why asking a model to track two
        citation schemes in one prompt's prose is a bad idea here specifically.
        """
        parser = TemplateParser(lang=lang, default_lang=self.settings.DEFAULT_LANG)

        if not hits and not memory_hits:
            return parser.get("chat", "system_prompt"), question

        parts = []

        if hits:
            parts.extend(
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
            )

        if memory_hits:
            facts = "\n".join(
                parser.get("memory", "memory_fact_prompt", {"description": hit.get("text") or ""})
                for hit in memory_hits
            )
            parts.append(parser.get("memory", "memory_context_prompt", {"facts": facts}))

        # rag.footer_prompt says "using the documents above" -- correct only
        # when there are document hits. With facts but no documents, the bare
        # question is what the ungrounded path already sends; wrapping it in
        # a footer that talks about documents that were never provided would
        # be actively misleading, not just unnecessary.
        footer = parser.get("rag", "footer_prompt", {"question": question}) if hits else question

        user_prompt = "\n\n".join(parts + [footer])

        system_prompt = parser.get("rag", "system_prompt") if hits else parser.get("chat", "system_prompt")

        return system_prompt, user_prompt

    @staticmethod
    def to_citations(
        hits: list[dict],
        names: dict[str, str] | None = None,
        pages: dict[tuple[str, int], dict] | None = None,
    ) -> list[dict]:
        """The parts of a hit worth showing and storing next to an answer.

        ``names`` maps asset_id to the source's current name. The vector
        payload carries a copy of the name from index time, which goes stale
        the moment someone renames a source, so the live name wins when the
        caller supplies one. A plain dict rather than a model keeps this class
        free of MongoDB, as promised above.

        ``pages`` maps (asset_id, chunk_order) to the page fields, resolved by
        application/services/rag/citations.py. Passed in already-resolved for the same reason:
        this class does not get to open a database.

        Kept deliberately small. What this returns is not only sent to the
        browser, it is written into messages.citations and replayed on every
        history load, for the life of the notebook. Highlight geometry and
        chunk text are fetched on demand from the locate endpoint instead —
        putting them here would freeze a copy per answer that a re-ingest
        could not correct.
        """
        citations = []
        names = names or {}
        pages = pages or {}

        for number, hit in enumerate(hits, start=1):
            metadata = hit.get("metadata") or {}
            asset_id = metadata.get("asset_id")
            chunk_order = metadata.get("chunk_order")
            located = pages.get((asset_id, chunk_order)) or {}

            citations.append(
                {
                    "num": number,
                    # Falls back to the indexed copy for an asset that has
                    # since been deleted — a stale name beats "unknown".
                    "source": names.get(asset_id) or metadata.get("source") or "unknown",
                    "asset_id": asset_id,
                    # Kept because it is how the locate endpoint finds the
                    # passage again, not because anything displays it.
                    "chunk_order": chunk_order,
                    # 1-based, for the viewer. Never the 0-based `page` from
                    # chunk metadata — see application/services/rag/citations.py.
                    "page_number": located.get("page_number"),
                    # A display string, which may be "iv". Never parsed.
                    "page_label": located.get("page_label"),
                    # A video transcript's chunk has a moment instead of a
                    # page: seconds for the player, "12:34" for the reader.
                    "time_start": located.get("time_start"),
                    "time_label": located.get("time_label"),
                    # `is not None`, not truthiness: 0.0 is a real score — an
                    # orthogonal match — and reporting it as "no score"
                    # loses the one number that says the hit was poor.
                    "score": (round(hit["score"], 4) if hit.get("score") is not None else None),
                }
            )

        return citations

    # --- retrieval --------------------------------------------------------------

    async def _retrieve(
        self,
        chat_id: str,
        question: str,
        lang: str,
        top_k: int,
        asset_ids: list[str] | None,
    ) -> list[dict]:
        """The merged, best top-`top_k` hits across *question*'s sub-queries.

        `decompose_query` turns *question* into 1-3 retrieval-oriented queries
        -- either a real split for a compound question, or just a better
        rephrasing when it isn't one (see QueryDecomposer's docstring). Each
        runs through `NLPService.search` concurrently rather than in
        sequence: they are independent reads against the same collection, and
        there is no reason the second query's embedding call should wait on
        the first's round trip.

        The same chunk routinely surfaces for more than one sub-query --
        deduplication is by `id`, the field NLPService.search's hits carry
        from the vector store's own row id (see PostgresVectorRepository.
        search_by_vector's `SELECT id, ...`), which names one chunk uniquely
        regardless of which sub-query's embedding found it. Ties are broken by
        keeping the higher score: the same chunk can be scored differently by
        two different query embeddings, and the higher score is the more
        honest one to show and to rank by.

        Sorted by score descending and cut to `top_k` last, not per
        sub-query -- with up to MAX_SUB_QUESTIONS queries each returning up to
        `top_k` hits, capping per-query first would let a weak sub-query's
        hits crowd out a strong one's from a different sub-query.
        """
        sub_questions = await decompose_query(self.decompose_client, question, lang)

        results = await asyncio.gather(
            *(
                self.nlp.search(chat_id, sub_question, limit=top_k, asset_ids=asset_ids)
                for sub_question in sub_questions
            )
        )

        merged: dict[str, dict] = {}
        for hits in results:
            for hit in hits:
                hit_id = hit.get("id")
                existing = merged.get(hit_id)
                if existing is None or (hit.get("score") or 0) > (existing.get("score") or 0):
                    merged[hit_id] = hit

        ranked = sorted(
            merged.values(),
            key=lambda hit: hit["score"] if hit.get("score") is not None else float("-inf"),
            reverse=True,
        )

        return ranked[:top_k]

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
        page_lookup: Callable[[list[dict]], Awaitable[dict]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield the answer as a sequence of events.

        ``meta`` first (so the UI can show sources before any text arrives),
        then ``thinking`` and ``delta`` pieces as they come, then ``done``.
        The caller assembles the deltas — and only the deltas — to persist the
        finished answer; the scratchpad is shown live and not stored.

        ``page_lookup`` takes the hits and returns the page map that turns
        citations into links. An opaque awaitable rather than a repository:
        this class still never opens a database, so the whole answering path
        stays testable without one.

        ``user_id`` searches that user's stored `/memory` facts and folds the
        closest ones into the prompt, independent of document groundedness
        below — personal facts and document retrieval are separate signals,
        so a chat with no documents still gets personalised. Surfaced in the
        ``meta`` event as ``memories``, the same way document hits are
        surfaced as ``citations`` — see MemoryService.to_citations.
        """
        memory_hits: list[dict] = []

        if self.memory is not None and user_id and question:
            memory_hits = await self.memory.search(user_id, question)

        hits: list[dict] = []

        # An empty list is not the same as None: it means every source was
        # switched off, so there is nothing to retrieve and the answer should
        # be ungrounded rather than searching all of them.
        if asset_ids != [] and await self.is_grounded(chat_id):
            _search_started = perf_counter()
            hits = await self._retrieve(chat_id, question, lang, top_k, asset_ids)
            RETRIEVAL_SECONDS.observe(perf_counter() - _search_started)
            RETRIEVAL_HITS.observe(len(hits))

        system_prompt, user_prompt = self.build_prompt(question, hits, memory_hits, lang)

        # One round trip before the first frame, and the meta frame is what the
        # UI paints sources from — so it is deliberately kept to a single
        # indexed lookup. A failure here costs the page links, nothing else:
        # an unlinked citation is still a citation, and losing the answer over
        # it would be a far worse trade.
        pages = None
        if hits and page_lookup is not None:
            try:
                pages = await page_lookup(hits)
            except Exception as exc:
                self.logger.warning("Could not resolve citation pages: %s", exc)

        citations = self.to_citations(hits, source_names, pages)
        memories = MemoryService.to_citations(memory_hits)

        self.logger.info(
            "Answering chat %r (grounded=%s, hits=%d, lang=%s, history=%d, sources=%s)",
            chat_id,
            bool(hits),
            len(hits),
            lang,
            len(history or []),
            "all" if asset_ids is None else len(asset_ids),
        )

        # Counted here rather than at the end: the stream can be abandoned
        # mid-answer, and an answer that started grounded still was.
        ANSWERS.labels(str(bool(hits)).lower()).inc()

        yield {"type": "meta", "grounded": bool(hits), "citations": citations, "memories": memories}

        # The system turn leads the history so the provider's _split_system can
        # lift it out for the backends that want it separately.
        messages = [self.generation_client.construct_message(ChatRole.SYSTEM, system_prompt)]
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
