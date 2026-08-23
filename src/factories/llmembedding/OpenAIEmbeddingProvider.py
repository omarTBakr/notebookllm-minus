from openai import AsyncOpenAI  # ty: ignore[unresolved-import]

from enums import EmbeddingInputType
from exceptions import EmbeddingError

from .LLMEmbeddingInterface import LLMEmbeddingInterface


class OpenAIEmbeddingProvider(LLMEmbeddingInterface):
    """Embeddings via OpenAI.

    OpenAI's models are symmetric — documents and queries embed the same way —
    so ``input_type`` is accepted for interface parity and ignored.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        embedding_size: int,
        base_url: str | None = None,
    ) -> None:

        super().__init__(api_key=api_key, model_id=model_id, embedding_size=embedding_size)

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:

        try:
            response = await self.client.embeddings.create(
                model=self.model_id,
                input=texts,
                # Honours EMBEDDING_MODEL_SIZE on the text-embedding-3-* family,
                # which supports shortening the vector at request time.
                dimensions=self.embedding_size,
            )

        except Exception as exc:
            raise EmbeddingError(f"OpenAI embedding failed: {exc}") from exc

        # Each item carries its own index; sort by it rather than trusting the
        # response to arrive in request order.
        ordered = sorted(response.data, key=lambda item: item.index)

        return [item.embedding for item in ordered]
