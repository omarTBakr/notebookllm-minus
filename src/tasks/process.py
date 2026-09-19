from types import SimpleNamespace

from celery_app import SETTINGS, celery_app
from utils import get_logger

from .process_service import process_data
from .recorder import TaskRecorder, downstream_ids
from .runtime import job_resources, run_job
from .status import task_status

logger = get_logger(__name__)


async def _run_process_task(
    project_id: str,
    request_data: dict,
    task_id: str | None = None,
    downstream: list[str] | None = None,
) -> dict:
    async with job_resources() as job:
        # No task_id (a direct call, or a test) means no row to update, and
        # every recorder method becomes a no-op.
        recorder = TaskRecorder(job.db, task_id, counts_ingest=True)
        await recorder.started()

        try:
            request = SimpleNamespace(**request_data)
            result = await process_data(project_id, request, job.db, recorder=recorder)

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


@celery_app.task(
    # bind=True purely for self.request.id — the task's own Celery id, which
    # is what ties this run to its row in task_executions.
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.process_data_task",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def process_data_task(self, project_id: str, request_data: dict) -> dict:
    """Run document ingestion in a Celery worker process."""
    return run_job(
        lambda: _run_process_task(
            project_id,
            request_data,
            task_id=self.request.id,
            downstream=downstream_ids(self.request),
        ),
        what=f"Document processing for {project_id!r}",
    )


def get_process_task(task_id: str) -> dict:
    """Return the current state and result metadata for an ingestion task."""
    return task_status(task_id)
