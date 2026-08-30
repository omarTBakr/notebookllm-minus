from enums import LLMEmbeddingProvider
from exceptions import UnsupportedProviderError
from utils import Settings, get_logger

from .CohereEmbeddingProvider import CohereEmbeddingProvider
from .GoogleEmbeddingProvider import GoogleEmbeddingProvider
from .LLMEmbeddingInterface import LLMEmbeddingInterface
from .OllamaEmbeddingProvider import OllamaEmbeddingProvider
from .OpenAIEmbeddingProvider import OpenAIEmbeddingProvider


class LLMEmbeddingFactory:
    """Builds the configured embedding provider from ``Settings``.

    Anthropic is absent by design — it has no embeddings API — so
    GENERATION_BACKEND and EMBEDDING_BACKEND are validated against different
    sets and need not agree.
    """

    # Ollama is absent on purpose: it runs locally and authenticates by host.
    _API_KEY_FIELDS = {
        LLMEmbeddingProvider.OPENAI: "OPENAI_API_KEY",
        LLMEmbeddingProvider.GOOGLE: "GOOGLE_API_KEY",
        LLMEmbeddingProvider.COHERE: "COHERE_API_KEY",
    }

    _PROVIDERS = {
        LLMEmbeddingProvider.OPENAI: OpenAIEmbeddingProvider,
        LLMEmbeddingProvider.GOOGLE: GoogleEmbeddingProvider,
        LLMEmbeddingProvider.COHERE: CohereEmbeddingProvider,
        LLMEmbeddingProvider.OLLAMA: OllamaEmbeddingProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(type(self).__module__)

    def create(
        self, provider: LLMEmbeddingProvider | str | None = None
    ) -> LLMEmbeddingInterface:
        """Build the provider named by *provider*, or by EMBEDDING_BACKEND."""
        name = provider or self.settings.EMBEDDING_BACKEND

        try:
            # See LLMChattingFactory: str() on an enum member gives its repr,
            # not its value, so unwrap before normalizing.
            chosen = LLMEmbeddingProvider(str(getattr(name, "value", name)).strip().lower())
        except ValueError as exc:
            raise UnsupportedProviderError(
                f"Unknown embedding provider {name!r}. Supported: "
                f"{[p.value for p in LLMEmbeddingProvider]} "
                "(Anthropic has no embeddings API)"
            ) from exc

        kwargs = {
            "model_id": self.settings.EMBEDDING_MODEL_ID,
            "embedding_size": self.settings.EMBEDDING_MODEL_SIZE,
        }

        if chosen is LLMEmbeddingProvider.OLLAMA:
            # Local: a host to reach, no key to check.
            kwargs["base_url"] = self.settings.ollama_base_url
        else:
            api_key = getattr(self.settings, self._API_KEY_FIELDS[chosen])
            if not api_key:
                raise UnsupportedProviderError(
                    f"{self._API_KEY_FIELDS[chosen]} is not set, so the "
                    f"{chosen.value!r} embedding provider cannot be built"
                )
            kwargs["api_key"] = api_key
            if chosen is LLMEmbeddingProvider.OPENAI and self.settings.OPENAI_API_BASE_URL:
                kwargs["base_url"] = self.settings.OPENAI_API_BASE_URL

        self.logger.info(
            "Building embedding provider %r (model=%r, size=%d)",
            chosen.value,
            self.settings.EMBEDDING_MODEL_ID,
            self.settings.EMBEDDING_MODEL_SIZE,
        )
        return self._PROVIDERS[chosen](**kwargs)
