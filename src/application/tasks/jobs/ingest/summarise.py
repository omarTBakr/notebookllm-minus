"""Summarise a newly stored document's chunks, one batch per message.

Runs after `assemble`, beside indexing rather than in front of it: the document
is searchable as soon as it always was, and the summaries fill in behind. They
are what Studio generates from -- a whole book does not fit in a context
window, its one-line summaries do -- so doing them here means the first
flashcards of a new notebook appear without a summarising pass in front of
them. Studio still summarises whatever is missing, so a batch that fails here
costs time later, never a card.

Summaries and not corrections in one call, on purpose. Correction works on
pages before chunking and is forbidden to summarise (its length guard rejects
anything that shrinks); summaries belong to chunks, which only exist after
chunking, and use the notebook's own model rather than the fixed correction
model. Asking one call for both would reintroduce the failure the correction
prompt exists to prevent.

One message per `SUMMARY_BATCH` chunks rather than one task for the document,
for the same reasons as the page batches upstream: a 222-page book is ~70
calls, which as one task would run up against the soft time limit and hold a
worker for the whole of it; as separate messages each takes seconds,
interleaves fairly with other documents' corrections on the same queue, and a
failure costs ten summaries.
"""

from application.prompts.template_parser import TemplateParser
from application.services import SUMMARY_BATCH, summarise_chunks
from celery_app import SETTINGS, celery_app
from data.models import ChatModel, ChunkModel
from shared.enums import CeleryTaskFunction
from shared.exceptions import ChatNotFoundError
from shared.utils import get_logger

from ...runtime import job_resources, run_job

logger = get_logger(__name__)


def publish_summaries(project_id: str, asset_id: str, chunk_count: int) -> int:
    """Queue one summarising message per batch of chunks. Returns how many."""
    if not SETTINGS.INGEST_SUMMARISE or chunk_count <= 0:
        return 0

    ranges = [(start, min(start + SUMMARY_BATCH, chunk_count)) for start in range(0, chunk_count, SUMMARY_BATCH)]

    for start, end in ranges:
        summarise_chunks_task.apply_async(args=[project_id, asset_id, start, end])

    return len(ranges)


async def summarise_range(project_id: str, asset_id: str, start: int, end: int, db, providers, settings) -> int:
    """Summarise chunks ``[start, end)`` of one asset. Returns how many.

    Chunks that already have a summary are skipped, so a redelivered message,
    or a Studio run that got there first, costs nothing. Every failure is a
    warning and a zero: nothing waits on this, and Studio fills any gap.
    """
    chunks = await ChunkModel(db).get_chunks_by_orders(asset_id, list(range(start, end)))
    pending = [chunks[order] for order in sorted(chunks) if not chunks[order].summary]

    if not pending:
        return 0

    try:
        chat = await ChatModel(db).get_chat(project_id)
    except ChatNotFoundError:
        # /process and /data create projects with no chat: default model.
        chat = None

    client = providers.chatting(getattr(chat, "generation_model", None))
    parser = TemplateParser(
        lang=getattr(chat, "lang", settings.DEFAULT_LANG),
        default_lang=settings.DEFAULT_LANG,
    )

    try:
        summaries = await summarise_chunks(client, parser, pending)
    except Exception as exc:  # noqa: BLE001 - Studio summarises what is left
        logger.warning(
            "Could not summarise chunks %d-%d of %r (%s); Studio will retry them",
            start,
            end - 1,
            asset_id,
            exc,
        )
        return 0

    if summaries:
        await ChunkModel(db).set_chunk_summaries(summaries)

    if len(summaries) < len(pending):
        logger.warning(
            "Model summarised %d of %d chunk(s) in %d-%d of %r; " "the rest stay unsummarised for Studio",
            len(summaries),
            len(pending),
            start,
            end - 1,
            asset_id,
        )

    return len(summaries)


async def _run_summarise(project_id: str, asset_id: str, start: int, end: int) -> dict:
    async with job_resources(providers=True) as job:
        done = await summarise_range(project_id, asset_id, start, end, job.db, job.providers, job.settings)

    logger.info("Summarised %d chunk(s) in %d-%d of %r", done, start, end - 1, asset_id)

    return {"asset_id": asset_id, "start": start, "end": end, "summarised": done}


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.SUMMARISE.value}",
    # The correction queue: the other stage that waits on a model rather than
    # a CPU, and one a worker already consumes -- a queue of its own would need
    # a -Q consumer in compose or its messages would sit there for ever.
    queue=SETTINGS.CELERY_QUEUE_POSTPROCESS,
)
def summarise_chunks_task(self, project_id: str, asset_id: str, start: int, end: int) -> dict:
    """Summarise one batch of a stored document's chunks."""
    return run_job(
        lambda: _run_summarise(project_id, asset_id, start, end),
        what=f"Summarising chunks {start}-{end - 1} of {asset_id!r}",
    )
