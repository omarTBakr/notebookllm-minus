import asyncio

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import SETTINGS, celery_app
from controllers import NLPController
from enums import TaskStage
from exceptions import CeleryTaskError, ChatNotFoundError
from factories import DbFactory, ProviderCache
from models import ChatModel, ChunkModel, ProjectModel
from utils import get_logger

from .recorder import TaskRecorder, downstream_ids
from .status import task_status

logger = get_logger(__name__)


def _record_progress(recorder: TaskRecorder, done: int, total: int) -> None:
    """Bridge index_chunks' synchronous on_progress into the async recorder.

    index_chunks calls this from inside its embedding loop, which is already
    running on the event loop, so the update is scheduled rather than awaited:
    blocking the loop to write a progress row would slow the very work being
    reported. A dropped update is harmless — the next batch overwrites it.
    """
    if not recorder.enabled:
        return

    try:
        asyncio.get_running_loop().create_task(recorder.stage(TaskStage.INDEXING.value, done, total))
    except RuntimeError:
        pass


async def _run_index_task(
    project_id: str,
    asset_id: str | None,
    reset: bool,
    batch_size: int | None,
    task_id: str | None = None,
    downstream: list[str] | None = None,
) -> dict:
    settings = SETTINGS
    db = DbFactory(settings).create()
    providers = None
    try:
        await db.connect()
        recorder = TaskRecorder(db, task_id)
        await recorder.started()
        providers = ProviderCache(settings)
        project = await ProjectModel(db).get_project(project_id)
        chat = None
        try:
            chat = await ChatModel(db).get_chat(project_id)
        except ChatNotFoundError:
            # Projects created through /process do not have a chat row.
            pass

        embedding_client = providers.embedding(
            getattr(chat, "embedding_model", None),
            getattr(chat, "embedding_dimensions", None),
        )
        controller = NLPController(
            embedding_client=embedding_client,
            vectordb_client=db.vectors(),
        )
        chunk_model = ChunkModel(db)
        chunks_found = await chunk_model.count_project_chunks(project.id, asset_id)
        if not chunks_found:
            raise ValueError(f"Project {project_id!r} has no chunks to index")

        await recorder.stage(TaskStage.INDEXING.value, 0, chunks_found)

        try:
            result = await controller.index_chunks(
                chunk_model=chunk_model,
                project_object_id=project.id,
                project_id=project_id,
                asset_id=asset_id,
                reset=reset,
                batch_size=batch_size,
                # The controller already reports embedding progress this way
                # for the synchronous upload path; it now feeds the row the
                # browser polls instead of a dict in one API process.
                on_progress=lambda done, total: _record_progress(recorder, done, total),
            )

        except BaseException as exc:
            await recorder.failed(exc)
            # Indexing is no longer the last link: build_vector_index_task
            # follows it, and a chain stops at its first failure, so its row
            # would otherwise sit QUEUED forever.
            await recorder.abandon(
                downstream or [],
                f"cancelled: {type(exc).__name__} while indexing {project_id!r}",
            )
            raise

        await recorder.succeeded(result)

        return result
    finally:
        try:
            if providers is not None:
                await providers.aclose_all()
        finally:
            await db.disconnect()


@celery_app.task(
    # bind=True for self.request.id — see process_data_task.
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.index_project_task",
    queue=SETTINGS.CELERY_QUEUE_INDEX,
)
def index_project_task(
    self,
    project_id: str,
    asset_id: str | None = None,
    reset: bool = False,
    # None, not 32: index_chunks falls back to CHUNKING_BATCH_SIZE (512) when
    # this is None, which is what the route has always produced — PushRequest
    # defaults batch_size to None too. A literal 32 here meant anyone calling
    # the task directly silently got batches 16x smaller, and 16x the round
    # trips to the embedding provider, for the same work.
    batch_size: int | None = None,
) -> dict:
    """Embed and index chunks without consuming an API process slot."""
    try:
        return asyncio.run(
            _run_index_task(
                project_id,
                asset_id,
                reset,
                batch_size,
                task_id=self.request.id,
                downstream=downstream_ids(self.request),
            )
        )

    except SoftTimeLimitExceeded as exc:
        # The nested finally in _run_index_task closes the provider pools and
        # then the DB. A hard kill skipped both, leaking an HTTP connection
        # pool per timed-out index run.
        logger.error(
            "index_project_task for project %r exceeded its soft time limit of %ss",
            project_id,
            SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT,
        )
        raise CeleryTaskError(
            f"Indexing {project_id!r} exceeded " f"{SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT}s and was stopped"
        ) from exc


async def _run_build_index_task(project_id: str, task_id: str | None = None) -> dict:
    settings = SETTINGS
    db = DbFactory(settings).create()
    providers = None
    try:
        await db.connect()
        recorder = TaskRecorder(db, task_id)
        await recorder.started()
        providers = ProviderCache(settings)

        # The whole reason this needs a provider at all: the index has to be
        # built at the width the *chat's* embedding model produces, which is
        # not necessarily the .env default — the model picker writes it per
        # chat, and building at the wrong width is what pgvector reports as
        # "different vector dimensions".
        try:
            chat = await ChatModel(db).get_chat(project_id)
        except ChatNotFoundError:
            # Projects created through /process do not have a chat row.
            chat = None

        controller = NLPController(
            embedding_client=providers.embedding(
                getattr(chat, "embedding_model", None),
                getattr(chat, "embedding_dimensions", None),
            ),
            vectordb_client=db.vectors(),
        )

        await recorder.stage(TaskStage.INDEXING.value, 0, 1)

        try:
            result = await controller.build_index(project_id)

        except BaseException as exc:
            await recorder.failed(exc)
            raise

        await recorder.succeeded(result)

        return result
    finally:
        try:
            if providers is not None:
                await providers.aclose_all()
        finally:
            await db.disconnect()


@celery_app.task(
    # bind=True for self.request.id — see process_data_task.
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.build_vector_index_task",
    # The index queue, not one of its own: no worker consumes a queue that is
    # not in a compose -Q list, and a task published to one hangs in QUEUED
    # with nothing to say so. See celery_queues.celery_queue_config.
    queue=SETTINGS.CELERY_QUEUE_INDEX,
)
def build_vector_index_task(self, project_id: str) -> dict:
    """Build the ANN index over a project's collection.

    The last link of the ingestion chain, and its own task rather than the tail
    of index_project_task so that the build is visible in Flower, gets its own
    task_executions row, and fails as itself: a build that timed out used to be
    reported as an indexing failure with every vector already written.
    """
    try:
        return asyncio.run(_run_build_index_task(project_id, task_id=self.request.id))

    except SoftTimeLimitExceeded as exc:
        logger.error(
            "build_vector_index_task for project %r exceeded its soft time limit of %ss",
            project_id,
            SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT,
        )
        raise CeleryTaskError(
            f"Building the vector index for {project_id!r} exceeded "
            f"{SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT}s and was stopped"
        ) from exc


def get_index_task(task_id: str) -> dict:
    """Return the current state and result metadata for an indexing task."""
    return task_status(task_id)
