"""Test-wide setup.

Two things make this app awkward to test, and both are handled here.

First, settings are read at *import* time: ``main.py`` and ``routes/data.py``
each bind a ``Settings`` object at module scope, and ``get_settings`` is
lru_cached. So the environment has to be right before anything under ``src``
is imported for the first time — which is why the env block below runs at
module level, not in a fixture.

Second, several caches and singletons outlive a test. Each has an autouse
fixture that puts it back.

Nothing here talks to Mongo, Postgres, Qdrant or Ollama. The fakes in
``test/fakes`` stand in for all four.
"""

import os

# Flip to True while debugging a 500 to see the real traceback.
RAISE_APP_EXCEPTIONS = os.environ.get('TEST_RAISE') == '1'

# --- environment, before the first `src` import -------------------------------
#
# Settings has required fields with no defaults and reads src/.env, which is
# gitignored and absent on a clean clone. Real environment variables win over
# the file in pydantic-settings, so setting them here makes the suite behave
# the same on any machine.
_ENV = {
    "APPLICATION_NAME": "notebookllm-minus-test",
    "APP_VERSION": "0.0.0-test",
    "ALLOWED_TYPES": '["application/pdf", "text/plain", "text/markdown"]',
    "MAX_FILE_CHUNK_SIZE": "512000",
    "MAX_FILE_SIZE": "10485760",
    "GENERATION_BACKEND": "ollama",
    "EMBEDDING_BACKEND": "ollama",
    "GENERATION_MODEL_ID": "test-chat",
    "EMBEDDING_MODEL_ID": "test-embed",
    "EMBEDDING_MODEL_SIZE": "8",
    # An unreachable host on purpose: any test that actually dials out is a
    # test that forgot to fake its client.
    "OLLAMA_HOST": "ollama.invalid",
    "OLLAMA_PORT": "11434",
    "OLLAMA_CLOUD_BASE_URL": "http://ollama-cloud.invalid",
    "DOCUMENT_DB_BACKEND": "mongo",
    # Keep the logging config from installing a rotating file handler that
    # would fight caplog and write into the repo.
    "LOG_TO_FILE": "false",
    "LOG_TO_CONSOLE": "false",
    "LOG_LEVEL": "CRITICAL",
}
os.environ.update(_ENV)

import pytest  # noqa: E402

from utils import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Any test that changes env must not leak the Settings it built."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """ModelController caches embed probes on the *class*, so they persist."""
    from controllers import ModelController

    ModelController.forget_probes()
    yield
    ModelController.forget_probes()


@pytest.fixture
def settings():
    return get_settings()


# --- the app, with every external dependency faked ----------------------------


@pytest.fixture
def fake_db():
    from test.fakes.db import FakeDb

    return FakeDb()


@pytest.fixture
def fake_providers():
    from test.fakes.llm import FakeProviderCache

    return FakeProviderCache()


@pytest.fixture
def app(fake_db, fake_providers):
    """The real FastAPI app with fakes bolted on, and no lifespan.

    The lifespan is deliberately never run: it connects to a database, builds
    a vector store and fires a background task that calls Ollama. Everything
    the routes touch is an attribute on the app, so assigning them directly is
    both simpler and faster than faking the world the lifespan builds.
    """
    import main

    saved = {name: getattr(main.app, name, None)
             for name in ("db", "providers", "generation_client", "embedding_client")}

    main.app.db = fake_db
    main.app.providers = fake_providers
    main.app.generation_client = fake_providers.chatting()
    main.app.embedding_client = fake_providers.embedding()

    yield main.app

    # The app is a module-level singleton, so leaving fakes on it would leak
    # into every later test.
    for name, value in saved.items():
        if value is None:
            if hasattr(main.app, name):
                delattr(main.app, name)
        else:
            setattr(main.app, name, value)


@pytest.fixture
async def client(app):
    """An HTTP client speaking straight to the ASGI app.

    raise_app_exceptions=False so the registered handlers turn a domain error
    into a response instead of the exception escaping into the test.
    """
    import httpx

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=RAISE_APP_EXCEPTIONS)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- seed helpers -------------------------------------------------------------


@pytest.fixture
def seed(fake_db):
    """Put a user, session, chat and asset in the fake db and hand back the ids."""
    from models.db_schema import Asset, Chat, Project, Session, User
    from enums import AssetType

    fake_db.users().items["u1"] = User(user_id="u1", label="Omar")
    fake_db.sessions().items["s1"] = Session(session_id="s1", user_id="u1")
    fake_db.chats().items["c1"] = Chat(chat_id="c1", session_id="s1", user_id="u1",
                                       title="A notebook")
    fake_db.projects().items["c1"] = Project(project_id="c1", name="A notebook")
    fake_db.assets().items["a1"] = Asset(
        asset_id="a1",
        asset_type=AssetType.TEXT,
        project_id="c1",
        name="note1.txt",
        file_bytes=b"the note body",
    )
    return {"user": "u1", "session": "s1", "chat": "c1", "asset": "a1"}
