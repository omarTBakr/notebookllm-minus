from shared.exceptions import UnsupportedProviderError
from shared.utils import Settings

from .LlamaCppRerankerProvider import LlamaCppRerankerProvider
from .RerankerInterface import NoopReranker, RerankerInterface
from .TEIRerankerProvider import TEIRerankerProvider


class RerankerFactory:
    """Builds the reranker RERANKER_PROVIDER names, or the pass-through."""

    # llamacpp: llama-server with a GGUF build (the compose default, ~1 GB RAM).
    # tei: Hugging Face text-embeddings-inference, if you run one yourself.
    _PROVIDERS = {"llamacpp": LlamaCppRerankerProvider, "tei": TEIRerankerProvider}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self) -> RerankerInterface:
        if not self.settings.RERANKER_ENABLED:
            return NoopReranker()

        provider = self.settings.RERANKER_PROVIDER.strip().lower()

        cls = self._PROVIDERS.get(provider)
        if cls is not None:
            return cls(
                base_url=self.settings.RERANKER_URL,
                model_id=self.settings.RERANKER_MODEL_ID,
                timeout=self.settings.RERANKER_TIMEOUT_S,
            )

        raise UnsupportedProviderError(f"Unknown reranker provider {provider!r}. Supported: {sorted(self._PROVIDERS)}")
