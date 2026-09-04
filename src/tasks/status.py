"""One answer to "what happened to this task?", shared by both task modules.

`get_process_task` and `get_index_task` were byte-for-byte identical, and both
had the same blind spot: they reported whatever `AsyncResult.status` said, and
Celery synthesises `PENDING` for *any* id it has no record of. So four
genuinely different situations arrived at the client as the same word —

    queued, no worker has taken it yet
    running right now
    finished so long ago the result expired
    never existed; the id is a typo

Only the second is fixed by configuration (``task_track_started``). The others
need the distinction made here, because Celery cannot make it: a PENDING result
is an absence of data, and an absence looks the same whatever caused it.

The fix is a marker written when the task is published. If the backend has no
record *and* no marker, nothing ever queued that id and it is UNKNOWN. If there
is a marker, it really is waiting for a worker.
"""

from celery.result import AsyncResult

from celery_app import SETTINGS, celery_app
from exceptions import CELERY_RESULT_EXCEPTIONS, CeleryResultError
from utils import get_logger

logger = get_logger(__name__)

# Celery's own name for "I have no record of this".
PENDING = "PENDING"

# Ours. Not a Celery state — it never reaches Celery, only the client.
UNKNOWN = "UNKNOWN"

_MARKER_PREFIX = "notebookllm:queued:"


def _redis():
    """The result backend's Redis client, or None if it is not Redis.

    Tests run against a memory backend and deployments could use another, so
    every caller degrades to the old behaviour rather than failing.
    """
    return getattr(celery_app.backend, "client", None)


def mark_queued(task_id: str) -> None:
    """Record that *task_id* was published, so it can be told from a typo.

    Best-effort by design. Failing to write the marker costs a later UNKNOWN
    where PENDING was true; failing the *enqueue* because the marker could not
    be written would trade a cosmetic problem for a real one.
    """
    client = _redis()

    if client is None:
        return

    try:
        client.setex(f"{_MARKER_PREFIX}{task_id}", SETTINGS.CELERY_RESULT_EXPIRES, "1")
    except Exception as exc:
        logger.warning("Could not mark task %r as queued: %s", task_id, exc)


def _was_queued(task_id: str) -> bool:
    client = _redis()

    if client is None:
        # No way to tell the two apart, so claim the harmless one: reporting a
        # real task as UNKNOWN would be worse than reporting a typo as PENDING.
        return True

    try:
        return bool(client.exists(f"{_MARKER_PREFIX}{task_id}"))
    except Exception as exc:
        logger.warning("Could not read queued marker for %r: %s", task_id, exc)
        return True


def task_status(task_id: str) -> dict:
    """The state of *task_id*, with PENDING split into its real causes."""
    try:
        result = AsyncResult(task_id, app=celery_app)
        status = result.status

        response = {"task_id": task_id, "status": status}

        if result.successful():
            response["result"] = result.result

        elif result.failed():
            # The type is half the diagnosis and used to be thrown away:
            # "Project 'x' has no chunks to index" and a broker timeout both
            # arrived as an untyped string.
            response["error"] = str(result.result)
            response["error_type"] = type(result.result).__name__

        elif status == PENDING and not _was_queued(task_id):
            response["status"] = UNKNOWN
            response["error"] = f"No task {task_id!r} was ever queued, or its result has expired"

        return response

    except CELERY_RESULT_EXCEPTIONS as exc:
        raise CeleryResultError(f"Could not read Celery task {task_id!r}") from exc
