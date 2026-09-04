from types import SimpleNamespace

import pytest
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis.exceptions import RedisError


def _fake_chain(calls, index_id="index-1", process_id="task-123"):
    """Stand in for ingestion_chain, recording how it was built.

    A chain's AsyncResult names its *last* task and reaches the earlier one
    through .parent, so the fake has to reproduce that shape or the route's
    two-row bookkeeping is not actually exercised.
    """
    def build(project_id, request_data, asset_id=None, batch_size=None):
        calls.append((project_id, request_data, asset_id, batch_size))
        return SimpleNamespace(
            apply_async=lambda: SimpleNamespace(
                id=index_id, parent=SimpleNamespace(id=process_id)
            )
        )

    return build


async def test_process_queues_the_whole_chain(client, monkeypatch):
    """One POST now queues process *and* index. The client used to have to
    poll /process and then call /nlp/index/push itself, and a client that
    stopped halfway left a project chunked but unindexed."""
    import routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_chain", _fake_chain(calls))

    response = await client.post(
        "/process/project-1",
        json={"asset_id": "asset-1", "chunk_size": 800, "overlap_size": 100},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task-123"          # the process half
    assert body["index_task_id"] == "index-1"     # queued without a second call
    assert body["queued"] is True
    assert calls == [
        (
            "project-1",
            {"asset_id": "asset-1", "chunk_size": 800, "overlap_size": 100, "reset": False},
            "asset-1",
            None,
        )
    ]


async def test_both_halves_of_the_chain_get_a_row(client, monkeypatch):
    """A chain's AsyncResult knows only its last task, so without recording
    .parent as well the process half would report UNKNOWN for its whole run."""
    import routes.process as process_route

    monkeypatch.setattr(process_route, "ingestion_chain", _fake_chain([]))

    await client.post("/process/project-1", json={"asset_id": "asset-1"})

    rows = client._transport.app.db.tasks().items

    assert sorted(rows) == ["index-1", "task-123"]
    assert {r.task_name.split(".")[-1] for r in rows.values()} == {
        "process_data_task",
        "index_project_task",
    }


async def test_an_identical_submission_joins_the_running_one(client, monkeypatch):
    """A double-click must not queue a second ingestion of the same document:
    it costs the embedding twice and hands the caller an id that is not the
    run they are watching."""
    import routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_chain", _fake_chain(calls))

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
    import routes.process as process_route

    calls = []
    monkeypatch.setattr(process_route, "ingestion_chain", _fake_chain(calls))

    await client.post("/process/project-1", json={"asset_id": "asset-1"})
    second = await client.post(
        "/process/project-1", json={"asset_id": "asset-1", "reset": True}
    )

    assert second.status_code == 202
    assert len(calls) == 2


async def test_process_returns_503_when_broker_rejects_task(client, monkeypatch):
    import routes.process as process_route

    def enqueue(*args, **kwargs):
        # What kombu actually raises when the broker is unreachable. Not
        # RuntimeError: that is an ordinary bug, and mapping it to 503 would
        # blame the broker for a fault in this process.
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(
        process_route,
        "ingestion_chain",
        lambda *a, **k: SimpleNamespace(apply_async=enqueue),
    )

    response = await client.post("/process/project-1", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not queue document processing"}


async def test_process_status_returns_result(client, monkeypatch):
    import tasks.status as status_module

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
    import tasks.status as status_module

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
    from exceptions import (
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
async def test_process_task_disconnects_database_after_processing(monkeypatch):
    import importlib

    process_tasks = importlib.import_module("tasks.process")
    calls = []

    class FakeDb:
        async def connect(self):
            calls.append("connect")

        async def disconnect(self):
            calls.append("disconnect")

    class FakeFactory:
        def __init__(self, settings):
            calls.append(("settings", settings))

        def create(self):
            return FakeDb()

    async def fake_process(project_id, request, db, recorder=None):
        calls.append((project_id, request.asset_id, request.reset))
        return {"status": "ok"}

    monkeypatch.setattr(process_tasks, "get_settings", lambda: "settings")
    monkeypatch.setattr(process_tasks, "DbFactory", FakeFactory)
    monkeypatch.setattr(process_tasks, "process_data", fake_process)

    result = await process_tasks._run_process_task(
        "project-1",
        {"asset_id": "asset-1", "chunk_size": 1000, "overlap_size": 200, "reset": True},
    )

    assert result == {"status": "ok"}
    assert calls == [
        ("settings", "settings"),
        "connect",
        ("project-1", "asset-1", True),
        "disconnect",
    ]


async def test_a_bug_in_enqueueing_is_not_reported_as_a_broker_outage(client, monkeypatch):
    """RuntimeError used to be in CELERY_BROKER_EXCEPTIONS, so any ordinary
    programming error inside .delay() answered 503 "Could not queue document
    processing" — blaming RabbitMQ for a fault in this process, and hiding the
    real traceback behind a status that reads as infrastructure."""
    import routes.process as process_route

    def enqueue(*args, **kwargs):
        raise RuntimeError("a genuine bug, not an outage")

    monkeypatch.setattr(
        process_route,
        "ingestion_chain",
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
    import tasks.status as status_module

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
    import tasks.status as status_module

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
    import tasks.status as status_module

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
    import tasks.status as status_module

    monkeypatch.setattr(status_module, "_redis", lambda: None)

    assert status_module._was_queued("anything") is True


# --- the soft time limit exists so cleanup runs -------------------------------


def test_a_soft_timeout_is_reported_as_a_celery_task_error(monkeypatch):
    """The hard limit SIGKILLs the worker child and skips every `finally` on the
    way out, leaking the DB connection and the provider pools. The soft limit
    raises inside the task instead; this is the path that proves it unwinds
    through the task's own error handling rather than dying mid-frame."""
    from celery.exceptions import SoftTimeLimitExceeded

    import tasks.process as process_tasks
    from exceptions import CeleryError, CeleryTaskError

    closed = []

    async def slow(project_id, request_data, task_id=None, downstream=None):
        try:
            raise SoftTimeLimitExceeded()
        finally:
            closed.append("db disconnected")

    monkeypatch.setattr(process_tasks, "_run_process_task", slow)

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

    from utils.config import Settings

    with pytest.raises(ValidationError, match="must be below"):
        Settings(CELERY_TASK_SOFT_TIME_LIMIT=600, CELERY_TASK_TIME_LIMIT=600)


# --- a chain that dies must not leave its tail looking queued ----------------


def test_the_rest_of_a_failed_chain_is_marked_dead():
    """A chain stops at its first failure, so everything after it is never
    published. Those rows stayed QUEUED forever — indistinguishable from work
    genuinely still waiting for a worker, which is the exact ambiguity the
    table was added to remove."""
    from types import SimpleNamespace as NS

    import tasks.process as process_tasks

    request = NS(
        id="proc-1",
        chain=[{"options": {"task_id": "index-1"}}, {"options": {"task_id": "index-2"}}],
    )

    assert process_tasks._downstream_ids(request) == ["index-1", "index-2"]


def test_a_task_outside_a_chain_has_no_downstream():
    """Reading Celery's internal chain shape defensively: a bare .delay() has
    no chain at all, and must not raise on the failure path."""
    from types import SimpleNamespace as NS

    import tasks.process as process_tasks

    assert process_tasks._downstream_ids(NS(id="solo", chain=None)) == []
    assert process_tasks._downstream_ids(NS(id="solo")) == []


async def test_abandoning_records_a_terminal_state(fake_db):
    """DEAD, not FAILURE: this task did not fail, it was cancelled by one that
    did — and Celery has no state for that, which is why the row does."""
    from enums import TaskExecutionStatus
    from models.db_schema import TaskExecution
    from tasks.recorder import TaskRecorder

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

    from enums import TaskExecutionStatus
    from models.db_schema import TaskExecution

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

    from enums import TaskExecutionStatus
    from models.db_schema import TaskExecution

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

    from enums import TaskExecutionStatus
    from models.db_schema import TaskExecution

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
