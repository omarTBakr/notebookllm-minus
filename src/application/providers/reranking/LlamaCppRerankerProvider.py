"""bge-reranker-v2-m3 as a quantized GGUF, behind llama.cpp's llama-server.

The default reranker backend. ``llama-server --reranking`` (the `reranker`
service in Docker/docker-compose.yml) serves the same cross-encoder TEI did,
but quantized to Q8_0: a ~640 MB file and about 1 GB of RAM, where TEI's CPU
backend needed 6-19 GB to load the fp32 weights and starved the host.

The endpoint is Jina-style: ``POST /v1/rerank`` with a query and documents,
answering ``{"results": [{"index", "relevance_score"}]}``. llama-server returns
the model's raw logit as the score (``embd[0]`` in tools/server), so it is put
through a sigmoid here: RERANK_MIN_SCORE then means the same 0..1 relevance
whichever backend is running.
"""

import math

import httpx

from shared.exceptions import LLMProviderError

from .RerankerInterface import RerankerInterface


def _sigmoid(x: float) -> float:
    # Split by sign so a large negative logit cannot overflow exp().
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class LlamaCppRerankerProvider(RerankerInterface):
    def __init__(self, base_url: str, model_id: str, timeout: float = 10.0) -> None:
        super().__init__(model_id=model_id)
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def _rerank(self, query: str, texts: list[str]) -> list[tuple[int, float]]:
        try:
            response = await self.client.post(
                "/v1/rerank",
                json={
                    # Informational only: llama-server serves the one model it
                    # was started with.
                    "model": self.model_id,
                    "query": query,
                    "documents": texts,
                    "top_n": len(texts),
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Reranker request failed: {exc}") from exc

        try:
            return [
                (int(item["index"]), _sigmoid(float(item["relevance_score"]))) for item in response.json()["results"]
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMProviderError(f"Reranker returned an unexpected body: {response.text[:200]}") from exc

    async def aclose(self) -> None:
        await self.client.aclose()
