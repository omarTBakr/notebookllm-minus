from google import genai  # ty: ignore[unresolved-import]
from google.genai import types  # ty: ignore[unresolved-import]

from enums import EmbeddingInputType, EMBEDDING_INPUT_TYPE_TO_GOOGLE
from exceptions import EmbeddingError

from .LLMEmbeddingInterface import LLMEmbeddingInterface


class GoogleEmbeddingProvider(LLMEmbeddingInterface):
    """Embeddings via Gemini, through the unified ``google-genai`` SDK."""

    def __init__(self, api_key: str, model_id: str, embedding_size: int) -> None:

        super().__init__(api_key=api_key, model_id=model_id, embedding_size=embedding_size)

        self.client = genai.Client(api_key=api_key)

    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:

        try:
            response = await self.client.aio.models.embed_content(
                model=self.model_id,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=EMBEDDING_INPUT_TYPE_TO_GOOGLE[input_type],
                    output_dimensionality=self.embedding_size,
                ),
            )

        except Exception as exc:
            raise EmbeddingError(f"Google embedding failed: {exc}") from exc

        embeddings = response.embeddings or []

        return [embedding.values for embedding in embeddings]

    async def aclose(self) -> None:

        await self.client.aio.aclose()
