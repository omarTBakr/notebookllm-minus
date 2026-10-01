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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps
from shared.enums import ArtifactKind
from shared.exceptions import InvalidInputError

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

    started = await deps.studio(http_request).start(chat_id, artifact_kind)

    if started.already_running:
        return JSONResponse(
            status_code=200,
            content={
                "chat_id": chat_id,
                "kind": artifact_kind.value,
                "task_id": started.task_id,
                "status": "already running",
            },
        )

    return JSONResponse(
        status_code=202,
        content={
            "chat_id": chat_id,
            "kind": artifact_kind.value,
            "task_id": started.task_id,
            "artifact_id": started.artifact_id,
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

    artifact = await deps.studio(http_request).find(chat_id, artifact_kind)

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
