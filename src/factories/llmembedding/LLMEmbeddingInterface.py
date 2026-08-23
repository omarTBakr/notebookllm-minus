"""The contract every embedding backend implements.

Batch-first on purpose: processing an asset embeds every chunk of it, and a
one-text-at-a-time interface would turn that into N round trips.
"""

from abc import ABC, abstractmethod
from time import perf_counter

from enums import EmbeddingInputType
from exceptions import EmbeddingError
from utils import get_logger


class LLMEmbeddingInterface(ABC):
    """Base for embedding providers.

    Shaped like ``BaseController``: the constructor holds what every provider
    needs and hands down a logger named after the subclass's own module.
    """

    def __init__(
        self, model_id: str, embedding_size: int, api_key: str | None = None
    ) -> None:

        # api_key is optional because a locally hosted backend (Ollama) has no
        # key to give; the factory decides which providers require one.
        self.api_key = api_key

        self.model_id = model_id

        self.embedding_size = embedding_size

        # e.g. "factories.llmembedding.OpenAIEmbeddingProvider"
        self.logger = get_logger(type(self).__module__)

    async def embed(
        self,
        texts: list[str],
        input_type: EmbeddingInputType = EmbeddingInputType.DOCUMENT,
    ) -> list[list[float]]:
        """Embed *texts*, returning one vector per input **in input order**.

        *input_type* says whether these are stored documents or a live query;
        asymmetric models embed the two differently.

        Raises ``EmbeddingError`` on failure or on a count mismatch.

        Concrete on purpose: the empty-input short circuit, the logging and the
        result validation are identical for every backend, so they live here
        and :meth:`_embed` is the only thing a provider writes.
        """
        resolved_type = EmbeddingInputType(input_type)

        if not texts:
            # Every SDK rejects an empty batch, and the callers that hand us one
            # are doing nothing wrong — an asset can chunk to nothing.
            self.logger.debug(
                "embed() called with no texts; skipping the %s call", type(self).__name__
            )
            return []

        # Counts and sizes only — never the chunk text itself.
        self.logger.debug(
            "Embedding %d texts (model=%s, input_type=%s)",
            len(texts),
            self.model_id,
            resolved_type.value,
            extra={"provider": type(self).__name__, "model_id": self.model_id},
        )

        started = perf_counter()

        # No try/except: providers raise EmbeddingError and stay quiet so the
        # handler in main.py is the single record of the failure.
        vectors = self._validate(texts, await self._embed(texts, resolved_type))

        elapsed_ms = (perf_counter() - started) * 1000

        self.logger.info(
            "Embedded %d texts into %d-dim vectors in %.0f ms (provider=%s, model=%s)",
            len(vectors),
            self.embedding_size,
            elapsed_ms,
            type(self).__name__,
            self.model_id,
            extra={
                "provider": type(self).__name__,
                "model_id": self.model_id,
                "duration_ms": round(elapsed_ms, 1),
                "text_count": len(vectors),
                "embedding_size": self.embedding_size,
            },
        )

        return vectors

    @abstractmethod
    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:
        """Call the vendor. *texts* is never empty and *input_type* is resolved.

        Return the raw vectors; the caller validates count and width.
        """

    async def aclose(self) -> None:
        """Release the client's connection pool.

        The default suits every SDK whose client exposes ``close()`` — which
        is most of them. Google and Cohere name it differently and override.
        """
        await self.client.close()

    # --- helper shared by every provider -------------------------------------

    def _validate(self, texts: list[str], vectors: list[list[float]]) -> list[list[float]]:
        """Guard the two failures that would otherwise surface far from here.

        Count: vectors are zipped against chunk ids by position downstream. If
        a provider drops or reorders one, every later chunk gets the wrong
        vector and retrieval degrades with nothing in the logs to explain it.

        Width: the vector store bakes EMBEDDING_MODEL_SIZE into the collection
        at creation, so a model that returns a different width makes every
        insert fail with an error that names the collection rather than the
        misconfigured setting. Providers that can request a width do; Ollama
        cannot, which is why checking here is worth the two comparisons.
        """
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"{type(self).__name__} returned {len(vectors)} vectors for "
                f"{len(texts)} inputs (model={self.model_id!r})"
            )

        if vectors and len(vectors[0]) != self.embedding_size:
            raise EmbeddingError(
                f"{type(self).__name__} returned {len(vectors[0])}-dimensional "
                f"vectors but EMBEDDING_MODEL_SIZE is {self.embedding_size} "
                f"(model={self.model_id!r}) — set EMBEDDING_MODEL_SIZE to match the model"
            )

        return vectors
