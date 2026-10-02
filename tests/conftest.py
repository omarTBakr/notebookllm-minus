"""Test-wide setup.

Two things make this app awkward to test, and both are handled here.

First, settings are read at *import* time: ``main.py`` and ``presentation/routes/data.py``
each bind a ``Settings`` object at module scope, and ``get_settings`` is
lru_cached. So the environment has to be right before anything under ``src``
is imported for the first time — which is why the env block below runs at
module level, not in a fixture.

Second, several caches and singletons outlive a test. Each has an autouse
fixture that puts it back.

Nothing here talks to Mongo, Postgres, Qdrant or Ollama. The fakes in
``tests/support`` stand in for all four.
"""

import os

# Flip to True while debugging a 500 to see the real traceback.
RAISE_APP_EXCEPTIONS = os.environ.get("TEST_RAISE") == "1"

# --- environment, before the first `src` import -------------------------------
#
# Settings has required fields with no defaults and reads src/.env, which is
# gitignored and absent on a clean clone. Real environment variables win over
# the file in pydantic-settings, so setting them here makes the suite behave
# the same on any machine.
_ENV = {
    "APPLICATION_NAME": "notebookllm-minus-test",
    "APP_VERSION": "0.0.0-test",
    "ALLOWED_TYPES": '["application/pdf", "text/plain", "text/markdown", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]',
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
    # Unreachable for the same reason as OLLAMA_HOST above, and it was missing:
    # CELERY_HOST defaults to localhost:5672, so on a developer's machine the
    # suite quietly published to their *running* RabbitMQ and passed, while CI
    # — which has no broker — failed every ingestion test with a 500. Tests must
    # not need a broker; anything that reaches for one is faking too little.
    "CELERY_HOST": "rabbit.invalid",
    "CELERY_BACKEND_HOST": "redis.invalid",
    # Off for the same reason OLLAMA_HOST is unreachable: the correction pass
    # dials a local model, and a test that reaches one is a test that faked too
    # little. It stays *called* in the drained pipeline (see fakes/ingest.py) so
    # the stage cannot break unnoticed -- it just returns the batch untouched.
    # The tests that exercise correction itself hand it a fake client and turn
    # this back on for their own settings copy.
    "POSTPROCESS_ENABLED": "false",
    # Keep the logging config from installing a rotating file handler that
    # would fight caplog and write into the repo.
    "LOG_TO_FILE": "false",
    "LOG_TO_CONSOLE": "false",
    "LOG_LEVEL": "CRITICAL",
}
os.environ.update(_ENV)

import pytest  # noqa: E402

from shared.utils import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Any test that changes env must not leak the Settings it built."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """ModelService caches embed probes on the *class*, so they persist."""
    from application.services import ModelService

    ModelService.forget_probes()
    yield
    ModelService.forget_probes()


@pytest.fixture
def settings():
    return get_settings()


# --- the app, with every external dependency faked ----------------------------


@pytest.fixture
def fake_db():
    from tests.support.db import FakeDb

    return FakeDb()


@pytest.fixture
def fake_providers():
    from tests.support.llm import FakeProviderCache

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

    test_app = main.create_app(lifespan_enabled=False)
    test_app.db = fake_db
    test_app.providers = fake_providers
    test_app.generation_client = fake_providers.chatting()
    test_app.embedding_client = fake_providers.embedding()

    yield test_app


@pytest.fixture
def ingest(client, app, monkeypatch):
    """POST a document and run the ingestion it queues.

    Attaching now returns 202 with the work queued, so a test that wants to
    assert on chunks or vectors has to run that work. Tests that only care
    about the upload itself keep using `client` directly.
    """
    from types import SimpleNamespace

    import application.tasks.tracking.status as task_status
    import application.tasks.workflows as workflows
    from tests.support.ingest import drain_ingestion

    def fake_signature(project_id, request_data, queued):
        """Stand in for the real publish. Nothing here touches a broker.

        `QueuedChain` still generates the three ids and AssetIngestService
        still writes a row per stage from them, so what the test exercises is
        the real bookkeeping path rather than a shortcut around it.
        drain_ingestion then performs the work those rows describe.

        Patched on `tasks.workflows` rather than on the caller's module: the
        service imports these late, inside the method, to keep services
        and tasks from importing each other at module scope. A late import
        reads the name out of `workflows` when it runs, so this is the binding
        it actually gets.

        Flat, not a three-deep `.parent` chain as it used to be: ingestion is
        no longer a Celery chain, and the ids come from QueuedChain rather than
        from walking a published one.
        """
        return SimpleNamespace(apply_async=lambda *a, **k: None)

    async def _ingest(chat_id, files):
        monkeypatch.setattr(workflows, "ingestion_signature", fake_signature)
        # Writes a marker into the result backend, which is equally absent.
        monkeypatch.setattr(task_status, "mark_queued", lambda task_id: None)

        response = await client.post(f"/chat/chats/{chat_id}/documents", files=files)

        if response.status_code < 400:
            await drain_ingestion(app)

        return response

    return _ingest


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
def make_seed(fake_db):
    """Create an isolated user, chat, project and optional text asset."""
    from data.models import Asset, Chat, Project, Session, User
    from shared.enums import AssetType

    def seed(
        *,
        user_id="u1",
        session_id="s1",
        chat_id="c1",
        asset_id="a1",
        title="A notebook",
        include_asset=True,
    ):
        fake_db.users().items[user_id] = User(user_id=user_id, label="Omar")
        fake_db.sessions().items[session_id] = Session(session_id=session_id, user_id=user_id)
        fake_db.chats().items[chat_id] = Chat(
            chat_id=chat_id,
            session_id=session_id,
            user_id=user_id,
            title=title,
        )
        fake_db.projects().items[chat_id] = Project(project_id=chat_id, name=title)

        if include_asset:
            fake_db.assets().items[asset_id] = Asset(
                asset_id=asset_id,
                asset_type=AssetType.TEXT,
                project_id=chat_id,
                name="note1.txt",
                file_bytes=b"the note body",
            )

        return {
            "user": user_id,
            "session": session_id,
            "chat": chat_id,
            "asset": asset_id if include_asset else None,
        }

    return seed


@pytest.fixture
def seed(make_seed):
    """Backward-compatible default seed for existing tests."""
    return make_seed()
