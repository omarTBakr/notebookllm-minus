import cohere  # ty: ignore[unresolved-import]

from enums import EmbeddingInputType
from exceptions import EmbeddingError

from ..cohere_support import aclose_cohere_client
from .LLMEmbeddingInterface import LLMEmbeddingInterface


class CohereEmbeddingProvider(LLMEmbeddingInterface):
    """Embeddings via Cohere's v2 Embed API."""

    # Cohere requires input_type on every call — there is no neutral default.
    _INPUT_TYPES = {
        EmbeddingInputType.DOCUMENT: "search_document",
        EmbeddingInputType.QUERY: "search_query",
    }

    def __init__(self, api_key: str, model_id: str, embedding_size: int) -> None:

        super().__init__(api_key=api_key, model_id=model_id, embedding_size=embedding_size)

        self.client = cohere.AsyncClientV2(api_key=api_key)

    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:

        try:
            response = await self.client.embed(
                model=self.model_id,
                texts=texts,
                input_type=self._INPUT_TYPES[input_type],
                output_dimension=self.embedding_size,
                # v2 returns a container keyed by type; ask for floats only so
                # there is exactly one list to read back.
                embedding_types=["float"],
            )

        except Exception as exc:
            raise EmbeddingError(f"Cohere embedding failed: {exc}") from exc

        # "float_" and not "float": the field is renamed to dodge the keyword.
        vectors = response.embeddings.float_ or []

        return list(vectors)

    async def aclose(self) -> None:

        await aclose_cohere_client(self.client)
