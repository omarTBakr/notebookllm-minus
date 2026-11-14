"""Turns a question into an answer, grounded in the chat's documents when it has any.

The one piece the project was missing: retrieval already worked and the chat
client was already built, but nothing fed the passages to the model.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter

from langchain_core.messages import HumanMessage

from application.guardrails.base import InputGuardrail, OutputGuardrail
from application.guardrails.stream import guard_stream
from application.prompts import TemplateParser
from application.providers.chatting import LLMChattingInterface
from application.providers.reranking import NoopReranker, RerankerInterface
from shared.enums import ChatRole
from shared.utils import tracing
from shared.utils.metrics import (
    ANSWERS,
    RERANK_FAILURES,
    RETRIEVAL_HITS,
    RETRIEVAL_SECONDS,
)

from ..core.BaseService import BaseService
from ..memory.MemoryService import MemoryService
from .NLPService import NLPService
from .QueryPlanner import QueryPlan, fallback_plan, plan_query, search_queries


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
        planner_client: LLMChattingInterface | None = None,
        reranker: RerankerInterface | None = None,
        input_guardrail: InputGuardrail | None = None,
        output_guardrail: OutputGuardrail | None = None,
        image_index=None,
        vision: bool = False,
    ) -> None:

        super().__init__()

        self.generation_client = generation_client

        self.nlp = nlp_controller
        # None for a caller that doesn't want personal facts folded in (e.g.
        # a test exercising retrieval alone) -- answer_stream treats it the
        # same as a user with no stored facts.
        self.memory = memory_controller
        # A second client on the same model, built with thinking=False, for
        # the calls whose output is not shown as it streams: the planner's
        # structured call and the complex path's sub-answers. A reasoning
        # model asked for JSON leaves `content` empty and puts everything in
        # `reasoning_content`, which fails validation on every retry; and a
        # sub-answer gains nothing from a scratchpad nobody sees but latency.
        # None falls back to generation_client, which suits a test;
        # dependencies.chat_service always supplies a dedicated one.
        self.planner_client = planner_client or generation_client
        # The pass-through when none is given, so retrieval has one code path.
        self.reranker = reranker or NoopReranker()
        # None switches a stage off (GUARDRAILS_ENABLED=false, or a test).
        self.input_guardrail = input_guardrail
        self.output_guardrail = output_guardrail
        # ImageIndexService, or None when MULTIMODAL_ENABLED is off: then no
        # image collection is searched and nothing below changes.
        self.image_index = image_index
        # Whether the answering model can look at pictures. Without vision an
        # image hit still reaches it, as its caption.
        self.vision = vision

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
        numbers: list[int] | None = None,
    ) -> tuple[str, str]:
        """Return ``(system_prompt, user_prompt)`` for this question.

        ``numbers`` gives each hit the citation number it should carry, when
        that is not simply its 1-based position (a sub-answer on the complex
        path shows its passages under their global numbers).

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
            # 1-based so it lines up with the [1] the model is told to cite.
            numbered = list(zip(numbers or range(1, len(hits) + 1), hits))
            parts.extend(self._document(parser, number, hit, numbered) for number, hit in numbered)

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
    def is_image(hit: dict) -> bool:
        return (hit.get("metadata") or {}).get("kind") == "image"

    def _content(self, parser: TemplateParser, hit: dict) -> str:
        """A hit's text as the prompt shows it: an image hit is labelled as one."""
        text = hit.get("text") or ""
        if self.is_image(hit):
            return parser.get("rag", "image_content_prompt", {"caption": text})
        return text

    def _document(self, parser: TemplateParser, number: int, hit: dict, numbered: list) -> str:
        """One numbered document block for the prompt.

        A partner brought in by the links (see rag/links.py) says which
        document it belongs with, on its heading line, so the model reads a
        figure next to the passage that refers to it rather than as an
        unrelated source.
        """
        block = parser.get(
            "rag",
            "document_prompt",
            {
                "num": number,
                "source": (hit.get("metadata") or {}).get("source") or "unknown",
                "content": self._content(parser, hit),
            },
        )
        origin = hit.get("linked_from")
        if origin:
            for other_number, other in numbered:
                if other.get("id") == origin and other is not hit:
                    note = parser.get("rag", "linked_document_note", {"num": other_number})
                    heading, newline, rest = block.partition("\n")
                    block = f"{heading} {note}{newline}{rest}"
                    break
        return block

    async def _with_partners(self, hits: list[dict], link_lookup) -> list[dict]:
        """*hits* plus the chunks linked to them: a passage's figure, a figure's text.

        Appended after the ranked hits, never in place of one, so a partner
        can add to an answer but cannot push out a better passage. The
        lookup caps how many come (LINK_MAX_*_PER_ANSWER). Any failure keeps
        the hits as they were.
        """
        if not hits or link_lookup is None or not self.settings.LINKS_ENABLED:
            return hits
        try:
            partners = await link_lookup(hits)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not follow chunk links; answering from the hits alone: %s", exc)
            return hits
        if partners:
            self.logger.debug("Added %d linked chunk(s) to %d hit(s)", len(partners), len(hits))
        return list(hits) + list(partners)

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
        number_of = {hit.get("id"): number for number, hit in enumerate(hits, start=1) if hit.get("id")}

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
                    # A spreadsheet chunk has a row (and a sheet, for a workbook):
                    # the row as Excel or Sheets numbers it. Labelled in the UI,
                    # which owns the language.
                    "row": located.get("row"),
                    "sheet": located.get("sheet"),
                    # `is not None`, not truthiness: 0.0 is a real score — an
                    # orthogonal match — and reporting it as "no score"
                    # loses the one number that says the hit was poor.
                    "score": (round(hit["score"], 4) if hit.get("score") is not None else None),
                    # The cross-encoder's 0..1 relevance, when a reranker ran.
                    # Kept beside the vector score rather than replacing it:
                    # the two disagreeing is exactly what is worth seeing.
                    "rerank_score": (round(hit["rerank_score"], 4) if hit.get("rerank_score") is not None else None),
                    # A figure or an uploaded picture: the UI shows a thumbnail
                    # (the chunk's image endpoint) and opens the page at it.
                    "kind": "image" if metadata.get("kind") == "image" else "text",
                    # Set when this was not retrieved on its own but brought in
                    # as the partner of citation [n]: the figure a passage
                    # refers to, or the passage that explains a figure.
                    "linked_to": number_of.get(hit.get("linked_from")) if hit.get("linked_from") else None,
                }
            )

        return citations

    # --- planning and retrieval ------------------------------------------------

    async def _plan(self, question: str, lang: str, history: list[dict] | None) -> QueryPlan:
        """How to answer *question*: see QueryPlanner.

        With AGENTIC_RAG_ENABLED off there is no planning call at all, and the
        plan is one grounded pass on the raw question.
        """
        if not self.settings.AGENTIC_RAG_ENABLED:
            return fallback_plan(question)

        return await plan_query(self.planner_client, question, lang, history)

    async def _search_and_rerank(
        self,
        chat_id: str,
        queries: list[str],
        rerank_query: str,
        top_k: int,
        asset_ids: list[str] | None,
        image_query: bytes | None = None,
    ) -> list[dict]:
        """Search every query, merge, rerank against *rerank_query*, keep top_k.

        With the image index on, each query is also searched against the
        notebook's pictures (a typed question, embedded into the picture
        space), and *image_query* (a picture the user attached) is searched
        for similar pictures. Image hits carry their caption as text, so the
        reranker scores them on the same scale as passages, and they compete
        for the same top_k.

        With a reranker, each query fetches RERANK_CANDIDATES hits instead of
        top_k: the cross-encoder can only promote a passage vector search
        handed it, so the pool has to be wider than what is finally kept.
        Without one, this is exactly the old merge-and-cut.
        """
        limit = max(top_k, self.settings.RERANK_CANDIDATES) if self.reranker.ENABLED else top_k

        with tracing.observation(
            "retrieve",
            as_type="retriever",
            input={"queries": queries, "limit": limit},
        ) as span:
            searches = [self.nlp.search(chat_id, query, limit=limit, asset_ids=asset_ids) for query in queries]
            if self.image_index is not None:
                searches += [self._image_search(chat_id, text=query, asset_ids=asset_ids) for query in queries]
                if image_query is not None:
                    searches.append(self._image_search(chat_id, image=image_query, asset_ids=asset_ids))

            results = await asyncio.gather(*searches)

            merged: dict[str, dict] = {}
            for hits in results:
                for hit in hits:
                    hit_id = hit.get("id")
                    existing = merged.get(hit_id)
                    if existing is None or (hit.get("score") or 0) > (existing.get("score") or 0):
                        # The same figure can come back from both collections
                        # (its caption from the text one); it stays an image.
                        if existing is not None and self.is_image(existing) and not self.is_image(hit):
                            hit = {**hit, "metadata": {**(hit.get("metadata") or {}), "kind": "image"}}
                        merged[hit_id] = hit

            ranked = sorted(
                merged.values(),
                key=lambda hit: hit["score"] if hit.get("score") is not None else float("-inf"),
                reverse=True,
            )

            span.update(output={"candidates": len(ranked)})

        if not self.reranker.ENABLED or not ranked:
            return self._vector_cut(ranked, top_k)

        return await self._rerank(rerank_query, ranked, top_k)

    def _vector_cut(self, ranked: list[dict], top_k: int) -> list[dict]:
        """The best of *ranked* by vector score alone, for when no reranker scored them.

        Vector scores from two collections are not comparable, so with no
        reranker to put them on one scale the passages keep their top_k and
        the pictures are added after them, capped.
        """
        texts = [hit for hit in ranked if not self.is_image(hit)]
        pictures = [hit for hit in ranked if self.is_image(hit)]
        return texts[:top_k] + pictures[: self.settings.IMAGE_TOP_K]

    async def _image_search(self, chat_id: str, **query) -> list[dict]:
        """One image-collection search; a failure costs the pictures, not the answer."""
        try:
            return await self.image_index.search(chat_id, **query)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Image search failed; answering from text only: %s", exc)
            return []

    async def _rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]:
        """The best *top_k* of *hits* by cross-encoder relevance.

        Any failure (timeout, the container down, a bad body) keeps vector
        order: a reranker is an improvement on retrieval, never a dependency
        of answering.
        """
        provider = type(self.reranker).__name__

        try:
            ranked = await asyncio.wait_for(
                self.reranker.rerank(query, [hit.get("text") or "" for hit in hits], top_n=top_k),
                timeout=self.settings.RERANKER_TIMEOUT_S,
            )
        except Exception as exc:
            RERANK_FAILURES.labels(provider).inc()
            self.logger.warning("Rerank failed, keeping vector order: %s", exc)
            tracing.event("rerank.fallback", level="WARNING", metadata={"error": str(exc)[:300]})
            # The same cut as having no reranker, not hits[:top_k]: *hits* is
            # sorted on raw scores from both collections, and the picture
            # embedder scores everything higher (~0.55-0.6 against ~0.4 for
            # passages), so a plain cut handed every slot to figure captions.
            return self._vector_cut(hits, top_k)

        floor = self.settings.RERANK_MIN_SCORE
        kept = []
        for index, score in ranked:
            if score < floor:
                continue
            kept.append({**hits[index], "rerank_score": score})

        return kept

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
        image: tuple[bytes, str] | None = None,
        image_caption: str | None = None,
        image_lookup: Callable[[list[dict]], Awaitable[dict]] | None = None,
        link_lookup: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield the answer as a sequence of events.

        *image* is a picture the user attached, as ``(bytes, mime)``, and
        *image_caption* its caption. The caption joins the question for
        planning, text search and the input guard; the picture itself is
        searched against the notebook's pictures and, for a vision model,
        attached to the answer. *image_lookup* takes the hits and returns
        ``{(asset_id, chunk_order): (bytes, mime)}`` for the image hits, so
        a vision model can see the figures it cites; like page_lookup it is
        opaque so this class never opens a database. *link_lookup* takes the
        hits and returns their linked partner chunks (rag/links.py), which
        are appended after them.

        ``meta`` first (so the UI can show sources before any text arrives),
        then ``thinking`` and ``delta`` pieces as they come, then ``done``.
        The caller assembles the deltas — and only the deltas — to persist the
        finished answer; the scratchpad is shown live and not stored.

        A grounded question is planned first (see QueryPlanner):

        * ``chitchat`` skips retrieval and answers as plain chat;
        * ``simple`` retrieves on the plan's queries, reranks against the
          standalone question and answers in one streamed pass;
        * ``complex`` emits ``step`` events while each part is retrieved,
          reranked and answered on its own, then streams one answer written
          from those parts (see :meth:`_answer_complex`).

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

        With guardrails on, the question is checked before anything else runs
        (a flagged one gets a ``guardrail`` event and no answer), and the
        streamed answer is checked passage by passage (see guard_stream).
        """
        parser = TemplateParser(lang=lang, default_lang=self.settings.DEFAULT_LANG)

        # What the user asked, as text: the question plus what their picture
        # shows. Planning, text retrieval and the guard all read this.
        searched = question
        if image_caption:
            note = parser.get("rag", "user_image_note", {"caption": image_caption})
            searched = f"{question}\n{note}".strip()

        refusal = await self._refusal(searched)
        if refusal is not None:
            yield refusal
            yield {"type": "done"}
            return

        memory_hits: list[dict] = []

        if self.memory is not None and user_id and searched:
            memory_hits = await self.memory.search(user_id, searched)

        hits: list[dict] = []

        # An empty list is not the same as None: it means every source was
        # switched off, so there is nothing to retrieve and the answer should
        # be ungrounded rather than searching all of them.
        grounded = asset_ids != [] and await self.is_grounded(chat_id)

        plan: QueryPlan | None = None
        if grounded:
            plan = await self._plan(searched, lang, history)
            # A picture to look up is never small talk, whatever the words were.
            if plan.intent == "chitchat" and image is None:
                grounded = False

        if grounded and plan.is_complex:
            async for event in self._answer_complex(
                chat_id=chat_id,
                question=question,
                plan=plan,
                lang=lang,
                history=history,
                top_k=top_k,
                temperature=temperature,
                max_tokens=max_tokens,
                asset_ids=asset_ids,
                source_names=source_names,
                page_lookup=page_lookup,
                memory_hits=memory_hits,
                image=image,
                image_lookup=image_lookup,
                link_lookup=link_lookup,
            ):
                yield event
            return

        if grounded:
            hits = await self._retrieve(chat_id, plan, searched, top_k, asset_ids, image, link_lookup)

        system_prompt, user_prompt = self.build_prompt(question, hits, memory_hits, lang)

        pictures, labels = await self._pictures(parser, hits, None, image, image_lookup)
        if labels:
            user_prompt += "\n\n" + parser.get("rag", "images_attached_prompt", {"labels": "; ".join(labels)})
        elif image_caption:
            # No vision: the model reads what the picture shows instead.
            user_prompt += "\n\n" + parser.get("rag", "user_image_note", {"caption": image_caption})

        meta = await self._meta(hits, source_names, page_lookup, memory_hits)

        self.logger.info(
            "Answering chat %r (grounded=%s, intent=%s, hits=%d, lang=%s, history=%d, sources=%s)",
            chat_id,
            bool(hits),
            plan.intent if plan else "ungrounded",
            len(hits),
            lang,
            len(history or []),
            "all" if asset_ids is None else len(asset_ids),
        )

        # Counted here rather than at the end: the stream can be abandoned
        # mid-answer, and an answer that started grounded still was.
        ANSWERS.labels(str(bool(hits)).lower()).inc()

        yield meta

        async for event in self._stream(
            searched, system_prompt, user_prompt, history, max_tokens, temperature, images=pictures
        ):
            yield event

    async def _refusal(self, searched: str) -> dict | None:
        """The ``guardrail`` event for a question the input guard flags, or None to go on."""
        if self.input_guardrail is None or not searched:
            return None

        update = await self.input_guardrail.abefore_model({"messages": [HumanMessage(content=searched)]})
        if update is None:
            return None

        verdict = update.get("guardrail_verdict") or {}
        return {
            "type": "guardrail",
            "stage": "input",
            "category": verdict.get("category", "undecided"),
            "message": update["messages"][-1].content,
        }

    async def _retrieve(
        self,
        chat_id: str,
        plan: QueryPlan,
        searched: str,
        top_k: int,
        asset_ids: list[str] | None,
        image: tuple[bytes, str] | None,
        link_lookup,
    ) -> list[dict]:
        """The hits for a one-pass answer: search, rerank, then linked partners. Timed."""
        started = perf_counter()

        # A chitchat plan only reaches here with a picture attached, and then
        # its rewritten queries are small talk: search on what was asked.
        planned = plan.intent != "chitchat"
        hits = await self._search_and_rerank(
            chat_id,
            search_queries(plan.sub_queries, searched) if planned else [searched],
            plan.standalone_query if planned else searched,
            top_k,
            asset_ids,
            image_query=image[0] if image else None,
        )
        hits = await self._with_partners(hits, link_lookup)

        RETRIEVAL_SECONDS.observe(perf_counter() - started)
        RETRIEVAL_HITS.observe(len(hits))

        return hits

    async def _pictures(
        self,
        parser: TemplateParser,
        hits: list[dict],
        numbers: list[int] | None,
        image: tuple[bytes, str] | None,
        image_lookup: Callable[[list[dict]], Awaitable[dict]] | None,
    ) -> tuple[list[tuple[bytes, str]], list[str]]:
        """The pictures to attach for a vision model, and a label for each.

        Image hits first, in citation order, then the user's own picture,
        capped at IMAGE_MAX_PER_ANSWER. Nothing for a model without vision:
        the captions are already in the prompt. A picture that cannot be
        loaded is skipped, never fatal.
        """
        if not self.vision:
            return [], []

        cap = self.settings.IMAGE_MAX_PER_ANSWER
        pictures: list[tuple[bytes, str]] = []
        labels: list[str] = []

        numbered = list(zip(numbers or range(1, len(hits) + 1), hits))
        image_hits = [(number, hit) for number, hit in numbered if self.is_image(hit)]
        room = cap - (1 if image is not None else 0)

        if image_hits and image_lookup is not None and room > 0:
            try:
                loaded = await image_lookup([hit for _, hit in image_hits[:room]])
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Could not load cited pictures: %s", exc)
                loaded = {}
            for number, hit in image_hits[:room]:
                metadata = hit.get("metadata") or {}
                picture = loaded.get((metadata.get("asset_id"), metadata.get("chunk_order")))
                if picture is not None:
                    pictures.append(picture)
                    labels.append(f"[{number}]")

        if image is not None:
            pictures.append(image)
            labels.append(parser.get("rag", "user_image_label"))

        return pictures, labels

    async def _meta(
        self,
        hits: list[dict],
        source_names: dict[str, str] | None,
        page_lookup: Callable[[list[dict]], Awaitable[dict]] | None,
        memory_hits: list[dict],
    ) -> dict:
        """The ``meta`` event: groundedness, citations and memories.

        One round trip before the first frame, and the meta frame is what the
        UI paints sources from — so it is deliberately kept to a single
        indexed lookup. A failure here costs the page links, nothing else:
        an unlinked citation is still a citation, and losing the answer over
        it would be a far worse trade.
        """
        pages = None
        if hits and page_lookup is not None:
            try:
                pages = await page_lookup(hits)
            except Exception as exc:
                self.logger.warning("Could not resolve citation pages: %s", exc)

        return {
            "type": "meta",
            "grounded": bool(hits),
            "citations": self.to_citations(hits, source_names, pages),
            "memories": MemoryService.to_citations(memory_hits),
        }

    async def _stream(
        self,
        question: str,
        system_prompt: str,
        user_prompt: str,
        history: list[dict] | None,
        max_tokens: int | None,
        temperature: float | None,
        images: list[tuple[bytes, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """The final answer as events, through the output guardrail when on."""
        events = self._generate(system_prompt, user_prompt, history, max_tokens, temperature, images)

        if self.output_guardrail is None:
            async for event in events:
                yield event
            return

        async for event in guard_stream(
            events, self.output_guardrail, question, self.settings.GUARDRAIL_OUTPUT_CHUNK_CHARS
        ):
            yield event

    async def _generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: list[dict] | None,
        max_tokens: int | None,
        temperature: float | None,
        images: list[tuple[bytes, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Stream the final answer as ``thinking`` / ``delta`` events, then ``done``."""
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
            **({"images": images} if images else {}),
        ):
            # Reasoning is surfaced under its own event type so the UI can show
            # it while waiting and keep it out of the stored answer.
            if piece["kind"] == "thinking":
                yield {"type": "thinking", "text": piece["text"]}
            else:
                yield {"type": "delta", "text": piece["text"]}

        yield {"type": "done"}

    # --- the complex path ---------------------------------------------------------

    @staticmethod
    def number_globally(parts: list[list[dict]]) -> tuple[list[dict], list[list[int]]]:
        """One citation numbering across every part's hits.

        Returns the union of hits (deduplicated by id, in part order, then rank
        order) and, for each part, the global number of each of its hits. A
        passage found by two parts keeps one number, so the final answer can
        cite it once whichever part it came through.
        """
        union: list[dict] = []
        number_of: dict[str, int] = {}
        numbers: list[list[int]] = []

        for hits in parts:
            part_numbers = []
            for hit in hits:
                key = hit.get("id")
                if key not in number_of:
                    union.append(hit)
                    number_of[key] = len(union)
                part_numbers.append(number_of[key])
            numbers.append(part_numbers)

        return union, numbers

    async def _answer_complex(
        self,
        *,
        chat_id: str,
        question: str,
        plan: QueryPlan,
        lang: str,
        history: list[dict] | None,
        top_k: int,
        temperature: float | None,
        max_tokens: int | None,
        asset_ids: list[str] | None,
        source_names: dict[str, str] | None,
        page_lookup: Callable[[list[dict]], Awaitable[dict]] | None,
        memory_hits: list[dict],
        image: tuple[bytes, str] | None = None,
        image_lookup: Callable[[list[dict]], Awaitable[dict]] | None = None,
        link_lookup: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
    ) -> AsyncIterator[dict]:
        """Answer each part on its own, then write one answer from the parts.

        1. Every sub-query is retrieved and reranked against itself, in
           parallel. Each part keeps its own top_k, so one part's strong hits
           cannot crowd out another part's.
        2. Hits are numbered once across all parts, and ``meta`` goes out with
           all of them.
        3. Each part gets a short, cited sub-answer from its own passages only,
           in parallel; a ``step`` event marks each one as it finishes.
        4. One streamed call writes the final answer from the original question
           and the (part, sub-answer) pairs. It never sees the passages, so its
           citations are the ones the sub-answers carried.
        """
        parser = TemplateParser(lang=lang, default_lang=self.settings.DEFAULT_LANG)
        queries = plan.sub_queries
        total = len(queries)

        yield {"type": "step", "phase": "plan", "intent": plan.intent, "sub_queries": queries}

        _search_started = perf_counter()
        parts = await asyncio.gather(
            *(
                self._search_and_rerank(
                    chat_id,
                    # The part's query, and the user's own words when the
                    # planner translated the part (see search_queries).
                    search_queries([query], question),
                    query,
                    top_k,
                    asset_ids,
                    image_query=image[0] if image else None,
                )
                for query in queries
            )
        )
        # Each part brings its own partners, so a sub-answer sees the figure
        # its passages refer to.
        parts = list(await asyncio.gather(*(self._with_partners(part, link_lookup) for part in parts)))
        RETRIEVAL_SECONDS.observe(perf_counter() - _search_started)

        union, numbers = self.number_globally(parts)
        RETRIEVAL_HITS.observe(len(union))

        meta = await self._meta(union, source_names, page_lookup, memory_hits)

        self.logger.info(
            "Answering chat %r (grounded=%s, intent=complex, parts=%d, hits=%d, lang=%s)",
            chat_id,
            bool(union),
            total,
            len(union),
            lang,
        )
        ANSWERS.labels(str(bool(union)).lower()).inc()

        yield meta

        if not union:
            # Nothing found for any part: there is nothing to combine, so answer
            # as the ungrounded path would rather than synthesise from blanks.
            system_prompt, user_prompt = self.build_prompt(question, [], memory_hits, lang)
            async for event in self._stream(question, system_prompt, user_prompt, history, max_tokens, temperature):
                yield event
            return

        answers: list[str | None] = [None] * total
        tasks = {
            asyncio.create_task(
                self._subanswer(parser, query, hits, part_numbers, lang, temperature, image, image_lookup)
            ): index
            for index, (query, hits, part_numbers) in enumerate(zip(queries, parts, numbers))
        }

        try:
            for index, query in enumerate(queries):
                yield {
                    "type": "step",
                    "phase": "part",
                    "index": index,
                    "total": total,
                    "query": query,
                    "status": "started",
                }

            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    index = tasks[task]
                    answers[index] = task.result()
                    yield {
                        "type": "step",
                        "phase": "part",
                        "index": index,
                        "total": total,
                        "query": queries[index],
                        "status": "done",
                    }
        finally:
            # The reader pressed Stop, or something failed: do not leave model
            # calls running for an answer nobody will read.
            for task in tasks:
                if not task.done():
                    task.cancel()

        findings = "\n\n".join(
            parser.get("rag", "finding_prompt", {"num": index + 1, "question": query, "answer": answer})
            for index, (query, answer) in enumerate(zip(queries, answers))
        )

        synthesis, prompt_ref = parser.get_with_ref(
            "rag", "synthesis_prompt", {"question": question, "findings": findings}
        )

        if memory_hits:
            facts = "\n".join(
                parser.get("memory", "memory_fact_prompt", {"description": hit.get("text") or ""})
                for hit in memory_hits
            )
            synthesis = parser.get("memory", "memory_context_prompt", {"facts": facts}) + "\n\n" + synthesis

        yield {"type": "step", "phase": "synthesis", "total": total}

        with tracing.observation("synthesis", as_type="chain", input={"question": question, "parts": total}):
            with tracing.linked_prompt(prompt_ref):
                async for event in self._stream(
                    question, parser.get("rag", "system_prompt"), synthesis, history, max_tokens, temperature
                ):
                    yield event

    async def _subanswer(
        self,
        parser: TemplateParser,
        query: str,
        hits: list[dict],
        numbers: list[int],
        lang: str,
        temperature: float | None,
        image: tuple[bytes, str] | None = None,
        image_lookup: Callable[[list[dict]], Awaitable[dict]] | None = None,
    ) -> str:
        """A short, cited answer to one part, from that part's passages only.

        A vision model also sees this part's pictures (see _pictures); the
        synthesis that follows only reads the sub-answers, so this is where
        a figure is actually looked at on the complex path.

        Never raises: a part that cannot be answered (no passages, or the model
        call failed) says so, and the final answer reports the gap instead of
        the whole answer failing.
        """
        not_covered = parser.get("rag", "part_not_covered")

        if not hits:
            return not_covered

        with tracing.observation("subanswer", as_type="chain", input={"query": query, "passages": len(hits)}) as span:
            system_prompt = parser.get("rag", "system_prompt")
            numbered = list(zip(numbers, hits))
            documents = "\n\n".join(self._document(parser, number, hit, numbered) for number, hit in numbered)
            prompt, prompt_ref = parser.get_with_ref("rag", "subanswer_prompt", {"question": query})

            pictures, labels = await self._pictures(parser, hits, numbers, image, image_lookup)
            if labels:
                prompt += "\n\n" + parser.get("rag", "images_attached_prompt", {"labels": "; ".join(labels)})

            try:
                with tracing.linked_prompt(prompt_ref):
                    answer = await self.planner_client.generate_text(
                        prompt=f"{documents}\n\n{prompt}",
                        chat_history=[self.planner_client.construct_message(ChatRole.SYSTEM, system_prompt)],
                        max_tokens=self.settings.AGENTIC_SUBANSWER_MAX_TOKENS,
                        temperature=temperature,
                        **({"images": pictures} if pictures else {}),
                    )
            except Exception as exc:
                self.logger.warning("Sub-answer failed for one part; reporting it as not covered: %s", exc)
                span.update(level="WARNING", status_message=str(exc)[:300])
                return not_covered

            span.update(output=answer)
            return answer.strip() or not_covered
