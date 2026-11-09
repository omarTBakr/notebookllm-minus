"""A cross-encoder behind Hugging Face text-embeddings-inference (TEI).

RERANKER_PROVIDER=tei. Not what compose runs: TEI's CPU backend needed 6-19 GB
to load bge-reranker-v2-m3, so the `reranker` service is llama.cpp instead
(LlamaCppRerankerProvider). Kept for a GPU box with TEI already running, where
its ``POST /rerank`` serves the same model well.

bge-reranker-v2-m3 is multilingual, which matters here: most of this corpus is
Arabic, and an English-only cross-encoder would score every Arabic passage as
noise.
"""

import httpx

from shared.exceptions import LLMProviderError

from .RerankerInterface import RerankerInterface


class TEIRerankerProvider(RerankerInterface):
    def __init__(self, base_url: str, model_id: str, timeout: float = 10.0) -> None:
        super().__init__(model_id=model_id)
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def _rerank(self, query: str, texts: list[str]) -> list[tuple[int, float]]:
        try:
            response = await self.client.post(
                "/rerank",
                json={
                    "query": query,
                    "texts": texts,
                    # Sigmoid-normalised 0..1, so RERANK_MIN_SCORE means the
                    # same thing whatever the model's raw logit range is.
                    "raw_scores": False,
                    # A long chunk is cut to the model's window instead of
                    # failing the whole request with a 413.
                    "truncate": True,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Reranker request failed: {exc}") from exc

        try:
            return [(int(item["index"]), float(item["score"])) for item in response.json()]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMProviderError(f"Reranker returned an unexpected body: {response.text[:200]}") from exc

    async def aclose(self) -> None:
        await self.client.aclose()
