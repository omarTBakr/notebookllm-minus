from enums import (
    LLMEmbeddingProvider,
    EMBEDDING_PROVIDER_API_KEY_FIELDS,
    EMBEDDING_PROVIDER_SETTING_KWARGS,
)
from exceptions import UnsupportedProviderError
from utils import Settings, get_logger

from ..setting_kwargs import setting_kwargs

from .CohereEmbeddingProvider import CohereEmbeddingProvider
from .GoogleEmbeddingProvider import GoogleEmbeddingProvider
from .LLMEmbeddingInterface import LLMEmbeddingInterface
from .NvidiaEmbeddingProvider import NvidiaEmbeddingProvider
from .OllamaEmbeddingProvider import OllamaEmbeddingProvider
from .OpenAIEmbeddingProvider import OpenAIEmbeddingProvider


class LLMEmbeddingFactory:
    """Builds the configured embedding provider from ``Settings``.

    Anthropic is absent by design — it has no embeddings API — so
    GENERATION_BACKEND and EMBEDDING_BACKEND are validated against different
    sets and need not agree.
    """

    _PROVIDERS = {
        LLMEmbeddingProvider.OPENAI: OpenAIEmbeddingProvider,
        LLMEmbeddingProvider.GOOGLE: GoogleEmbeddingProvider,
        LLMEmbeddingProvider.COHERE: CohereEmbeddingProvider,
        LLMEmbeddingProvider.NVIDIA: NvidiaEmbeddingProvider,
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
            api_key = getattr(self.settings, EMBEDDING_PROVIDER_API_KEY_FIELDS[chosen])
            if not api_key:
                raise UnsupportedProviderError(
                    f"{EMBEDDING_PROVIDER_API_KEY_FIELDS[chosen]} is not set, so the "
                    f"{chosen.value!r} embedding provider cannot be built"
                )
            kwargs["api_key"] = api_key

        # The endpoint, and anything else a provider needs from .env, come
        # from one table rather than a branch per vendor.
        kwargs.update(
            setting_kwargs(self.settings, EMBEDDING_PROVIDER_SETTING_KWARGS.get(chosen, {}))
        )

        self.logger.info(
            "Building embedding provider %r (model=%r, size=%d)",
            chosen.value,
            self.settings.EMBEDDING_MODEL_ID,
            self.settings.EMBEDDING_MODEL_SIZE,
        )
        return self._PROVIDERS[chosen](**kwargs)
