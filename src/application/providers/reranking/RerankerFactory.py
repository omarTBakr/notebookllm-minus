from shared.exceptions import UnsupportedProviderError
from shared.utils import Settings

from .RerankerInterface import NoopReranker, RerankerInterface
from .TEIRerankerProvider import TEIRerankerProvider


class RerankerFactory:
    """Builds the reranker RERANKER_PROVIDER names, or the pass-through."""

    _PROVIDERS = {"tei"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self) -> RerankerInterface:
        if not self.settings.RERANKER_ENABLED:
            return NoopReranker()

        provider = self.settings.RERANKER_PROVIDER.strip().lower()

        if provider == "tei":
            return TEIRerankerProvider(
                base_url=self.settings.RERANKER_URL,
                model_id=self.settings.RERANKER_MODEL_ID,
                timeout=self.settings.RERANKER_TIMEOUT_S,
            )

        raise UnsupportedProviderError(f"Unknown reranker provider {provider!r}. Supported: {sorted(self._PROVIDERS)}")
