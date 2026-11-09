"""bge-reranker-v2-m3 (or any cross-encoder) behind Hugging Face TEI.

text-embeddings-inference runs the model in its own container (the `reranker`
service in Docker/docker-compose.yml) and serves ``POST /rerank``. That keeps
torch and the model weights out of the application image: the app sends text
over HTTP and gets scores back.

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
