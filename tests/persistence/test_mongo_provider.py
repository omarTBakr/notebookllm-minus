"""MongoProvider.connect, without a MongoDB.

The DSN stopped being a setting and became the `mongo_uri` property built from
MONGO_HOST/PORT/USER/PASSWORD. The provider kept reading `MONGO_URI`, which no
longer exists, so every connect raised DbConnectionError. Nothing caught it
because the suite runs on fakes and the default backend is Postgres.
"""

import data.repositories.mongo.provider as provider_module
from data.repositories.mongo.provider import MongoProvider
from shared.utils import get_settings


class _Admin:
    async def command(self, name):
        return {"ok": 1}


class _Client:
    """Stands in for AsyncIOMotorClient: records the URI it was built with."""

    built_with: list[str] = []

    def __init__(self, uri):
        type(self).built_with.append(uri)
        self.admin = _Admin()

    def __getitem__(self, name):
        return f"db:{name}"

    def close(self):
        pass


class _Vectors:
    async def connect(self):
        pass

    async def disconnect(self):
        pass


async def test_connect_uses_the_uri_built_from_the_mongo_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "MONGO_USER", "a user")
    monkeypatch.setattr(settings, "MONGO_PASSWORD", "p@ss")
    monkeypatch.setattr(settings, "MONGO_HOST", "mongo.internal")
    monkeypatch.setattr(settings, "MONGO_PORT", 27018)
    monkeypatch.setattr(provider_module, "QdrantVectorRepository", lambda **kwargs: _Vectors())
    monkeypatch.setattr(provider_module, "AsyncIOMotorClient", _Client)
    _Client.built_with = []

    provider = MongoProvider(settings)
    await provider.connect()

    # Credentials are percent-encoded, so an "@" in a password cannot end the
    # userinfo part early.
    assert _Client.built_with == ["mongodb://a+user:p%40ss@mongo.internal:27018"]
    assert provider.db == f"db:{settings.MONGO_DB_NAME}"

    await provider.disconnect()
    assert provider.client is None
