from exceptions import UnsupportedProviderError
from utils.config import Settings

from .interfaces.provider import DbProvider


class DbFactory:
    """Creates the appropriate DbProvider based on DOCUMENT_DB_BACKEND in settings."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self) -> DbProvider:
        backend = (self.settings.DOCUMENT_DB_BACKEND or "mongo").lower()

        if backend == "mongo":
            from .mongo.provider import MongoProvider
            return MongoProvider(self.settings)

        if backend == "postgres":
            from .postgres.provider import PostgresProvider
            return PostgresProvider(self.settings)

        raise UnsupportedProviderError(f"Unknown document db backend: {backend!r}")
