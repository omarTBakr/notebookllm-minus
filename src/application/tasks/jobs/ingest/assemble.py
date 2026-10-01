"""Collect a document's corrected batches, chunk them, and index.

Stage four of four, and it runs once per asset: the batch that claimed the
collection dispatched it, and the claim admits exactly one winner.

Order is rebuilt from `page_index`, never from the order anything arrived in.
The batches are read back in `batch_index` order and their pages sorted again
inside, because a document silently assembled back-to-front is the kind of bug
that survives a long time -- every page is present and each one reads correctly.

When the chunks are stored the scratch rows go. They hold the full text of the
document plus every word box; leaving them would keep a second copy of every
book ever ingested.

The index stage is dispatched from here rather than chained behind the planner.
The planner returns as soon as the fan-out is published, so a chain hung off it
would have run while the pages were still being parsed.
"""

from pathlib import Path

from bson import ObjectId
from langchain_core.documents import Document  # ty: ignore[unresolved-import]

from application.services import ProcessService
from application.services.ingest import page_from_dict
from celery_app import SETTINGS, celery_app
from data.models import ChunkModel, DataChunk, IngestBatchModel, ProjectModel
from shared.enums import CeleryTaskFunction, ProcessStatus, TaskStage
from shared.utils import get_logger

from ...runtime import job_resources, run_job
from ...tracking.recorder import TaskRecorder

logger = get_logger(__name__)


def _documents(asset_name: str, pages: list[dict]) -> tuple[list[Document], dict, dict, dict]:
    """The pages of one asset as Documents, plus the state `split_file` needs.

    Returns the documents, the ``page_index -> PageWords`` map a highlight is
    computed from, the ``page_index -> scale`` map that rebases a corrected
    page's character offsets onto boxes measured against the original text,
    and the ``page_index -> timeline`` map of a video transcript's pages.
    """
    total = len(pages)

    layout: dict[int, object] = {}
    scales: dict[int, float] = {}
    timelines: dict[int, list] = {}
    documents: list[Document] = []

    for page in pages:
        index = page["page_index"]

        layout[index] = page_from_dict(page)

        # A video transcript: which stretch of video each character came from.
        if page.get("timeline"):
            timelines[index] = page["timeline"]

        # Present only when a correction was accepted -- see
        # TextCorrectionService._accept for what "accepted" means. A page
        # that kept its extracted text needs no rebasing: its offsets index the
        # very string the boxes were measured against.
        if "corrected_text" in page:
            scales[index] = page["scale"]

        documents.append(
            Document(
                page_content=page.get("corrected_text", page["text"]),
                metadata={
                    "source": asset_name,
                    "page": index,
                    "page_label": page["page_label"],
                    "total_pages": total,
                },
            )
        )

    return documents, layout, scales, timelines


async def assemble_chunks(asset_id: str, db, recorder=None) -> dict:
    """Chunk and store one asset's collected batches, against an open *db*.

    Split from the task like every other stage, so the whole pipeline can be
    driven in-process by a test with no broker and no worker. Every recorder
    call is a no-op when absent.
    """

    async def stage(name: str, done: int = 0, total: int = 0) -> None:
        if recorder is not None:
            await recorder.stage(name, done, total)

    batches = IngestBatchModel(db)

    run = await batches.find_run(asset_id)

    if run is None:
        logger.warning("No ingest run for %r; nothing to collect", asset_id)
        return {"asset_id": asset_id, "status": "missing", "chunks_saved": 0}

    pages: list[dict] = []
    asset_name = ""

    async for batch in batches.iter_batches(asset_id):
        asset_name = batch.asset_name or asset_name
        pages.extend(batch.payload.get("pages", []))

    # Sorted again: batch_index order puts the batches in sequence, and this
    # puts the pages in sequence within them. Cheap, and the alternative is a
    # document that reads correctly page by page and is in the wrong order.
    pages.sort(key=lambda page: page["page_index"])

    request_data = run.request_data or {}

    controller = ProcessService(
        request_data.get("chunk_size") or 1000,
        request_data.get("overlap_size") if request_data.get("overlap_size") is not None else 200,
    )

    documents, layout, scales, timelines = _documents(asset_name, pages)

    # split_file reads both off the instance -- the same contract the
    # single-process path uses, where _process_pdf_with_layout fills them in.
    # Setting them here is what keeps the highlight machinery working across
    # the queue hop.
    controller._pdf_pages = layout
    controller._text_scale = scales
    controller._timelines = timelines

    # Normally done by process_file, which a batched pymupdf PDF does not go
    # through. The stores reject text the extractor can emit, so it has to
    # happen before the splitter sees it. Idempotent, so the whole-file route
    # having already sanitised costs nothing.
    controller.text.sanitize(documents, source=asset_name)

    await stage(TaskStage.CHUNKING.value, run.total_batches, run.total_batches)

    chunked = controller.split_file(documents, extension=Path(asset_name).suffix.lower())

    object_id = ObjectId(run.project_object_id) if run.project_object_id else ObjectId()

    chunks = [
        DataChunk(
            project_id=object_id,
            asset_id=asset_id,
            chunk_order=order,
            chunk_content=doc.page_content,
            chunk_metadata=doc.metadata,
        )
        for order, doc in enumerate(chunked)
    ]

    await stage(TaskStage.STORING.value, run.total_batches, run.total_batches)

    inserted = await ChunkModel(db).create_chunks(chunks)
    await ProjectModel(db).add_chunk_ids(run.project_id, inserted)

    # Only once the chunks are safely stored. A crash before this leaves the
    # batches in place, which is recoverable; deleting first would not be.
    await batches.clear_run(asset_id)

    corrected = sum(1 for page in pages if "corrected_text" in page)

    logger.info(
        "Assembled %r: %d page(s), %d corrected, %d chunk(s)",
        asset_name,
        len(pages),
        corrected,
        len(inserted),
    )

    return {
        "project_id": run.project_id,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "status": ProcessStatus.PROCESSING_SUCCESS.value,
        "pages": len(pages),
        "pages_corrected": corrected,
        "chunks_created": len(chunked),
        "chunks_saved": len(inserted),
        "index_task_id": run.index_task_id,
        "build_task_id": run.build_task_id,
        "parent_task_id": run.parent_task_id,
    }


async def _run_assemble(asset_id: str) -> dict:
    """Own the connection and the bookkeeping around `assemble_chunks`."""
    async with job_resources() as job:
        run = await IngestBatchModel(job.db).find_run(asset_id)
        parent = run.parent_task_id if run else None

        recorder = TaskRecorder(job.db, parent, counts_ingest=True)

        try:
            result = await assemble_chunks(asset_id, job.db, recorder=recorder)
        except BaseException as exc:
            await recorder.failed(exc)
            raise

        await recorder.succeeded(result)

        return result


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.ASSEMBLE.value}",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def assemble_chunks_task(self, asset_id: str) -> dict:
    """Collect, chunk, store, then publish the indexing stage."""
    result = run_job(lambda: _run_assemble(asset_id), what=f"Assembling {asset_id!r}")

    if result.get("chunks_saved"):
        from ..index import build_vector_index_task, index_project_task

        # Published with the ids generated when the upload was accepted, so the
        # task_executions rows the browser has been polling since then are the
        # ones these tasks fill in -- rather than two new ids nobody was told
        # about, leaving the originals QUEUED for ever.
        index_project_task.apply_async(
            args=[result["project_id"], result["asset_id"], False, None],
            task_id=result["index_task_id"] or None,
            link=build_vector_index_task.si(result["project_id"]).set(task_id=result["build_task_id"] or None),
        )

        # After indexing is queued, never in front of it: summaries are for
        # Studio, and a document is searchable without them.
        from .summarise import publish_summaries

        publish_summaries(result["project_id"], result["asset_id"], result["chunks_saved"])

    return result
