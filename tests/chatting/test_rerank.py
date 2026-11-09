"""Reranking between vector search and the prompt."""

import asyncio

import httpx
import pytest

from application.providers.reranking import (
    NoopReranker,
    RerankerFactory,
    RerankerInterface,
    TEIRerankerProvider,
)
from application.services.rag.ChatService import ChatService
from shared.exceptions import LLMProviderError
from tests.support.llm import FakeChatClient


def hit(hit_id, score, text=None):
    return {"id": hit_id, "score": score, "text": text or f"text {hit_id}", "metadata": {"asset_id": "a"}}


class FakeNLP:
    """Serves canned hits per query and records the limit asked for."""

    def __init__(self, by_query):
        self.by_query = by_query
        self.limits = []

    async def search(self, chat_id, query, limit, asset_ids=None):
        self.limits.append(limit)
        return self.by_query.get(query, [])[:limit]


class ScriptedReranker(RerankerInterface):
    """Scores each text by a lookup, so the expected order is explicit."""

    def __init__(self, scores=None, error=None, delay=0.0):
        super().__init__(model_id="scripted")
        self.scores = scores or {}
        self.error = error
        self.delay = delay
        self.queries = []

    async def _rerank(self, query, texts):
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [(i, self.scores.get(text, 0.0)) for i, text in enumerate(texts)]


def service(nlp, reranker=None):
    return ChatService(generation_client=FakeChatClient(), nlp_controller=nlp, reranker=reranker)


# --- ChatService ------------------------------------------------------------------


async def test_without_a_reranker_it_is_the_old_merge_and_cut():
    nlp = FakeNLP({"q": [hit("1", 0.9), hit("2", 0.8), hit("3", 0.7)]})

    hits = await service(nlp)._search_and_rerank("c", ["q"], "q", top_k=2, asset_ids=None)

    assert [h["id"] for h in hits] == ["1", "2"]
    assert nlp.limits == [2]
    assert all("rerank_score" not in h for h in hits)


async def test_reranker_widens_the_pool_and_reorders(settings):
    pool = [hit(str(i), 1.0 - i / 100) for i in range(30)]
    nlp = FakeNLP({"q": pool})
    # The best passage by far sits 25th in vector order.
    reranker = ScriptedReranker({"text 24": 0.99, "text 0": 0.5})

    hits = await service(nlp, reranker)._search_and_rerank("c", ["q"], "the question", top_k=2, asset_ids=None)

    assert nlp.limits == [settings.RERANK_CANDIDATES]
    # 24 is outside RERANK_CANDIDATES=20, so it was never a candidate; 0 wins,
    # and the tie at 0.0 below it keeps vector order.
    assert [h["id"] for h in hits] == ["0", "1"]


async def test_reranker_promotes_a_candidate_and_scores_it():
    nlp = FakeNLP({"q": [hit("1", 0.9), hit("2", 0.8), hit("3", 0.7)]})
    reranker = ScriptedReranker({"text 3": 0.95, "text 1": 0.4, "text 2": 0.1})

    hits = await service(nlp, reranker)._search_and_rerank("c", ["q"], "the question", top_k=2, asset_ids=None)

    assert [h["id"] for h in hits] == ["3", "1"]
    assert hits[0]["rerank_score"] == 0.95
    assert hits[0]["score"] == 0.7  # the vector score is kept alongside
    assert reranker.queries == ["the question"]


async def test_merged_duplicates_are_reranked_once():
    nlp = FakeNLP({"a": [hit("1", 0.9), hit("2", 0.5)], "b": [hit("1", 0.95), hit("3", 0.6)]})
    reranker = ScriptedReranker({"text 1": 0.9, "text 2": 0.8, "text 3": 0.7})

    hits = await service(nlp, reranker)._search_and_rerank("c", ["a", "b"], "q", top_k=5, asset_ids=None)

    assert [h["id"] for h in hits] == ["1", "2", "3"]


async def test_rerank_min_score_drops_weak_passages(monkeypatch):
    monkeypatch.setenv("RERANK_MIN_SCORE", "0.5")
    nlp = FakeNLP({"q": [hit("1", 0.9), hit("2", 0.8)]})
    reranker = ScriptedReranker({"text 1": 0.2, "text 2": 0.7})

    hits = await service(nlp, reranker)._search_and_rerank("c", ["q"], "q", top_k=5, asset_ids=None)

    assert [h["id"] for h in hits] == ["2"]


async def test_failed_rerank_falls_back_to_vector_order():
    nlp = FakeNLP({"q": [hit("1", 0.9), hit("2", 0.8), hit("3", 0.7)]})
    reranker = ScriptedReranker(error=LLMProviderError("down"))

    hits = await service(nlp, reranker)._search_and_rerank("c", ["q"], "q", top_k=2, asset_ids=None)

    assert [h["id"] for h in hits] == ["1", "2"]


async def test_slow_rerank_times_out_to_vector_order(monkeypatch):
    monkeypatch.setenv("RERANKER_TIMEOUT_S", "0.05")
    nlp = FakeNLP({"q": [hit("1", 0.9), hit("2", 0.8)]})
    reranker = ScriptedReranker({"text 2": 1.0}, delay=1.0)

    hits = await service(nlp, reranker)._search_and_rerank("c", ["q"], "q", top_k=1, asset_ids=None)

    assert [h["id"] for h in hits] == ["1"]


def test_citations_carry_the_rerank_score():
    citations = ChatService.to_citations([{**hit("1", 0.5), "rerank_score": 0.912345}, hit("2", 0.4)])

    assert citations[0]["rerank_score"] == 0.9123
    assert citations[1]["rerank_score"] is None


# --- providers --------------------------------------------------------------------


async def test_noop_keeps_order():
    ranked = await NoopReranker().rerank("q", ["a", "b", "c"])
    assert [i for i, _ in ranked] == [0, 1, 2]


async def test_tei_provider_posts_and_sorts():
    seen = {}

    def handler(request: httpx.Request):
        import json

        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"index": 0, "score": 0.1}, {"index": 1, "score": 0.9}])

    provider = TEIRerankerProvider(base_url="http://reranker", model_id="m")
    provider.client = httpx.AsyncClient(base_url="http://reranker", transport=httpx.MockTransport(handler))

    ranked = await provider.rerank("query", ["a", "b"], top_n=1)

    assert ranked == [(1, 0.9)]
    assert seen["path"] == "/rerank"
    assert seen["body"]["query"] == "query" and seen["body"]["texts"] == ["a", "b"]
    assert seen["body"]["raw_scores"] is False


async def test_tei_provider_raises_provider_error_on_http_failure():
    provider = TEIRerankerProvider(base_url="http://reranker", model_id="m")
    provider.client = httpx.AsyncClient(
        base_url="http://reranker", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )

    with pytest.raises(LLMProviderError):
        await provider.rerank("q", ["a"])


def test_factory_builds_noop_when_disabled(settings):
    assert isinstance(RerankerFactory(settings).create(), NoopReranker)


def test_factory_builds_tei_when_enabled(monkeypatch):
    from shared.utils import get_settings

    monkeypatch.setenv("RERANKER_ENABLED", "true")
    get_settings.cache_clear()

    assert isinstance(RerankerFactory(get_settings()).create(), TEIRerankerProvider)
