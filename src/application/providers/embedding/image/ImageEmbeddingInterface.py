"""The contract for a multimodal embedder: pictures and text in one space.

Separate from LLMEmbeddingInterface rather than a method added to it,
because almost no text embedder can embed a picture and the chat-level text
embedder stays a free per-notebook choice. This one is global
(IMAGE_EMBEDDING_MODEL_ID) and fills the ``project_<id>__img`` collections.

The one property that makes the feature work: :meth:`embed_texts` and
:meth:`embed_images` come from the *same* model, so a typed question lands in
the space the pictures were indexed in and is compared with them directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter

from shared.enums import EmbeddingInputType
from shared.exceptions import EmbeddingError
from shared.utils import get_logger
from shared.utils.metrics import EMBEDDING_REQUESTS, EMBEDDING_SECONDS


class ImageEmbeddingInterface(ABC):
    """Base for multimodal embedding backends."""

    def __init__(self, model_id: str, embedding_size: int, timeout_s: float = 60.0) -> None:
        self.model_id = model_id
        self.embedding_size = embedding_size
        self.timeout_s = timeout_s
        self.logger = get_logger(type(self).__module__)

    async def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """One vector per picture, in input order. *images* are prepared PNG/JPEG bytes."""
        if not images:
            return []
        return await self._measured("images", len(images), self._embed_images(images))

    async def embed_texts(
        self,
        texts: list[str],
        input_type: EmbeddingInputType = EmbeddingInputType.QUERY,
    ) -> list[list[float]]:
        """One vector per text, in the same space as the pictures."""
        if not texts:
            return []
        return await self._measured("texts", len(texts), self._embed_texts(texts, EmbeddingInputType(input_type)))

    async def _measured(self, what: str, count: int, call) -> list[list[float]]:
        provider = type(self).__name__
        started = perf_counter()
        try:
            vectors = self._validate(count, await call)
        except Exception:
            EMBEDDING_REQUESTS.labels(provider, self.model_id, "error").inc()
            raise
        elapsed = perf_counter() - started
        EMBEDDING_REQUESTS.labels(provider, self.model_id, "ok").inc()
        EMBEDDING_SECONDS.labels(provider, self.model_id).observe(elapsed)
        self.logger.info(
            "Embedded %d %s in %.0f ms (provider=%s, model=%s)",
            count,
            what,
            elapsed * 1000,
            provider,
            self.model_id,
        )
        return vectors

    @abstractmethod
    async def _embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Call the backend. *images* is never empty."""

    @abstractmethod
    async def _embed_texts(self, texts: list[str], input_type: EmbeddingInputType) -> list[list[float]]:
        """Call the backend. *texts* is never empty."""

    async def aclose(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.aclose()

    def _validate(self, count: int, vectors: list[list[float]]) -> list[list[float]]:
        if len(vectors) != count:
            raise EmbeddingError(
                f"{type(self).__name__} returned {len(vectors)} vectors for {count} inputs (model={self.model_id!r})"
            )
        if vectors and len(vectors[0]) != self.embedding_size:
            raise EmbeddingError(
                f"{type(self).__name__} returned {len(vectors[0])}-dimensional vectors but "
                f"IMAGE_EMBEDDING_SIZE is {self.embedding_size} (model={self.model_id!r})"
            )
        return vectors
