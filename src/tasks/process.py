import asyncio
from types import SimpleNamespace

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import SETTINGS, celery_app
from exceptions import CeleryTaskError
from factories import DbFactory
from utils import get_logger, get_settings

from .process_service import process_data
from .recorder import TaskRecorder
from .status import task_status

logger = get_logger(__name__)


def _downstream_ids(request) -> list[str]:
    """The ids of the tasks queued after this one in the same chain.

    Celery hands each task the remainder of its chain, so a failing task can
    name exactly the work its failure cancels. Read defensively: the shape is
    an internal detail, and a task that runs outside a chain has none of it.
    """
    ids = []

    for link in getattr(request, "chain", None) or []:
        options = link.get("options") if isinstance(link, dict) else None
        task_id = (options or {}).get("task_id")

        if task_id:
            ids.append(task_id)

    return ids


async def _run_process_task(
    project_id: str,
    request_data: dict,
    task_id: str | None = None,
    downstream: list[str] | None = None,
) -> dict:
    settings = get_settings()
    db = DbFactory(settings).create()
    try:
        await db.connect()
        # No task_id (a direct call, or a test) means no row to update, and
        # every recorder method becomes a no-op.
        recorder = TaskRecorder(db, task_id, counts_ingest=True)
        await recorder.started()

        try:
            request = SimpleNamespace(**request_data)
            result = await process_data(project_id, request, db, recorder=recorder)

        except BaseException as exc:
            # BaseException, not Exception: SoftTimeLimitExceeded inherits
            # from it, and a task killed on the time limit is exactly the one
            # whose failure most needs recording.
            await recorder.failed(exc)
            await recorder.abandon(
                downstream or [],
                f"cancelled: {type(exc).__name__} in {project_id!r} ingestion",
            )
            raise

        await recorder.succeeded(result)

        return result
    finally:
        await db.disconnect()


@celery_app.task(
    # bind=True purely for self.request.id — the task's own Celery id, which
    # is what ties this run to its row in task_executions.
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.process_data_task",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def process_data_task(self, project_id: str, request_data: dict) -> dict:
    """Run document ingestion in a Celery worker process."""
    try:
        return asyncio.run(
            _run_process_task(
                project_id,
                request_data,
                task_id=self.request.id,
                downstream=_downstream_ids(self.request),
            )
        )

    except SoftTimeLimitExceeded as exc:
        # Reached only because task_soft_time_limit fires *inside* the task:
        # `asyncio.run` unwinds, `_run_process_task`'s finally disconnects the
        # DB, and only then does this run. Under the hard limit alone the
        # child is killed here and none of that happens.
        logger.error(
            "process_data_task for project %r exceeded its soft time limit of %ss",
            project_id,
            SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT,
        )
        raise CeleryTaskError(
            f"Document processing for {project_id!r} exceeded "
            f"{SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT}s and was stopped"
        ) from exc


def get_process_task(task_id: str) -> dict:
    """Return the current state and result metadata for an ingestion task."""
    return task_status(task_id)
