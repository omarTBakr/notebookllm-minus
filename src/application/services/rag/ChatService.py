"""Turns a question into an answer, grounded in the chat's documents when it has any.

The one piece the project was missing: retrieval already worked and the chat
client was already built, but nothing fed the passages to the model.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter

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
from .QueryPlanner import QueryPlan, fallback_plan, plan_query


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
                for number, hit in zip(numbers or range(1, len(hits) + 1), hits)
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
    ) -> list[dict]:
        """Search every query, merge, rerank against *rerank_query*, keep top_k.

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
            results = await asyncio.gather(
                *(self.nlp.search(chat_id, query, limit=limit, asset_ids=asset_ids) for query in queries)
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

            span.update(output={"candidates": len(ranked)})

        if not self.reranker.ENABLED or not ranked:
            return ranked[:top_k]

        return await self._rerank(rerank_query, ranked, top_k)

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
            return hits[:top_k]

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
    ) -> AsyncIterator[dict]:
        """Yield the answer as a sequence of events.

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
        """
        memory_hits: list[dict] = []

        if self.memory is not None and user_id and question:
            memory_hits = await self.memory.search(user_id, question)

        hits: list[dict] = []

        # An empty list is not the same as None: it means every source was
        # switched off, so there is nothing to retrieve and the answer should
        # be ungrounded rather than searching all of them.
        grounded = asset_ids != [] and await self.is_grounded(chat_id)

        plan: QueryPlan | None = None
        if grounded:
            plan = await self._plan(question, lang, history)
            if plan.intent == "chitchat":
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
            ):
                yield event
            return

        if grounded:
            _search_started = perf_counter()
            hits = await self._search_and_rerank(chat_id, plan.sub_queries, plan.standalone_query, top_k, asset_ids)
            RETRIEVAL_SECONDS.observe(perf_counter() - _search_started)
            RETRIEVAL_HITS.observe(len(hits))

        system_prompt, user_prompt = self.build_prompt(question, hits, memory_hits, lang)

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

        async for event in self._stream(system_prompt, user_prompt, history, max_tokens, temperature):
            yield event

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
        system_prompt: str,
        user_prompt: str,
        history: list[dict] | None,
        max_tokens: int | None,
        temperature: float | None,
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
            *(self._search_and_rerank(chat_id, [query], query, top_k, asset_ids) for query in queries)
        )
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
            async for event in self._stream(system_prompt, user_prompt, history, max_tokens, temperature):
                yield event
            return

        answers: list[str | None] = [None] * total
        tasks = {
            asyncio.create_task(self._subanswer(parser, query, hits, part_numbers, lang, temperature)): index
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
                    parser.get("rag", "system_prompt"), synthesis, history, max_tokens, temperature
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
    ) -> str:
        """A short, cited answer to one part, from that part's passages only.

        Never raises: a part that cannot be answered (no passages, or the model
        call failed) says so, and the final answer reports the gap instead of
        the whole answer failing.
        """
        not_covered = parser.get("rag", "part_not_covered")

        if not hits:
            return not_covered

        with tracing.observation("subanswer", as_type="chain", input={"query": query, "passages": len(hits)}) as span:
            system_prompt = parser.get("rag", "system_prompt")
            documents = "\n\n".join(
                parser.get(
                    "rag",
                    "document_prompt",
                    {
                        "num": number,
                        "source": (hit.get("metadata") or {}).get("source") or "unknown",
                        "content": hit.get("text") or "",
                    },
                )
                for number, hit in zip(numbers, hits)
            )
            prompt, prompt_ref = parser.get_with_ref("rag", "subanswer_prompt", {"question": query})

            try:
                with tracing.linked_prompt(prompt_ref):
                    answer = await self.planner_client.generate_text(
                        prompt=f"{documents}\n\n{prompt}",
                        chat_history=[self.planner_client.construct_message(ChatRole.SYSTEM, system_prompt)],
                        max_tokens=self.settings.AGENTIC_SUBANSWER_MAX_TOKENS,
                        temperature=temperature,
                    )
            except Exception as exc:
                self.logger.warning("Sub-answer failed for one part; reporting it as not covered: %s", exc)
                span.update(level="WARNING", status_message=str(exc)[:300])
                return not_covered

            span.update(output=answer)
            return answer.strip() or not_covered
