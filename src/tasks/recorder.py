"""Writes a task's progress into the task_executions table, from the worker.

Deliberately reuses the database connection the task has already opened rather
than reaching for its own: a task is short, its connection is already there,
and a second pool per task would cost more than everything this records.

Every method is a no-op when there is no task id. That is what lets the task
bodies be called directly — from a test, or from a synchronous path — without
a broker, a worker, or a row to update. Recording is an accompaniment to the
work, never a precondition for it, which is also why each write is wrapped:
a task must not fail because its bookkeeping did.
"""

from time import perf_counter

from enums import TaskExecutionStatus
from models import TaskModel, summarize_result
from utils import get_logger
from utils.metrics import INGEST_DOCUMENTS, INGEST_STAGE_SECONDS

logger = get_logger(__name__)


def downstream_ids(request) -> list[str]:
    """The ids of the tasks queued after this one in the same chain.

    Celery hands each task the remainder of its chain, so a failing task can
    name exactly the work its failure cancels — which is what abandon() below
    then marks DEAD. Read defensively: the shape is an internal detail, and a
    task that runs outside a chain has none of it.

    Lives here rather than in one task module because every task that is not
    the *last* link needs it, which since the index build was split out is
    both process and index.
    """
    ids = []

    for link in getattr(request, "chain", None) or []:
        options = link.get("options") if isinstance(link, dict) else None
        task_id = (options or {}).get("task_id")

        if task_id:
            ids.append(task_id)

    return ids


class TaskRecorder:
    """Progress reporting for one task run."""

    def __init__(self, db, task_id: str | None, counts_ingest: bool = False) -> None:
        self.task_id = task_id
        self.tasks = TaskModel(db) if task_id else None
        # Only one task in a chain may count a document, or every upload would
        # be tallied twice. Processing owns it: it is the stage that decides
        # whether a document was readable at all.
        self.counts_ingest = counts_ingest
        # Stage timing moved here with the work itself. It used to be observed
        # by the in-process progress dict, which no longer exists — and which
        # only ever saw the stages of uploads that happened to land on its own
        # API process.
        self._stage = ""
        self._since = perf_counter()

    @property
    def enabled(self) -> bool:
        return self.tasks is not None

    async def started(self) -> None:
        await self._set(TaskExecutionStatus.STARTED.value)

    async def succeeded(self, result: dict | None = None) -> None:
        # summarize_result strips the chunk bodies: process returns the full
        # text of everything it created, and storing that would copy each
        # ingested document into the task table a second time.
        self._close_stage()

        if self.counts_ingest:
            INGEST_DOCUMENTS.labels("ok").inc()

        await self._set(TaskExecutionStatus.SUCCESS.value, result=summarize_result(result))

    async def failed(self, exc: BaseException) -> None:
        self._close_stage()

        if self.counts_ingest:
            INGEST_DOCUMENTS.labels("failed").inc()

        await self._set(
            TaskExecutionStatus.FAILURE.value,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    async def abandon(self, task_ids, reason: str) -> None:
        """Mark tasks that will never run now that this one has failed.

        A chain stops at its first failure, so everything after it is simply
        never published — leaving its rows QUEUED forever, indistinguishable
        from work that is genuinely still waiting for a worker. DEAD is the
        state Celery has no equivalent for and the row exists to record.
        """
        if not self.enabled:
            return

        for task_id in task_ids:
            try:
                await self.tasks.update_status(
                    task_id,
                    TaskExecutionStatus.DEAD.value,
                    error=reason,
                    error_type="ChainAbandoned",
                )
            except Exception as exc:
                logger.warning("Could not abandon %r: %s", task_id, exc)

    async def stage(self, stage: str, done: int = 0, total: int = 0) -> None:
        """Report which phase of ingestion is running, for the progress poll."""
        if not self.enabled:
            return

        if stage != self._stage:
            self._close_stage()
            self._stage = stage
            self._since = perf_counter()

        try:
            await self.tasks.set_stage(self.task_id, stage, done, total)
        except Exception as exc:
            logger.warning("Could not record stage %r for %r: %s", stage, self.task_id, exc)

    def _close_stage(self) -> None:
        """Observe how long the stage that is ending took."""
        if not self._stage:
            return

        INGEST_STAGE_SECONDS.labels(self._stage).observe(perf_counter() - self._since)
        self._stage = ""

    async def _set(self, status: str, **fields) -> None:
        if not self.enabled:
            return

        try:
            await self.tasks.update_status(self.task_id, status, **fields)
        except Exception as exc:
            # Never let bookkeeping break the task. A lost status line is a
            # reporting gap; an exception here would turn a successful ingest
            # into a failed one.
            logger.warning("Could not record %s for %r: %s", status, self.task_id, exc)
