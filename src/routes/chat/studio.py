"""Generated study material: flashcard decks and quizzes.

Two endpoints and one idea. `POST` starts a generation and returns
immediately; `GET` returns whatever exists *so far*, with a status saying
whether more is coming. That second part is what makes the panel usable: a
694-chunk book takes minutes to work through, and the alternative — a spinner
until it is all done — was the design this replaced.

Progress is not reported here. The task writes a `task_executions` row like any
other, so the browser polls the same `/indexing` endpoint the upload already
uses rather than growing a second progress mechanism that could disagree
with it.
"""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import IdempotencyController
from enums import ArtifactKind, ArtifactStatus
from exceptions import (
    CELERY_BROKER_EXCEPTIONS,
    CeleryBrokerError,
    InvalidInputError,
    NotFoundError,
)
from models import ArtifactModel, ChatModel, ChunkModel, ProjectModel
from models.db_schema import Artifact
from tasks import generate_artifact_task
from tasks.status import mark_queued

studio_router = APIRouter()


def _parse_kind(kind: str) -> ArtifactKind:
    """The URL segment as a kind, or a 400 naming what is available.

    ArtifactKind holds only what has a generator behind it, so an unbuilt
    Studio tile cannot be started by guessing its name in a URL.
    """
    try:
        return ArtifactKind(kind)
    except ValueError:
        raise InvalidInputError(
            f"{kind!r} is not something that can be generated. "
            f"Available: {', '.join(k.value for k in ArtifactKind)}"
        ) from None


@studio_router.post("/chats/{chat_id}/studio/{kind}")
async def generate_artifact(chat_id: str, kind: str, http_request: Request):
    """Start generating a deck or quiz for this notebook.

    202 with the task id, or 200 with the id of the run already in flight — the
    same distinction the ingestion routes draw, so a caller can tell "started"
    from "already going" rather than accidentally queueing a second identical
    job when someone double-clicks a tile.
    """
    artifact_kind = _parse_kind(kind)
    db = http_request.app.db

    # Raises ChatNotFoundError -> 404 if the notebook does not exist, before
    # anything is queued.
    await ChatModel(db).get_chat(chat_id)

    # Answered here rather than inside the task, because a task that dies on
    # its first line is the worst possible way to say "this notebook has no
    # documents yet": the browser is left polling an artifact that was never
    # created, which it cannot tell apart from one still being written, so the
    # panel says "reading the documents..." forever. Observed doing exactly
    # that -- two tasks dead within half a second of being queued, a panel
    # still spinning four minutes later. Clicking a Studio tile while the
    # upload is still indexing is an ordinary thing to do and gets an ordinary
    # answer.
    try:
        project = await ProjectModel(db).get_project(chat_id)
        chunk_count = await ChunkModel(db).count_project_chunks(project.id)
    except NotFoundError:
        chunk_count = 0

    if not chunk_count:
        raise InvalidInputError(
            f"Notebook {chat_id!r} has nothing to generate from yet. "
            "Add a document and let it finish indexing first."
        )

    idempotency = IdempotencyController(db)
    task_name = generate_artifact_task.name
    args = {"chat_id": chat_id, "kind": artifact_kind.value}

    running = await idempotency.claim(task_name, args)

    if running is not None:
        return JSONResponse(
            status_code=200,
            content={
                "chat_id": chat_id,
                "kind": artifact_kind.value,
                "task_id": running.task_id,
                "status": "already running",
            },
        )

    # The row is created *here*, before the task is queued, so that from the
    # moment this returns 202 there is something for the browser to poll. The
    # task used to create it, which left a window -- and, when the task failed
    # early, a permanent hole -- where GET answered `exists: false` and the UI
    # read that as "still working". Creating it upserts on (chat_id, kind) and
    # clears the items, which is exactly what regenerating wants.
    artifacts = ArtifactModel(db)

    # Reuse the id if this notebook already has a set of this kind. The row is
    # an upsert on (chat_id, kind), so that pair is the real identity and a
    # fresh surrogate id on every regeneration would name the same row
    # differently each time for no reader's benefit.
    previous = await artifacts.find_artifact(chat_id, artifact_kind.value)
    artifact_id = previous.artifact_id if previous else str(uuid.uuid4())

    # Resets the items, which is what regenerating means.
    await artifacts.create_artifact(
        Artifact(
            artifact_id=artifact_id,
            chat_id=chat_id,
            kind=artifact_kind,
            status=ArtifactStatus.GENERATING,
        )
    )

    try:
        result = generate_artifact_task.apply_async(args=[chat_id, artifact_kind.value])
    except CELERY_BROKER_EXCEPTIONS as exc:
        # The row exists now, so it has to be marked rather than left claiming
        # to be generating something nothing is working on.
        await artifacts.finish_artifact(artifact_id, ArtifactStatus.FAILED.value, "could not be queued")
        raise CeleryBrokerError(f"Could not queue {artifact_kind.value} generation for {chat_id!r}") from exc

    mark_queued(result.id)
    await idempotency.record(task_id=result.id, task_name=task_name, project_id=chat_id, args=args)

    return JSONResponse(
        status_code=202,
        content={
            "chat_id": chat_id,
            "kind": artifact_kind.value,
            "task_id": result.id,
            "artifact_id": artifact_id,
            "status": "queued",
        },
    )


@studio_router.get("/chats/{chat_id}/studio/{kind}")
async def read_artifact(chat_id: str, kind: str, http_request: Request):
    """The current set, however far along it is.

    Returns partial results deliberately. `status` says whether more is coming,
    and the UI renders what it has rather than waiting -- which is the whole
    reason the generator appends per batch instead of writing once at the end.

    200 with `exists: false` rather than 404 for a notebook that has never
    generated this kind: the browser asks on every panel open, and a 404 there
    is an ordinary state dressed as an error.
    """
    artifact_kind = _parse_kind(kind)

    artifact = await ArtifactModel(http_request.app.db).find_artifact(chat_id, artifact_kind.value)

    if artifact is None:
        return JSONResponse(
            status_code=200,
            content={
                "chat_id": chat_id,
                "kind": artifact_kind.value,
                "exists": False,
                "items": [],
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "kind": artifact_kind.value,
            "exists": True,
            "artifact_id": artifact.artifact_id,
            "status": str(artifact.status),
            "items": artifact.items,
            "count": len(artifact.items),
            "task_id": artifact.source_task_id,
            "error": artifact.error,
        },
    )


# No DELETE endpoint. Regenerating replaces the set -- create_artifact upserts
# on (chat_id, kind) and resets the items -- so discarding one is what starting
# another already does. The repository can only delete a notebook's artifacts
# wholesale, and a per-kind delete existing solely to back an endpoint nothing
# asked for would be a method to maintain for no caller.
