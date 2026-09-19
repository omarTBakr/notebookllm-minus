"""Generating study material from a notebook, a batch at a time.

The shape of this task is set by one number: a 222-page book is 694 chunks and
~470k characters, so the whole notebook does not fit in a context window. The
same book as one-line summaries is ~35k tokens, which does. So the model is
given summaries and never the raw text, and each item carries the `chunk_order`
of the summary it came from — the only thread back to a real page, and what
makes a generated card checkable against the document.

It is written as one streaming loop rather than summarise-everything-then-
generate, because the second shape costs two to four minutes of blank screen
before anything appears. Here the first batch produces cards in seconds and the
set fills while they are being read. Items are appended in the database, so the
browser sees them on its next poll.

Summaries are written back onto the chunks, which makes the whole thing
resumable twice over: a second run over the same notebook does no summarising
at all, and a worker killed mid-run loses only the batch in flight. That
mattered enough over the last few days to design for.
"""

import uuid

from celery_app import SETTINGS, celery_app
from controllers import (
    FlashcardController,
    MindMapController,
    QuizController,
    generate_structured,
)
from enums import ArtifactKind, ArtifactStatus, TaskStage
from exceptions import CeleryTaskError, StructuredOutputError
from models import ArtifactModel, ChatModel, ChunkModel, ProjectModel
from models.db_schema import Artifact, SummarySet
from templates.template_parser import TemplateParser
from utils import get_logger

from .recorder import TaskRecorder
from .runtime import job_resources, run_job

logger = get_logger(__name__)

#: Chunks summarised per model call. Ten turns 694 calls into 70, which is the
#: difference between a job that finishes and one nobody waits for. Larger
#: batches summarise more cheaply but delay the first card, which is the number
#: this design exists to protect.
SUMMARY_BATCH = 10

#: ...except for the very first batch, which is deliberately tiny.
#:
#: The whole point of streaming is that something is on screen in seconds, and
#: a uniform batch of ten does not deliver that: the first card costs ten
#: summaries *and* a generation call before anything appears, which on a real
#: notebook is most of a minute staring at "reading the documents...". Three
#: chunks is one quick summarising call and enough material for the first few
#: cards; by the time those have been read the full-size batches behind them
#: have landed. Ten stays the steady-state size, because past the first
#: screenful throughput matters more than latency.
FIRST_BATCH = 3

#: Which controller generates which tile. Adding a Studio tile that generates
#: something is an entry here and a controller, not another branch in this
#: module: the batching, summarising and resumability below are the same
#: whatever is being made.
_CONTROLLERS = {
    ArtifactKind.FLASHCARDS: FlashcardController,
    ArtifactKind.QUIZ: QuizController,
    ArtifactKind.MIND_MAP: MindMapController,
}


async def _summarise(client, parser, batch) -> dict[str, str]:
    """One model call for a batch of chunks, returning summaries by row id.

    Structured, not prose split on newlines. Splitting was the obvious thing
    and it was wrong: a small model answering a ten-excerpt batch with a single
    line had that line assigned to every chunk in the batch, so nine of them
    carried a summary describing a different passage -- and every card built
    from those cited the wrong chunk. Found by running it, not by testing it;
    the fake in the unit tests politely returned one line per excerpt.

    Numbered 1..N within the batch, matching `ArtifactController.generate`,
    so a summary is
    tied to a chunk by a number the model was actually shown. A chunk the model
    skips is simply left unsummarised: it has no summary rather than someone
    else's, and the next run picks it up.
    """
    numbered = list(enumerate(batch, start=1))

    excerpts = "\n\n".join(
        parser.get(
            "studio",
            "excerpt_prompt",
            {"num": num, "content": chunk.chunk_content[:4000]},
        )
        for num, chunk in numbered
    )

    prompt = parser.get("studio", "summarise_prompt", {"excerpts": excerpts})
    result = await generate_structured(client, prompt, SummarySet)

    by_number = dict(numbered)
    summaries: dict[str, str] = {}

    for entry in result.summaries:
        chunk = by_number.get(entry.num)

        if chunk is not None:
            summaries[str(chunk.id)] = entry.summary

    return summaries


async def _finalize(controller, client, parser, artifacts, chat_id: str, kind: ArtifactKind) -> None:
    """Give the controller its one look at the finished set.

    A failure here keeps the set as the batches left it rather than failing the
    run: an ungrouped mind map still has every topic and every citation, which
    is worth more than an error.
    """
    current = await artifacts.find_artifact(chat_id, kind.value)

    if current is None or not current.items:
        return

    try:
        replacement = await controller.finalize(client, parser, current.items)
    except StructuredOutputError as exc:
        logger.warning("Final pass for %s on %r failed; keeping the items as generated: %s", kind.value, chat_id, exc)
        return

    if replacement is not None:
        await artifacts.replace_items(current.artifact_id, replacement)


async def _run_generation(chat_id: str, kind: str, task_id: str | None = None) -> dict:
    artifact_kind = ArtifactKind(kind)

    async with job_resources(providers=True) as job:
        settings, db, providers = job.settings, job.db, job.providers

        try:
            recorder = TaskRecorder(db, task_id)
            await recorder.started()

            chat = await ChatModel(db).get_chat(chat_id)
            client = providers.chatting(getattr(chat, "generation_model", None))
            parser = TemplateParser(
                lang=getattr(chat, "lang", settings.DEFAULT_LANG),
                default_lang=settings.DEFAULT_LANG,
            )

            # chunks.project_id is the project row's ObjectId, not the chat id --
            # the identifier mismatch this codebase has already been bitten by.
            project = await ProjectModel(db).get_project(chat_id)
            chunk_model = ChunkModel(db)
            total = await chunk_model.count_project_chunks(project.id)

            if not total:
                raise ValueError(f"Notebook {chat_id!r} has no chunks to generate from")

            controller = _CONTROLLERS[artifact_kind]()
            artifacts = ArtifactModel(db)

            # The route creates the row before queueing, so a browser polling
            # straight after the 202 always finds something. Adopt it rather than
            # creating a second one, which would reset the items and change the id
            # underneath whoever is already reading.
            existing = await artifacts.find_artifact(chat_id, artifact_kind.value)
            artifact_id = existing.artifact_id if existing else str(uuid.uuid4())

            # Written unconditionally, not only when absent. Creating upserts on
            # (chat_id, kind) and clears the items, so this both adopts the row the
            # route made -- stamping it with the task actually doing the work -- and
            # resets a previous deck when the task is driven directly. Appending to
            # an inherited set would show old cards among new ones with no way to
            # tell them apart.
            await artifacts.create_artifact(
                Artifact(
                    artifact_id=artifact_id,
                    chat_id=chat_id,
                    kind=artifact_kind,
                    status=ArtifactStatus.GENERATING,
                    source_task_id=task_id or "",
                )
            )

            produced = 0
            seen = 0
            summarised_calls = 0
            batch: list = []

            async def flush(batch):
                nonlocal produced, seen, summarised_calls

                # Only chunks that have never been summarised cost a call. A second
                # run over the same notebook does none at all.
                pending = [c for c in batch if not c.summary]

                if pending:
                    await recorder.stage(TaskStage.SUMMARISING.value, seen, total)
                    fresh = await _summarise(client, parser, pending)
                    await chunk_model.set_chunk_summaries(fresh)
                    summarised_calls += 1

                    missing = len(pending) - len(fresh)

                    if missing:
                        # Left unsummarised on purpose, so a later run retries them.
                        # Worth a line: silently summarising 6 of 10 chunks every
                        # batch would halve the deck with nothing to show for it.
                        logger.warning(
                            "Model summarised %d of %d chunk(s); the rest stay unsummarised for a later run",
                            len(fresh),
                            len(pending),
                        )

                    for chunk in batch:
                        if not chunk.summary:
                            chunk.summary = fresh.get(str(chunk.id), "")

                usable = [c for c in batch if c.summary]

                if not usable:
                    return

                await recorder.stage(TaskStage.GENERATING.value, seen, total)
                items = await controller.generate(client, parser, usable)

                if items:
                    produced = await artifacts.append_items(artifact_id, items)

            # Small first, then full size: see FIRST_BATCH.
            target = min(FIRST_BATCH, total)

            async for chunk in chunk_model.iter_project_chunks(project.id):
                batch.append(chunk)
                seen += 1

                if len(batch) >= target:
                    await flush(batch)
                    batch = []
                    target = SUMMARY_BATCH

            if batch:
                await flush(batch)

            await _finalize(controller, client, parser, artifacts, chat_id, artifact_kind)

            await artifacts.finish_artifact(artifact_id, ArtifactStatus.COMPLETE.value)
            await recorder.stage(TaskStage.GENERATING.value, total, total)

            result = {
                "chat_id": chat_id,
                "kind": kind,
                "artifact_id": artifact_id,
                "items": produced,
                "chunks_seen": total,
                "summarising_calls": summarised_calls,
            }
            await recorder.succeeded(result)

            logger.info(
                "Generated %d %s item(s) for notebook %r from %d chunk(s), %d summarising call(s)",
                produced,
                kind,
                chat_id,
                total,
                summarised_calls,
            )

            return result

        except BaseException as exc:
            # A failed run keeps whatever it produced: a deck of twelve cards that
            # stopped early is worth more than an error page, and the status is
            # what lets the UI say which it is.
            try:
                existing = await ArtifactModel(db).find_artifact(chat_id, kind)

                if existing is not None:
                    await ArtifactModel(db).finish_artifact(
                        existing.artifact_id, ArtifactStatus.FAILED.value, str(exc)[:500]
                    )
            except Exception:  # noqa: BLE001 - never mask the original failure
                logger.warning("Could not mark the artifact failed", exc_info=True)

            await recorder.failed(exc)
            raise


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.generate_artifact_task",
    queue=SETTINGS.CELERY_QUEUE_STUDIO,
)
def generate_artifact_task(self, chat_id: str, kind: str) -> dict:
    """Generate a flashcard deck, quiz or mind map for one notebook."""
    try:
        return run_job(
            lambda: _run_generation(chat_id, kind, task_id=self.request.id),
            what=f"Generating {kind} for {chat_id!r}",
        )

    except StructuredOutputError as exc:
        # The model would not return the schema even after retries. Distinct
        # from a timeout or a broker fault: nothing here will fix itself on a
        # retry, so it is reported rather than raised as an infrastructure
        # failure.
        logger.error("Model would not return valid %s for %r: %s", kind, chat_id, exc)
        raise CeleryTaskError(f"Could not generate {kind} for {chat_id!r}: {exc}") from exc
