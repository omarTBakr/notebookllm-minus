from enums import DistanceMethod, VectorDBProvider
from exceptions import UnsupportedProviderError
from utils import Settings, get_logger

from .QdrantProvider import QdrantProvider
from .VectorDBInterface import VectorDBInterface


class VectorDBFactory:
    """Builds the configured vector store from ``Settings``."""

    _PROVIDERS = {
        VectorDBProvider.QDRANT: QdrantProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(type(self).__module__)

    def create(self, provider: VectorDBProvider | str | None = None) -> VectorDBInterface:
        """Build the store named by *provider*, or by VECTOR_DB_BACKEND.

        Returns a provider that is configured but **not connected** — call
        ``await store.connect()``, which the app's lifespan does at startup.
        """
        name = provider or self.settings.VECTOR_DB_BACKEND

        try:
            # See LLMChattingFactory: str() on an enum member gives its repr,
            # not its value, so unwrap before normalizing.
            chosen = VectorDBProvider(str(getattr(name, "value", name)).strip().lower())
        except ValueError as exc:
            raise UnsupportedProviderError(
                f"Unknown vector database provider {name!r}. Supported: "
                f"{[p.value for p in VectorDBProvider]}"
            ) from exc

        url = self.settings.VECTOR_DB_URL
        # url and path are mutually exclusive, so a configured URL wins and the
        # embedded path is left unset rather than passed alongside it.
        kwargs = {
            "url": url,
            "api_key": self.settings.VECTOR_DB_API_KEY if url else None,
            "path": None if url else self.settings.vector_db_path,
            "distance_method": DistanceMethod(self.settings.VECTOR_DB_DISTANCE_METHOD),
        }

        self.logger.info(
            "Building vector database %r (%s)",
            chosen.value,
            f"server={url}" if url else f"embedded path={self.settings.vector_db_path}",
        )
        return self._PROVIDERS[chosen](**kwargs)
