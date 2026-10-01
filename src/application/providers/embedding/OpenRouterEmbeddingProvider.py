from shared.enums import EmbeddingInputType
from shared.exceptions import EmbeddingError

from .OpenAIEmbeddingProvider import OpenAIEmbeddingProvider


class OpenRouterEmbeddingProvider(OpenAIEmbeddingProvider):
    """Embeddings via OpenRouter's OpenAI-compatible endpoint.

    ``dimensions`` is deliberately *not* sent, for the reason
    :class:`NvidiaEmbeddingProvider` gives: the models behind OpenRouter do not
    all accept it, and a vendor 400 names nothing this application can act on.
    Leaving the interface's own ``_validate`` to compare the width that came
    back against EMBEDDING_MODEL_SIZE produces the message that names the
    setting.
    """

    _VENDOR = "OpenRouter"

    async def _embed(self, texts: list[str], input_type: EmbeddingInputType) -> list[list[float]]:
        try:
            response = await self.client.embeddings.create(model=self.model_id, input=texts)

        except Exception as exc:
            raise EmbeddingError(f"{self._VENDOR} embedding failed: {exc}") from exc

        ordered = sorted(response.data, key=lambda item: item.index)

        return [item.embedding for item in ordered]
