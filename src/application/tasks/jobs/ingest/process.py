"""Plan a document's ingestion, and publish its batches.

Stage one of four. It decides *what* to extract -- which assets, which page
ranges, which are already chunked and can be skipped -- records the run, puts
one parse message on the queue per batch, and returns.

It used to replace itself with a chord over those batches. That is gone: on
Celery 5.6.3 `self.replace(chord(group(chain(parse, postprocess)), assemble))`
marked one of twenty-three chain-tails as a chord member, the accumulator
reached 1, the callback never fired, and a document whose every batch had
succeeded produced no chunks at all. Completion lives in `ingest_runs` now, and
the last batch to finish dispatches the collector.

Three things still make the batching worth it.

*Parallelism.* `extract_pages` splits a document across a process pool, but a
Celery prefork worker is daemonic and may not have children, so in production it
silently degrades to serial -- ~520s for a 274-page document, most of the soft
time limit, for one upload. Batching across Celery tasks makes the worker the
unit of concurrency, which is a unit that exists here.

*The correction pass.* Repairing a page with a model takes minutes a batch. It
has to run on its own queue, at its own pace, without holding a parsing slot.

*Blast radius.* A batch is what gets retried, and what a failure costs.
"""

from pathlib import Path

from application.services.ingest import page_count
from celery_app import SETTINGS, celery_app
from data.models import (
    AssetModel,
    ChunkModel,
    IngestBatchModel,
    IngestRun,
    Project,
    ProjectModel,
)
from shared.enums import AssetType, FileExtension, PdfLoader, ProcessStatus, TaskStage
from shared.exceptions import InvalidInputError, ProjectNotFoundError
from shared.utils import get_logger, get_settings

from ...runtime import job_resources, run_job
from ...tracking.recorder import TaskRecorder, downstream_ids
from ...tracking.status import task_status
from .parse import _cache_file, parse_batch_task

logger = get_logger(__name__)


def _batches_for(asset, settings) -> list[tuple[int | None, int | None]]:
    """The page ranges one asset is split into.

    ``(None, None)`` means "the whole file, through the ordinary loader" -- a
    text file, or a PDF on a loader other than pymupdf. Those have no page
    geometry to batch by and no word boxes to preserve, so they stay one unit
    and take the same route through the queue as everything else rather than
    needing a second pipeline beside it.

    The ranges tile the document exactly: no gap, no overlap. Both failures are
    invisible downstream -- a gap is a page missing from the notebook and an
    overlap is a page chunked twice, and the ingest reports success either way.
    """
    suffix = Path(asset.name).suffix.lower()

    # By type first: a YouTube transcript is named after its video, and a
    # title can end in ".pdf" as easily as anything else.
    if getattr(asset, "asset_type", None) == AssetType.YOUTUBE:
        return [(None, None)]

    if suffix != FileExtension.PDF or settings.PDF_LOADER != PdfLoader.PYMUPDF:
        return [(None, None)]

    path = _cache_file(asset.asset_id, asset.file_bytes, asset.name)

    total = page_count(path)

    if total == 0:
        return [(None, None)]

    size = max(1, settings.PDF_BATCH_PAGES)

    return [(start, min(start + size, total)) for start in range(0, total, size)]


async def plan_ingestion(project_id: str, request_data: dict, db, recorder=None) -> dict:
    """Resolve the assets, apply reset/skip, and return the batch plan.

    Takes an already-connected *db*, the same split `process_service` had and
    for the same reason: this is the body of the planning job, callable directly
    from a test with no broker and no worker.

    Reset and the already-chunked skip are decided *here*, before anything is
    parsed, rather than per asset once the pages are in hand. Re-uploading a
    document that is already chunked should cost nothing, and under the old
    shape it cost a full extraction before the skip was noticed.
    """
    settings = get_settings()

    asset_model = AssetModel(db)
    project_model = ProjectModel(db)
    chunk_model = ChunkModel(db)

    asset_id = request_data.get("asset_id")

    if asset_id is not None:
        assets = [await asset_model.get_asset(asset_id)]
    else:
        assets = [item async for item in asset_model.iter_project_assets(project_id)]

    if not assets or assets[0] is None:
        raise ProjectNotFoundError(f"Project {project_id!r} has no assets to process")

    for asset in assets:
        if asset.project_id != project_id:
            raise InvalidInputError(f"Asset {asset.asset_id!r} does not belong to project {project_id!r}")
        if not asset.file_bytes:
            raise InvalidInputError(f"Asset {asset.asset_id!r} has no stored file content to process")

    project = Project(
        project_id=project_id,
        name=assets[0].name,
        description=f"Project {project_id}",
    )
    project_object_id = await project_model.update_project(project)

    plan: list[dict] = []
    skipped: list[dict] = []

    for asset in assets:
        if request_data.get("reset"):
            removed = await chunk_model.delete_chunks_for_asset(project_object_id, asset.asset_id)
            await project_model.remove_chunk_ids(project_id, removed)

            if removed:
                logger.info("Reset asset %r: removed %d existing chunk(s)", asset.asset_id, len(removed))

        elif await chunk_model.has_asset_chunks(project_object_id, asset.asset_id):
            logger.info("Skipping asset %r (%s): already chunked", asset.asset_id, asset.name)
            skipped.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_name": asset.name,
                    "project_object_id": str(project_object_id),
                    "status": "skipped",
                    "reason": "already chunked; pass reset=true to re-ingest",
                    "chunks_created": 0,
                    "chunks_saved": 0,
                }
            )
            continue

        ranges = _batches_for(asset, settings)

        # The run is written before a single batch is published. It is the
        # denominator the progress poll reads, the arguments the collector
        # chunks with, and the row whose `collected_at` decides which
        # finishing batch gets to collect -- all of which have to exist
        # before anything can finish.
        await IngestBatchModel(db).start_run(
            IngestRun(
                asset_id=asset.asset_id,
                project_id=project_id,
                total_batches=len(ranges),
                request_data=request_data,
                project_object_id=str(project_object_id),
                parent_task_id=request_data.get("parent_task_id", ""),
                index_task_id=request_data.get("index_task_id", ""),
                build_task_id=request_data.get("build_task_id", ""),
            )
        )

        for index, (start, end) in enumerate(ranges):
            plan.append(
                {
                    "asset_id": asset.asset_id,
                    "start": start,
                    "end": end,
                    "batch_index": index,
                }
            )

    if plan:
        logger.info(
            "Planned %d batch(es) of at most %d page(s) for project %r",
            len(plan),
            settings.PDF_BATCH_PAGES,
            project_id,
        )

    return {
        "project_object_id": str(project_object_id),
        "batches": plan,
        # Only read when there is nothing to fan out; otherwise `assemble`
        # builds the result from what it actually stored.
        "result": {
            "project_id": project_id,
            "chunk_size": request_data.get("chunk_size"),
            "overlap_size": request_data.get("overlap_size"),
            "status": ProcessStatus.PROCESSING_SUCCESS.value,
            "reset": request_data.get("reset", False),
            "assets_found": len(assets),
            "assets_processed": 0,
            "assets_skipped": len(skipped),
            "results": skipped,
        },
    }


async def _run_plan(
    project_id: str,
    request_data: dict,
    task_id: str | None = None,
    downstream: list[str] | None = None,
) -> dict:
    """Own the connection and the bookkeeping around `plan_ingestion`."""
    async with job_resources() as job:
        recorder = TaskRecorder(job.db, task_id)
        await recorder.started()

        try:
            plan = await plan_ingestion(project_id, request_data, job.db, recorder=recorder)

            # Report the stage before the fan-out is even published, so the
            # browser has something other than "Queued" the moment it starts
            # polling -- and a real denominator, since the batch count is known
            # here and nowhere earlier.
            if plan["batches"]:
                await recorder.stage(TaskStage.EXTRACTING.value, 0, len(plan["batches"]))

            return plan

        except BaseException as exc:
            # BaseException, not Exception: SoftTimeLimitExceeded inherits from
            # it, and a task killed on the time limit is exactly the one whose
            # failure most needs recording.
            await recorder.failed(exc)
            await recorder.abandon(
                downstream or [],
                f"cancelled: {type(exc).__name__} in {project_id!r} ingestion",
            )
            raise


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.process_data_task",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def process_data_task(self, project_id: str, request_data: dict) -> dict:
    """Plan the ingestion and publish its batches.

    This task used to replace itself with a chord over the batches. It does not
    any more: `self.replace(chord(group(chain(...)), callback))` marked one of
    twenty-three chain-tails as a chord member on Celery 5.6.3, so the
    accumulator reached 1, the callback never fired, and a document whose every
    batch had succeeded produced no chunks at all -- silently.

    So the fan-out is published and this returns. Completion is tracked in
    `ingest_runs` instead, and the last batch to finish dispatches the
    collector. Nothing downstream depends on this task's return value or on a
    chain hanging off it.
    """
    plan = run_job(
        lambda: _run_plan(
            project_id,
            request_data,
            task_id=self.request.id,
            downstream=downstream_ids(self.request),
        ),
        what=f"Document processing for {project_id!r}",
    )

    for batch in plan["batches"]:
        parse_batch_task.apply_async(
            args=[
                project_id,
                batch["asset_id"],
                batch["start"],
                batch["end"],
                batch["batch_index"],
            ]
        )

    return plan["result"] | {"batches_published": len(plan["batches"])}


def get_process_task(task_id: str) -> dict:
    """Return the current state and result metadata for an ingestion task."""
    return task_status(task_id)
