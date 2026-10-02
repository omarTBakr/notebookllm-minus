"""Fetch a URL source in a worker, then hand it to normal ingestion."""

from application.services import SourceService, UrlSourceService
from celery_app import SETTINGS, celery_app
from data.models import ChatModel
from shared.enums import CeleryTaskFunction, TaskStage

from ...runtime import job_resources, run_job
from ...tracking.recorder import TaskRecorder


async def _fetch_and_attach(project_id: str, url: str, db, task_id: str | None = None) -> dict:
    recorder = TaskRecorder(db, task_id)
    await recorder.started()
    await recorder.stage(TaskStage.EXTRACTING.value)

    try:
        chat = await ChatModel(db).get_chat(project_id)
        fetched = await UrlSourceService().fetch(url)
        asset, queued = await SourceService(db).attach(
            chat,
            filename=fetched.name,
            content_type=fetched.content_type,
            file_bytes=fetched.content,
            chunk_size=chat.chunk_size or 1000,
            overlap_size=chat.overlap_size if chat.overlap_size is not None else 200,
            asset_type=fetched.asset_type,
            content_hash=fetched.content_hash,
            source_url=fetched.source_url,
        )
        result = {"asset_id": asset.asset_id, "filename": asset.name, "ingestion_task_id": queued.task_id}
        await recorder.succeeded(result)
        return result
    except BaseException as exc:
        await recorder.failed(exc)
        raise


async def fetch_and_attach(project_id: str, url: str, task_id: str | None = None) -> dict:
    async with job_resources() as job:
        return await _fetch_and_attach(project_id, url, job.db, task_id)


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.FETCH_URL.value}",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def fetch_url_task(self, project_id: str, url: str) -> dict:
    return run_job(
        lambda: fetch_and_attach(project_id, url, self.request.id), what=f"Fetching source for {project_id!r}"
    )
