"""The two indexing paths that do not go through the ingestion chain.

Everything uploaded through /process or the chat's document route ends with
build_vector_index_task, because the chain puts it there. These two do not use
that chain, and each lost its index when the build was split out of
index_chunks:

* POST /nlp/index/push queued index_project_task bare, with a .delay(),
* PATCH /chat/chats/{id}/models re-indexes synchronously inside the request.

Neither failure is visible at runtime: a collection with no ANN index still
answers every search, correctly, from an exact scan. On this project's own
2048-dim data that is 120-185 ms instead of 0.85-0.98 ms, and nothing reports
it.
"""

from types import SimpleNamespace

import pytest
from bson.objectid import ObjectId


@pytest.fixture
def chunked(fake_db, seed):
    """A chat whose chunks already exist — what both routes below assume."""
    from data.models import DataChunk

    project_oid = fake_db.projects().items["c1"].id

    fake_db.chunks().items.append(
        DataChunk(
            _id=ObjectId(),
            chunk_content="the note body",
            chunk_metadata={},
            chunk_order=0,
            project_id=project_oid,
            asset_id="a1",
        )
    )
    return seed


def _fake_chain(calls, index_id="index-1", build_id="build-1"):
    """Stand in for index_chain, recording how it was built.

    Two deep, and nested the way Celery nests: apply_async names the last task
    and reaches the one before through .parent.
    """
    def build(project_id, asset_id=None, reset=False, batch_size=None):
        calls.append((project_id, asset_id, reset, batch_size))
        return SimpleNamespace(
            apply_async=lambda: SimpleNamespace(
                id=build_id, parent=SimpleNamespace(id=index_id, parent=None)
            )
        )

    return build


# --- POST /nlp/index/push -----------------------------------------------------


async def test_push_queues_the_build_after_the_indexing(client, chunked, monkeypatch):
    """A bare .delay() here would embed every chunk and stop, leaving the
    collection permanently unindexed."""
    import presentation.routes.nlp as nlp_route

    calls = []
    monkeypatch.setattr(nlp_route, "index_chain", _fake_chain(calls))
    monkeypatch.setattr(nlp_route, "mark_queued", lambda task_id: None)

    response = await client.post("/nlp/index/push/c1", json={"asset_id": "a1"})

    assert response.status_code == 202
    body = response.json()
    # task_id is still the *index* task: it is what existing clients poll, and
    # it is the half that takes the time.
    assert body["task_id"] == "index-1"
    assert body["build_index_task_id"] == "build-1"
    assert calls == [("c1", "a1", False, None)]


async def test_push_records_a_row_for_both_links(client, chunked, monkeypatch):
    """Without walking .parent the index half would be unqueryable and report
    UNKNOWN for the whole of its run."""
    import presentation.routes.nlp as nlp_route

    monkeypatch.setattr(nlp_route, "index_chain", _fake_chain([]))
    monkeypatch.setattr(nlp_route, "mark_queued", lambda task_id: None)

    await client.post("/nlp/index/push/c1", json={"asset_id": "a1"})

    rows = client._transport.app.db.tasks().items

    assert {task_id: row.task_name.split(".")[-1] for task_id, row in rows.items()} == {
        "index-1": "index_project_task",
        "build-1": "build_vector_index_task",
    }


async def test_push_passes_its_own_reset_through(client, chunked, monkeypatch):
    """Unlike the ingestion chain, reset here *is* the index's reset: this
    caller is asking for the collection to be dropped and rebuilt."""
    import presentation.routes.nlp as nlp_route

    calls = []
    monkeypatch.setattr(nlp_route, "index_chain", _fake_chain(calls))
    monkeypatch.setattr(nlp_route, "mark_queued", lambda task_id: None)

    await client.post("/nlp/index/push/c1", json={"reset": True, "batch_size": 16})

    assert calls == [("c1", None, True, 16)]


async def test_a_broker_outage_on_push_is_still_a_503(client, chunked, monkeypatch):
    """The chain publishes where a .delay() used to, so the failure boundary
    has to have moved with it."""
    from kombu.exceptions import OperationalError as BrokerOperationalError

    import presentation.routes.nlp as nlp_route

    def refuse():
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(
        nlp_route, "index_chain", lambda *a, **k: SimpleNamespace(apply_async=refuse)
    )

    response = await client.post("/nlp/index/push/c1", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not queue vector indexing"}


# --- PATCH /chat/chats/{id}/models --------------------------------------------


@pytest.fixture
def probeable(monkeypatch):
    """Make the embedding-capability probe answer without a network call."""
    import importlib

    # The class shadows its module in the package namespace, hence import_module.
    conversation = importlib.import_module("application.services.conversation.ConversationService")

    monkeypatch.setattr(
        conversation,
        "for_source",
        lambda source: SimpleNamespace(embedding_dimensions=_dimensions),
    )


async def _dimensions(tag):
    return 8


async def test_switching_the_embedding_model_rebuilds_the_index(
    client, chunked, fake_db, probeable
):
    """This path has no chain to append a build to, and reset=True has just
    dropped the collection *and its index*. Skipping the rebuild leaves the new
    model's vectors searchable only by exact scan."""
    fake_db.chats().items["c1"].has_documents = True

    response = await client.patch(
        "/chat/chats/c1/models", json={"embedding_model": "local/nomic-embed-text:latest"}
    )

    assert response.status_code == 200
    assert response.json()["reindexed_chunks"] == 1
    assert [row["collection_name"] for row in fake_db.vectors().indexed] == ["project_c1"]


async def test_a_chat_with_no_documents_indexes_nothing(client, chunked, fake_db, probeable):
    """There is nothing to rebuild, so neither the embedding nor the index
    build should run — the chat just changes model."""
    fake_db.chats().items["c1"].has_documents = False

    response = await client.patch(
        "/chat/chats/c1/models", json={"embedding_model": "local/nomic-embed-text:latest"}
    )

    assert response.status_code == 200
    assert fake_db.vectors().indexed == []


# --- which link of the chain the browser watches -------------------------------


async def test_the_upload_returns_the_first_link_not_the_last(ingest, client, seed, fake_db):
    """`apply_async` hands back the *last* link and reaches the others through
    `.parent`, so naming `.parent` positionally meant "the process task" only
    while the chain was two links long.

    A third link was added and the response silently began handing the browser
    the *index* task — which is queued behind OCR and reports no stage, so the
    progress row read "Queued" for seven minutes on a real upload and then
    jumped straight to 74%.
    """
    files = {"file": ("first-link.txt", b"some bytes to ingest", "text/plain")}

    response = await ingest("c1", files)
    body = response.json()

    rows = [t for t in fake_db.tasks().items.values() if t.project_id == "c1"]
    by_id = {t.task_id: t for t in rows}

    assert body["task_id"] in by_id, "the returned task_id is not a recorded task"
    assert by_id[body["task_id"]].task_name.endswith("process_data_task"), (
        f"the browser was pointed at {by_id[body['task_id']].task_name}, which is "
        "queued behind the work the user is waiting for"
    )


async def test_progress_reports_the_running_link_not_the_newest(ingest, client, seed, fake_db):
    """A chain writes every row in the same instant, so "most recent" is the
    last link — queued, stageless, and doing nothing yet. Asking a project what
    it is doing has to answer with the task that is actually running."""
    await ingest("c1", {"file": ("running.txt", b"bytes", "text/plain")})

    tasks = fake_db.tasks()
    rows = sorted(
        (t for t in tasks.items.values() if t.project_id == "c1"),
        key=lambda t: t.created_at,
    )
    assert len(rows) >= 2, "expected a multi-link chain to have been recorded"

    # The first link starts running; the rest stay queued behind it.
    await tasks.update_status(rows[0].task_id, "STARTED")
    await tasks.set_stage(rows[0].task_id, "extracting", 12, 222)

    active = await tasks.find_active_for_project("c1")

    assert active.task_id == rows[0].task_id
    assert active.stage == "extracting"
    assert (active.done, active.total) == (12, 222)
