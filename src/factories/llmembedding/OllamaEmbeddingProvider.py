from ollama import AsyncClient  # ty: ignore[unresolved-import]

from enums import EmbeddingInputType
from exceptions import EmbeddingError

from .LLMEmbeddingInterface import LLMEmbeddingInterface


class OllamaEmbeddingProvider(LLMEmbeddingInterface):
    """Embeddings from a local Ollama server.

    Two consequences of running locally: there is no API key, and the vector
    width is whatever the pulled model produces. Most Ollama embedding models
    have one fixed size and quietly ignore a requested dimension, so this
    provider does not ask for one — it lets the inherited check compare the
    result against EMBEDDING_MODEL_SIZE and fail with an actionable message.

    ``input_type`` is accepted for interface parity and ignored: the API has no
    document/query distinction to pass it to.
    """

    def __init__(self, model_id: str, embedding_size: int, base_url: str, **kwargs) -> None:

        super().__init__(model_id=model_id, embedding_size=embedding_size, **kwargs)

        self.base_url = base_url

        self.client = AsyncClient(host=base_url)

    async def _embed(
        self, texts: list[str], input_type: EmbeddingInputType
    ) -> list[list[float]]:

        try:
            response = await self.client.embed(model=self.model_id, input=texts)

        except Exception as exc:
            raise EmbeddingError(
                f"Ollama embedding failed at {self.base_url}: {exc} "
                "(is `ollama serve` running, and has the model been pulled?)"
            ) from exc

        return [list(vector) for vector in response.embeddings]
