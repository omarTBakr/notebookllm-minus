"""Extract one batch of pages, and hand it to the correction queue.

Stage two of four. The planner published one of these per page range; each
extracts its own range, stores the parsed pages, and puts a *reference* on the
correction queue for the LLM stage to pick up.

The reference is the point. A batch of ten pages carries every word and every
bounding box of those pages -- what a citation highlight is later drawn from --
which is megabytes. Putting that on the broker would make RabbitMQ a file
server, and would park a copy in the result backend besides, where a 512MB cap
with LRU eviction is waiting for it. So the payload goes to a table and the
message carries `(asset_id, batch_index)`.

Why a batch rather than the whole document, when `extract_pages` already knows
how to split one across a process pool: that pool does not run in production. A
Celery prefork worker is daemonic and may not have children, so `extract_pages`
detects it and degrades to serial -- ~520s for a 274-page document, most of the
soft time limit, for one upload. Splitting across Celery *tasks* makes the
worker the unit of concurrency, which is a unit that exists here.
"""

import asyncio
import tempfile
from pathlib import Path

from application.services import ProcessService
from application.services.ingest import (
    extract_page_range,
    page_to_dict,
    transcript_page,
)
from celery_app import SETTINGS, celery_app
from data.models import AssetModel, IngestBatch, IngestBatchModel
from data.models.ingest import IngestBatchStatus
from shared.enums import AssetType, CeleryTaskFunction
from shared.exceptions import ExtractionError, InvalidInputError
from shared.utils import get_logger

from ...runtime import job_resources

logger = get_logger(__name__)


# One cached temp file per worker process, keyed by asset. A document is
# normally 10-30 batches and a prefork worker takes them one after another, so
# without this the same 50 MB blob is read out of Postgres and written to /tmp
# once per batch. Only the most recent is kept: batches for one asset arrive
# together, and holding more would mean a cache eviction policy for something
# that only ever needs a window of one.
_CACHED: dict[str, object] = {"asset_id": None, "path": None}


def _cache_file(asset_id: str, file_bytes: bytes, filename: str) -> Path:
    """The on-disk path for *asset_id*, writing it out if this worker lacks it."""
    if _CACHED["asset_id"] == asset_id:
        path = _CACHED["path"]
        if isinstance(path, Path) and path.exists():
            return path

    previous = _CACHED["path"]
    if isinstance(previous, Path):
        previous.unlink(missing_ok=True)

    suffix = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        path = Path(tmp.name)

    _CACHED["asset_id"] = asset_id
    _CACHED["path"] = path

    return path


def _whole_file(file_bytes: bytes, filename: str) -> list[dict]:
    """One asset that is not a batchable PDF, as page-shaped data.

    A text file, or a PDF on a loader other than pymupdf. Neither has word
    geometry, so the box fields come back empty and every page reports no
    highlight -- `rects_for_range` finds no words, `highlight_metadata` returns
    None, and `split_file` already treats a missing highlight as "nothing to
    draw". That is the honest answer: those loaders genuinely do not know where
    on the paper anything sat.

    Shaped like a parsed page anyway so there is one pipeline rather than two.
    """
    docs = ProcessService().process_bytes(file_bytes, filename)

    return [
        {
            "page_index": index,
            "page_label": str(doc.metadata.get("page_label") or index + 1),
            "width": 0.0,
            "height": 0.0,
            "text": doc.page_content,
            "starts": [],
            "words": [],
            "boxes": [],
        }
        for index, doc in enumerate(docs)
    ]


def _fill_unreadable_pages(path: Path, start: int, end: int, pages: list[dict]) -> None:
    """Put OCR text on the pages of this batch that have no usable text layer.

    In place, on the parsed page dicts. Here rather than in the correction
    stage because this is where the file is on disk, and before it because the
    model should get to repair OCR's mistakes like any other page's.
    """
    ocr_text = ProcessService().ocr_unreadable_pages(path, start, end)

    for page in pages:
        if page["page_index"] in ocr_text:
            page["text"] = ocr_text[page["page_index"]]
            # The boxes described the old text -- none at all, or glyph
            # garbage -- and OCR returns no positions, so they go rather than
            # point a highlight at the wrong words. Width and height stay:
            # with no boxes and a known size, `highlight_metadata` marks the
            # whole page instead.
            page["starts"] = []
            page["words"] = []
            page["boxes"] = []


async def parse_batch(project_id: str, asset_id: str, start, end, batch_index: int, db) -> int:
    """Extract pages ``[start, end)`` and store them. Returns the page count.

    Split from the task for the same reason `plan_ingestion` is: it is the body
    of the job, and a test should be able to run it without a broker.
    """
    asset = await AssetModel(db).get_asset(asset_id)

    if asset is None:
        raise InvalidInputError(f"Asset {asset_id!r} no longer exists")

    if asset.project_id != project_id:
        raise InvalidInputError(f"Asset {asset_id!r} does not belong to project {project_id!r}")

    if not asset.file_bytes:
        raise InvalidInputError(f"Asset {asset_id!r} has no stored file content to parse")

    path = _cache_file(asset_id, asset.file_bytes, asset.name)

    if start is None:
        if asset.asset_type == AssetType.YOUTUBE:
            pages = [transcript_page(asset.file_bytes)]
        else:
            pages = _whole_file(asset.file_bytes, asset.name)

        if asset.source_url:
            # Added from a link: an article's extracted text or a transcript,
            # neither of which has the broken text layer the repair pass is
            # for. As one whole-document "page" it would only fail that pass's
            # length guard after a long call.
            for page in pages:
                page["skip_correction"] = True

        logger.info("Parsed %r whole (%d page(s), no word boxes)", asset.name, len(pages))
    else:
        try:
            extracted = extract_page_range(path, start, end)
        except Exception as exc:
            raise ExtractionError(f"Could not extract pages {start}-{end} of {asset.name!r}") from exc

        pages = [page_to_dict(page) for page in extracted]
        logger.info("Parsed pages %d-%d of %r (%d page(s))", start, end - 1, asset.name, len(pages))

        _fill_unreadable_pages(path, start, end, pages)

    await IngestBatchModel(db).save_batch(
        IngestBatch(
            asset_id=asset_id,
            project_id=project_id,
            batch_index=batch_index,
            status=IngestBatchStatus.PARSED,
            asset_name=asset.name,
            payload={"pages": pages},
        )
    )

    return len(pages)


async def _run_parse_batch(project_id: str, asset_id: str, start, end, batch_index: int) -> dict:
    """Own the connection around `parse_batch`."""
    async with job_resources() as job:
        pages = await parse_batch(project_id, asset_id, start, end, batch_index, job.db)

    return {"asset_id": asset_id, "batch_index": batch_index, "pages": pages}


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PARSE.value}",
    queue=SETTINGS.CELERY_QUEUE_PROCESS,
)
def parse_batch_task(self, project_id: str, asset_id: str, start, end, batch_index: int) -> dict:
    """Extract one batch, store it, and queue it for correction."""
    # Imported here, not at module scope: postprocess imports nothing from this
    # module, but the pair is easier to reason about when the dependency runs
    # in one direction only.
    from .postprocess import postprocess_batch_task

    result = asyncio.run(_run_parse_batch(project_id, asset_id, start, end, batch_index))

    # Published after the payload is committed, never before. The correction
    # stage reads the row this just wrote, and a message that overtook its own
    # data would find nothing there.
    postprocess_batch_task.apply_async(args=[asset_id, batch_index])

    return result
