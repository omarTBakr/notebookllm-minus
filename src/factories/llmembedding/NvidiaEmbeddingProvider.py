from enums import (
    EMBEDDING_INPUT_TYPE_TO_NVIDIA,
    EMBEDDING_TRUNCATE_TO_NVIDIA,
    EmbeddingInputType,
    TruncateMode,
)
from exceptions import EmbeddingError

from .OpenAIEmbeddingProvider import OpenAIEmbeddingProvider


class NvidiaEmbeddingProvider(OpenAIEmbeddingProvider):
    """Embeddings via NVIDIA's hosted NIM endpoints.

    The transport is OpenAI's embeddings API, so this reuses
    :class:`OpenAIEmbeddingProvider`'s client; ``_embed`` is overridden rather
    than inherited because NVIDIA's models differ from OpenAI's in three ways,
    all of which had to be found by asking the endpoint:

    * they are **asymmetric** — ``input_type`` is passage or query, and the
      call succeeds without it while quietly returning a worse vector, so it
      is always sent;
    * a request is **capped** at *max_batch* inputs, below this application's
      default CHUNKING_BATCH_SIZE, so a long batch is split here rather than
      leaving every deployment to discover the cap through a failed upload;
    * the width is **fixed** — ``nemotron-3-embed-1b`` answers
      ``dimensions must be one of 2048`` to anything else — so ``dimensions``
      is deliberately *not* sent. Letting the interface's own ``_validate``
      catch a wrong EMBEDDING_MODEL_SIZE gives the message that names the
      setting, instead of a 400 from the vendor that does not.

    The cap and the truncation mode are NVIDIA's numbers rather than ours, so
    they arrive from Settings (NVIDIA_EMBEDDING_MAX_BATCH,
    NVIDIA_EMBEDDING_TRUNCATE) and the signature defaults here are only what
    a directly-constructed provider falls back to.
    """

    _VENDOR = "NVIDIA"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        embedding_size: int,
        base_url: str | None = None,
        max_batch: int = 256,
        truncate: TruncateMode | str = TruncateMode.END,
    ) -> None:

        super().__init__(
            api_key=api_key,
            model_id=model_id,
            embedding_size=embedding_size,
            base_url=base_url,
        )

        self.max_batch = max_batch

        # Settings stores enum *values* (use_enum_values), so this arrives as
        # a plain string. Resolved once, here, so a bad mode is a construction
        # error rather than a KeyError on the first upload.
        self.truncate = EMBEDDING_TRUNCATE_TO_NVIDIA[TruncateMode(truncate)]

    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:

        vectors: list[list[float]] = []

        for start in range(0, len(texts), self.max_batch):
            vectors.extend(
                await self._embed_batch(texts[start : start + self.max_batch], input_type)
            )

        return vectors

    async def _embed_batch(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:
        """One request, at most ``max_batch`` inputs."""
        try:
            response = await self.client.embeddings.create(
                model=self.model_id,
                input=texts,
                extra_body={
                    "input_type": EMBEDDING_INPUT_TYPE_TO_NVIDIA[input_type],
                    # A chunk longer than the model's context would otherwise
                    # fail the whole batch. The chunker caps chunks near 1000
                    # characters, so the default should never fire — it is
                    # here so that if it ever does, one long chunk costs its
                    # own tail rather than the entire upload.
                    "truncate": self.truncate,
                },
            )

        except Exception as exc:
            raise EmbeddingError(f"{self._VENDOR} embedding failed: {exc}") from exc

        # Same reasoning as the OpenAI provider: each item carries its index,
        # and position in the response is not promised.
        ordered = sorted(response.data, key=lambda item: item.index)

        return [item.embedding for item in ordered]
