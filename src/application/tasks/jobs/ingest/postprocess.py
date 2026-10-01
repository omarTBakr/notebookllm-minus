"""Repair one batch of parsed pages, and decide whether the document is done.

Stage three of four, and the only one with a queue of its own. It drains the
correction queue independently of the parsers: each message names a batch, this
loads it, sends its pages to the model, writes the corrected text back, and asks
whether it was the last.

The separate queue is not tidiness. This is the only stage that waits on a
*model* rather than a CPU -- minutes a batch against milliseconds to parse one
-- so behind an extraction on the shared queue one large document's corrections
would stall every other document's parsing.

`claim_collection` is what replaces the Celery chord that used to gather these.
The chord recorded one member of twenty-three and its callback never fired, so a
document whose every batch had succeeded produced no chunks at all. A row and an
atomic UPDATE cannot half-happen, survive a worker restart, and are unbothered
by a redelivered task.

Nothing here is fatal to the ingestion. A model that is down, slow, out of
context or bad at JSON leaves the pages with the text PyMuPDF extracted, which
was already readable -- that is the whole reason this pass is an improvement
rather than a dependency. See TextCorrectionService for the guard rails.
"""

import asyncio

from application.providers import ProviderCache
from application.services.ingest import TextCorrectionService
from celery_app import SETTINGS, celery_app
from data.models import IngestBatchModel, TaskModel
from data.models.ingest import IngestBatchStatus
from shared.enums import CeleryTaskFunction, TaskStage
from shared.utils import get_logger

from ...runtime import job_resources

logger = get_logger(__name__)


def _language_of(pages: list[dict]) -> str:
    """Which locale's prompts to use for this batch.

    Decided per batch from the text itself, using the same profiler the OCR
    path uses to judge a text layer. Arabic instructions for an Arabic page are
    not cosmetic: the single most-broken rule in this prompt is "do not
    translate", and an English instruction block wrapped around an Arabic page
    is itself a nudge towards answering in English.

    A batch is at most ten consecutive pages of one document, so it is nearly
    always all one language; the majority wins and the prompt is only a frame
    around text that carries its own language anyway.
    """
    from application.arabic_extraction.language import profile

    arabic = sum(1 for page in pages if page.get("text") and profile(page["text"]).is_arabic)

    return "ar" if arabic * 2 > len(pages) else "en"


async def _correct(pages: list[dict], settings) -> int:
    """Correct *pages* in place. Returns how many were replaced.

    Pages marked ``skip_correction`` are left alone: a source added from a link
    (an article's text, a video's transcript) has no broken text layer to
    repair. See `parse.parse_batch`.
    """
    pages = [page for page in pages if not page.get("skip_correction")]

    if not settings.POSTPROCESS_ENABLED or not pages:
        return 0

    providers = ProviderCache(settings)

    try:
        client = providers.chatting(
            settings.POSTPROCESS_MODEL_ID,
            num_ctx=settings.POSTPROCESS_NUM_CTX,
            # Off, and load-bearing rather than a preference. A reasoning model
            # asked to repair a page and return JSON reasons about it in prose
            # instead: measured on this corpus, 15-18k characters of scratchpad,
            # no JSON at all, three failed parses and a rejected page, in 429s.
            # The same call with the scratchpad off answers in 12s. Copy-typing
            # does not benefit from deliberation.
            thinking=False,
        )

        replacements = await TextCorrectionService(client, lang=_language_of(pages)).correct(pages)

    except Exception as exc:
        # The controller already swallows per-call failures; this catches the
        # ones before it -- an unreachable host, an unresolvable model id. Same
        # verdict: the pages keep their extracted text.
        logger.warning("Post-processing unavailable (%s); keeping the extracted text", exc)
        return 0

    finally:
        await providers.aclose_all()

    # Written alongside `text` rather than over it, so the collector can still
    # see both. `scale` is what maps a chunk's offsets in the corrected string
    # back onto word boxes measured against the original -- without it every
    # corrected page loses its citation highlight.
    for page in pages:
        replacement = replacements.get(page["page_index"])

        if replacement is None:
            continue

        page["corrected_text"] = replacement["text"]
        page["scale"] = replacement["scale"]

    return len(replacements)


async def _report(db, run, done: int) -> None:
    """Move the row the browser is polling one batch further along.

    Against the *planner's* row, not this task's: the route wrote one
    task_executions row per declared chain link before anything ran, and these
    batch tasks are not among them.

    `done` is a count of rows, which is what keeps it honest. The Redis counter
    this replaced was an unbounded INCR, and a single redelivered task pushed a
    progress bar to 104%.
    """
    if not run.parent_task_id:
        return

    try:
        await TaskModel(db).set_stage(run.parent_task_id, TaskStage.EXTRACTING.value, done, run.total_batches)
    except Exception as exc:
        # Bookkeeping must never fail the work it reports on.
        logger.warning("Could not report progress for %r: %s", run.parent_task_id, exc)


async def _run_postprocess(asset_id: str, batch_index: int) -> dict:
    async with job_resources() as job:
        batches = IngestBatchModel(job.db)

        batch = await batches.find_batch(asset_id, batch_index)
        run = await batches.find_run(asset_id)

        if batch is None or run is None:
            # The run was cleared underneath us -- a reset, or a redelivery
            # arriving after the document was already collected. Not an error.
            logger.info("Batch %d of %r is gone; nothing to correct", batch_index, asset_id)
            return {"asset_id": asset_id, "batch_index": batch_index, "corrected": 0}

        pages = batch.payload.get("pages", [])

        corrected = await _correct(pages, job.settings)

        batch.payload["pages"] = pages
        batch.status = IngestBatchStatus.CORRECTED
        await batches.save_batch(batch)

        done = await batches.count_corrected(asset_id)
        await _report(job.db, run, done)

        logger.info(
            "Corrected batch %d of %r: %d of %d page(s) replaced (%d/%d batches done)",
            batch_index,
            batch.asset_name,
            corrected,
            len(pages),
            done,
            run.total_batches,
        )

        # The last one out turns off the lights. Exactly one caller can win
        # this, however many finish together.
        claimed = await batches.claim_collection(asset_id)

    if claimed:
        from .assemble import assemble_chunks_task

        logger.info("All %d batch(es) of %r are in; collecting", run.total_batches, asset_id)
        assemble_chunks_task.apply_async(args=[asset_id])

    return {"asset_id": asset_id, "batch_index": batch_index, "corrected": corrected}


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.POSTPROCESS.value}",
    queue=SETTINGS.CELERY_QUEUE_POSTPROCESS,
)
def postprocess_batch_task(self, asset_id: str, batch_index: int) -> dict:
    """Correct one stored batch, and collect the document if it is the last."""
    return asyncio.run(_run_postprocess(asset_id, batch_index))
