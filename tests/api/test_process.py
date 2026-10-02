from types import SimpleNamespace

import pytest
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis.exceptions import RedisError

from shared.exceptions import ProjectNotFoundError


def _fake_chain(calls):
    """Stand in for ingestion_signature, recording how it was called.

    Flat, where this used to reproduce a three-deep `.parent` nesting:
    ingestion is no longer a Celery chain, so the route takes its three ids
    from `QueuedChain` rather than walking a published one. The route's
    per-stage bookkeeping still runs against those ids, which is the thing
    under test.
    """

    def build(project_id, request_data, queued):
        calls.append((project_id, request_data, queued))
        return SimpleNamespace(apply_async=lambda *a, **k: None)

    return build


async def test_process_queues_the_whole_chain(client, monkeypatch):
    """One POST now queues process *and* index. The client used to have to
    poll /process and then call /nlp/index/push itself, and a client that
    stopped halfway left a project chunked but unindexed."""
    import presentation.routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_signature", _fake_chain(calls))

    response = await client.post(
        "/process/project-1",
        json={"asset_id": "asset-1", "chunk_size": 800, "overlap_size": 100},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["queued"] is True

    # Three distinct ids, handed back so a client can poll any stage.
    ids = {body["task_id"], body["index_task_id"], body["build_index_task_id"]}
    assert len(ids) == 3

    project_id, request_data, queued = calls[0]
    assert project_id == "project-1"
    assert request_data["asset_id"] == "asset-1"
    assert request_data["chunk_size"] == 800
    assert request_data["overlap_size"] == 100
    assert request_data["reset"] is False
    # The ids the route reported are the ones it published under.
    assert body["task_id"] == queued.process_id
    assert body["index_task_id"] == queued.index_id
    assert body["build_index_task_id"] == queued.build_id


async def test_every_stage_gets_a_row(client, monkeypatch):
    """All three rows are written before any of the work runs.

    Index and build are published by the collector minutes later, so without a
    row apiece the browser would poll two ids that do not exist yet and be told
    UNKNOWN for the whole run."""
    import presentation.routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_signature", _fake_chain(calls))

    await client.post("/process/project-1", json={"asset_id": "asset-1"})

    rows = client._transport.app.db.tasks().items
    _, _, queued = calls[0]

    assert sorted(rows) == sorted(queued.ids)
    # Each id is recorded under the name of the task it actually is — pairing
    # them the wrong way round would make every status poll answer about a
    # different stage than the one it asked for.
    assert {task_id: row.task_name.split(".")[-1] for task_id, row in rows.items()} == {
        queued.process_id: "process_data_task",
        queued.index_id: "index_project_task",
        queued.build_id: "build_vector_index_task",
    }


async def test_an_identical_submission_joins_the_running_one(client, monkeypatch):
    """A double-click must not queue a second ingestion of the same document:
    it costs the embedding twice and hands the caller an id that is not the
    run they are watching."""
    import presentation.routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_signature", _fake_chain(calls))

    body = {"asset_id": "asset-1", "chunk_size": 800, "overlap_size": 100}

    first = await client.post("/process/project-1", json=body)
    second = await client.post("/process/project-1", json=body)

    assert first.status_code == 202 and first.json()["queued"] is True
    # 200, not 202: nothing new was queued, and it is not an error either.
    assert second.status_code == 200
    assert second.json()["queued"] is False
    assert second.json()["task_id"] == first.json()["task_id"]
    assert len(calls) == 1, "the second submission must not reach the broker"


async def test_different_arguments_are_not_deduplicated(client, monkeypatch):
    """Only *identical* work joins an existing run — re-ingesting the same
    project with reset=true is a different request and must queue."""
    import presentation.routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_signature", _fake_chain(calls))

    await client.post("/process/project-1", json={"asset_id": "asset-1"})
    second = await client.post("/process/project-1", json={"asset_id": "asset-1", "reset": True})

    assert second.status_code == 202
    assert len(calls) == 2


async def test_process_returns_503_when_broker_rejects_task(client, monkeypatch):
    import presentation.routes.process as process_route

    def enqueue(*args, **kwargs):
        # What kombu actually raises when the broker is unreachable. Not
        # RuntimeError: that is an ordinary bug, and mapping it to 503 would
        # blame the broker for a fault in this process.
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(
        process_route,
        "ingestion_signature",
        lambda *a, **k: SimpleNamespace(apply_async=enqueue),
    )

    response = await client.post("/process/project-1", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not queue document processing"}


async def test_process_status_returns_result(client, monkeypatch):
    import application.tasks.tracking.status as status_module

    class CompletedResult:
        status = "SUCCESS"

        def successful(self):
            return True

        def failed(self):
            return False

        result = {"status": "processing_success"}

    monkeypatch.setattr(status_module, "AsyncResult", lambda task_id, app: CompletedResult())

    response = await client.get("/process/tasks/task-123")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-123",
        "status": "SUCCESS",
        "result": {"status": "processing_success"},
    }


async def test_process_status_returns_503_when_result_backend_fails(client, monkeypatch):
    import application.tasks.tracking.status as status_module

    class BrokenResult:
        @property
        def status(self):
            # redis-py's hierarchy, not a builtin — RedisError derives straight
            # from Exception, so this only maps to 503 because the boundary
            # tuple names it explicitly.
            raise RedisError("backend unavailable")

    monkeypatch.setattr(status_module, "AsyncResult", lambda task_id, app: BrokenResult())

    response = await client.get("/process/tasks/task-123")

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not read Celery task 'task-123'"}


def test_celery_errors_are_application_errors():
    from shared.exceptions import (
        CeleryBrokerError,
        CeleryError,
        CeleryResultError,
        CeleryTaskError,
    )

    assert issubclass(CeleryBrokerError, CeleryError)
    assert issubclass(CeleryResultError, CeleryError)
    assert issubclass(CeleryTaskError, CeleryError)
    assert CeleryError.status_code == 503


@pytest.mark.asyncio
async def test_planning_disconnects_the_database_even_when_it_fails(monkeypatch, fake_db):
    """The planner opens its own connection and must close it on both paths.

    A worker is a separate process tree with its own pool, and planning can
    raise on perfectly ordinary input -- an asset that was deleted between the
    upload and the worker picking it up. A leaked connection per failed upload
    exhausts the pool and then every *later* upload fails for a reason that has
    nothing to do with it."""
    import importlib

    process_tasks = importlib.import_module("application.tasks.jobs.ingest.process")
    runtime = importlib.import_module("application.tasks.runtime")
    calls = []

    class WatchedDb:
        def __init__(self, inner):
            self._inner = inner

        async def connect(self):
            calls.append("connect")

        async def disconnect(self):
            calls.append("disconnect")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class FakeFactory:
        def __init__(self, settings):
            pass

        def create(self):
            return WatchedDb(fake_db)

    # On tasks.runtime: `job_resources` is what builds the connection now, and
    # the connect/disconnect pairing this test guards lives there.
    monkeypatch.setattr(runtime, "DbFactory", FakeFactory)

    with pytest.raises(ProjectNotFoundError):
        await process_tasks._run_plan("project-with-nothing-in-it", {})

    assert calls == ["connect", "disconnect"], "the planner must disconnect on the failure path too"


@pytest.mark.asyncio
async def test_a_batch_covers_every_page_exactly_once(monkeypatch):
    """The page ranges must tile the document: no gap, no overlap.

    A gap is a page silently missing from the notebook and a overlap is a page
    chunked twice, and neither shows up as an error anywhere -- the ingest
    reports success either way."""
    import importlib
    from types import SimpleNamespace

    process_tasks = importlib.import_module("application.tasks.jobs.ingest.process")

    settings = SimpleNamespace(PDF_BATCH_PAGES=10, PDF_LOADER="pymupdf")
    asset = SimpleNamespace(asset_id="a1", name="book.pdf", file_bytes=b"%PDF-fake")

    for total in (1, 9, 10, 11, 274):
        monkeypatch.setattr(process_tasks, "_cache_file", lambda *a, **k: "/tmp/x.pdf")
        monkeypatch.setattr(process_tasks, "page_count", lambda _p, n=total: n)

        ranges = process_tasks._batches_for(asset, settings)

        assert all(end - start <= 10 for start, end in ranges), f"a batch exceeded 10 pages at {total}"

        covered = [page for start, end in ranges for page in range(start, end)]

        assert covered == list(range(total)), f"pages were dropped or repeated at {total}"


async def test_a_bug_in_enqueueing_is_not_reported_as_a_broker_outage(client, monkeypatch):
    """RuntimeError used to be in CELERY_BROKER_EXCEPTIONS, so any ordinary
    programming error inside .delay() answered 503 "Could not queue document
    processing" — blaming RabbitMQ for a fault in this process, and hiding the
    real traceback behind a status that reads as infrastructure."""
    import presentation.routes.process as process_route

    def enqueue(*args, **kwargs):
        raise RuntimeError("a genuine bug, not an outage")

    monkeypatch.setattr(
        process_route,
        "ingestion_signature",
        lambda *a, **k: SimpleNamespace(apply_async=enqueue),
    )

    response = await client.post("/process/project-1", json={})

    assert response.status_code == 500
    assert response.json() != {"detail": "Could not queue document processing"}


# --- telling apart the four things that all used to say PENDING ---------------


async def test_an_id_that_was_never_queued_is_unknown_not_pending(client, monkeypatch):
    """Celery synthesises PENDING for any id it has no record of, so a typo and
    a task waiting for a worker were indistinguishable — and the ambiguity ran
    the wrong way: the client was told to keep polling something that would
    never arrive."""
    import application.tasks.tracking.status as status_module

    class NoRecord:
        status = "PENDING"

        def successful(self):
            return False

        def failed(self):
            return False

    monkeypatch.setattr(status_module, "AsyncResult", lambda task_id, app: NoRecord())
    monkeypatch.setattr(status_module, "_was_queued", lambda task_id: False)

    body = (await client.get("/process/tasks/never-queued")).json()

    assert body["status"] == "UNKNOWN"
    assert "was ever queued" in body["error"]


async def test_a_queued_id_still_reports_pending(client, monkeypatch):
    """The other half of the same distinction: a real task that no worker has
    picked up yet must stay PENDING, or the client stops polling too early."""
    import application.tasks.tracking.status as status_module

    class NoRecord:
        status = "PENDING"

        def successful(self):
            return False

        def failed(self):
            return False

    monkeypatch.setattr(status_module, "AsyncResult", lambda task_id, app: NoRecord())
    monkeypatch.setattr(status_module, "_was_queued", lambda task_id: True)

    body = (await client.get("/process/tasks/really-queued")).json()

    assert body["status"] == "PENDING"
    assert "error" not in body


async def test_a_failure_reports_its_exception_type(client, monkeypatch):
    """`str(exc)` alone threw away half the diagnosis: a missing project and a
    broker timeout both arrived as an untyped string."""
    import application.tasks.tracking.status as status_module

    class FailedResult:
        status = "FAILURE"
        result = ValueError("Project 'x' has no chunks to index")

        def successful(self):
            return False

        def failed(self):
            return True

    monkeypatch.setattr(status_module, "AsyncResult", lambda task_id, app: FailedResult())

    body = (await client.get("/process/tasks/task-fail")).json()

    assert body["status"] == "FAILURE"
    assert body["error_type"] == "ValueError"
    assert "no chunks to index" in body["error"]


async def test_a_missing_marker_backend_does_not_invent_unknown(monkeypatch):
    """When the backend is not Redis there is no marker to read. Reporting a
    real task as UNKNOWN would be worse than reporting a typo as PENDING, so
    the unknowable case resolves to the harmless one."""
    import application.tasks.tracking.status as status_module

    monkeypatch.setattr(status_module, "_redis", lambda: None)

    assert status_module._was_queued("anything") is True


# --- the soft time limit exists so cleanup runs -------------------------------


def test_a_soft_timeout_is_reported_as_a_celery_task_error(monkeypatch):
    """The hard limit SIGKILLs the worker child and skips every `finally` on the
    way out, leaking the DB connection and the provider pools. The soft limit
    raises inside the task instead; this is the path that proves it unwinds
    through the task's own error handling rather than dying mid-frame."""
    from celery.exceptions import SoftTimeLimitExceeded

    import application.tasks.jobs.ingest.process as process_tasks
    from shared.exceptions import CeleryError, CeleryTaskError

    closed = []

    async def slow(project_id, request_data, task_id=None, downstream=None):
        try:
            raise SoftTimeLimitExceeded()
        finally:
            closed.append("db disconnected")

    monkeypatch.setattr(process_tasks, "_run_plan", slow)

    with pytest.raises(CeleryTaskError) as caught:
        # The task is bound (bind=True, for self.request.id); calling it
        # directly still injects self, so the call shape is unchanged.
        process_tasks.process_data_task("proj-1", {})

    # The cleanup a hard kill would have skipped.
    assert closed == ["db disconnected"]
    assert issubclass(CeleryTaskError, CeleryError)
    assert "exceeded" in str(caught.value)


def test_the_soft_limit_must_be_below_the_hard_limit():
    """A soft limit at or above the hard one can never fire, which silently
    restores the exact behaviour it was added to prevent."""
    from pydantic import ValidationError

    from shared.utils.config import Settings

    with pytest.raises(ValidationError, match="must be below"):
        Settings(CELERY_TASK_SOFT_TIME_LIMIT=600, CELERY_TASK_TIME_LIMIT=600)


# --- a chain that dies must not leave its tail looking queued ----------------


def test_the_rest_of_a_failed_chain_is_marked_dead():
    """A chain stops at its first failure, so everything after it is never
    published. Those rows stayed QUEUED forever — indistinguishable from work
    genuinely still waiting for a worker, which is the exact ambiguity the
    table was added to remove."""
    from types import SimpleNamespace as NS

    from application.tasks.tracking.recorder import downstream_ids

    request = NS(
        id="proc-1",
        chain=[{"options": {"task_id": "index-1"}}, {"options": {"task_id": "index-2"}}],
    )

    assert downstream_ids(request) == ["index-1", "index-2"]


def test_a_task_outside_a_chain_has_no_downstream():
    """Reading Celery's internal chain shape defensively: a bare .delay() has
    no chain at all, and must not raise on the failure path."""
    from types import SimpleNamespace as NS

    from application.tasks.tracking.recorder import downstream_ids

    assert downstream_ids(NS(id="solo", chain=None)) == []
    assert downstream_ids(NS(id="solo")) == []


async def test_abandoning_records_a_terminal_state(fake_db):
    """DEAD, not FAILURE: this task did not fail, it was cancelled by one that
    did — and Celery has no state for that, which is why the row does."""
    from application.tasks.tracking.recorder import TaskRecorder
    from data.models import TaskExecution
    from shared.enums import TaskExecutionStatus

    await fake_db.tasks().create_task(
        TaskExecution(
            task_id="index-1",
            task_name="notebookllm.index_project_task",
            project_id="p1",
        )
    )

    await TaskRecorder(fake_db, "proc-1").abandon(["index-1"], "cancelled: upstream failed")

    row = await fake_db.tasks().get_task("index-1")

    assert row.status == TaskExecutionStatus.DEAD
    assert row.error_type == "ChainAbandoned"
    assert row.completed_at is not None


# --- the maintenance sweep ----------------------------------------------------


async def test_the_sweep_marks_a_run_whose_worker_vanished(fake_db):
    """The one state the table cannot correct on its own. A worker killed
    mid-task leaves STARTED behind forever, because the process that would
    have written the ending no longer exists."""
    from datetime import datetime, timedelta, timezone

    from data.models import TaskExecution
    from shared.enums import TaskExecutionStatus

    now = datetime.now(timezone.utc)
    tasks = fake_db.tasks()

    await tasks.create_task(
        TaskExecution(
            task_id="abandoned",
            task_name="notebookllm.process_data_task",
            project_id="p1",
            status=TaskExecutionStatus.STARTED,
            started_at=now - timedelta(hours=6),
        )
    )
    await tasks.create_task(
        TaskExecution(
            task_id="still-running",
            task_name="notebookllm.process_data_task",
            project_id="p1",
            status=TaskExecutionStatus.STARTED,
            started_at=now,
        )
    )

    marked = await tasks.mark_abandoned(
        now - timedelta(minutes=30), now - timedelta(days=7), TaskExecutionStatus.DEAD.value
    )

    assert marked == 1
    assert (await tasks.get_task("abandoned")).status == TaskExecutionStatus.DEAD
    assert (await tasks.get_task("abandoned")).error_type == "WorkerLost"
    # A task that started a moment ago is doing its job, not lost.
    assert (await tasks.get_task("still-running")).status == TaskExecutionStatus.STARTED


async def test_queued_work_is_given_far_longer_than_running_work(fake_db):
    """A task waits legitimately for as long as its workers are down, so the
    queued cutoff must not be the tight one used for a run that has stalled —
    otherwise a deploy would be reported as lost work."""
    from datetime import datetime, timedelta, timezone

    from data.models import TaskExecution
    from shared.enums import TaskExecutionStatus

    now = datetime.now(timezone.utc)
    tasks = fake_db.tasks()

    await tasks.create_task(
        TaskExecution(
            task_id="waiting",
            task_name="notebookllm.index_project_task",
            project_id="p1",
            status=TaskExecutionStatus.QUEUED,
            created_at=now - timedelta(hours=2),
        )
    )

    # Two hours old: past the running cutoff, nowhere near the queued one.
    marked = await tasks.mark_abandoned(
        now - timedelta(minutes=20), now - timedelta(days=7), TaskExecutionStatus.DEAD.value
    )

    assert marked == 0
    assert (await tasks.get_task("waiting")).status == TaskExecutionStatus.QUEUED


async def test_the_sweep_never_deletes_unfinished_work(fake_db):
    """Retention applies to finished rows only. Deleting something still
    queued would lose the record of work that is about to happen."""
    from datetime import datetime, timedelta, timezone

    from data.models import TaskExecution
    from shared.enums import TaskExecutionStatus

    old = datetime.now(timezone.utc) - timedelta(days=30)
    tasks = fake_db.tasks()

    for task_id, status in (
        ("done", TaskExecutionStatus.SUCCESS),
        ("failed", TaskExecutionStatus.FAILURE),
        ("queued", TaskExecutionStatus.QUEUED),
    ):
        await tasks.create_task(
            TaskExecution(
                task_id=task_id,
                task_name="notebookllm.process_data_task",
                project_id="p1",
                status=status,
                created_at=old,
            )
        )

    deleted = await tasks.delete_finished_before(datetime.now(timezone.utc))

    assert deleted == 2
    assert sorted(tasks.items) == ["queued"]
