from enums import DbBackend
from exceptions import UnsupportedProviderError
from utils.config import Settings

from .interfaces.provider import DbProvider


class DbFactory:
    """Creates the appropriate DbProvider based on DOCUMENT_DB_BACKEND in settings."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self) -> DbProvider:
        # Settings already validated this against DbBackend and stored the
        # enum's value, so the lookup cannot miss for a configured app. The
        # try/except covers a Settings built by hand, in a test or a script.
        try:
            backend = DbBackend(self.settings.DOCUMENT_DB_BACKEND or DbBackend.MONGO)
        except ValueError as exc:
            raise UnsupportedProviderError(
                f"Unknown document db backend: {self.settings.DOCUMENT_DB_BACKEND!r}. "
                f"Supported: {[b.value for b in DbBackend]}"
            ) from exc

        # Imported inside the branch so a Mongo deployment never imports
        # sqlalchemy, and a Postgres one never imports motor.
        if backend is DbBackend.MONGO:
            from .mongo.provider import MongoProvider
            return MongoProvider(self.settings)

        from .postgres.provider import PostgresProvider
        return PostgresProvider(self.settings)
