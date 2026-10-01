"""`/memory`: recognising the command, queuing its extraction, and searching it.

Split out of messages.py because the command handling has nothing in common
with the RAG answer stream once the work is queued rather than done inline:
this module owns the command's syntax, its idempotency key, and the immediate
ack the SSE stream sends back. The actual extraction runs off-request in
`application/tasks/jobs/memory.py`, which writes the real answer ("Saved: diet, job
title") as a second assistant message once it's done — picked up client-side
by the poller in `static/js/memory.js`.

It also owns the one memory-related GET: the composer's memory button,
searching the current draft against stored facts. Kept apart from the
*automatic* per-question fold in `ChatService.answer_stream` — that one
reads every fact unranked (see `MemoryService.list_facts`); this is a
genuine ranked search (`MemoryService.search`), run only when asked.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from application.services import MemoryService
from application.tasks import extract_memory_task
from application.tasks.tracking.status import mark_queued
from presentation import dependencies as deps
from shared.exceptions import CELERY_BROKER_EXCEPTIONS

from ._helpers import _sse, logger

memory_router = APIRouter()

# Matched case-insensitively against the *start* of the trimmed text, so
# "/memory I'm vegetarian" and "/Memory I'm vegetarian" both count, and a
# question that merely mentions the word "memory" does not.
_MEMORY_PREFIX = "/memory"


def is_memory_command(text: str) -> str | None:
    """The text after `/memory`, or None if *text* is not that command."""
    stripped = text.strip()

    if not stripped.lower().startswith(_MEMORY_PREFIX):
        return None

    return stripped[len(_MEMORY_PREFIX) :].strip()


async def handle_memory_command(http_request: Request, chat, messages, remainder: str) -> StreamingResponse:
    """Queue extraction for *remainder* and reply with an immediate ack.

    Speaks the same `delta`/`done`/`error` SSE contract as a normal answer, so
    the composer needs no changes to render the ack — only to know to poll
    afterward for the real reply.
    """
    idempotency = deps.idempotency(http_request)
    task_name = extract_memory_task.name
    args = {"chat_id": chat.chat_id, "text": remainder}

    # Claimed *before* the SSE response starts, not inside it: a double-submit
    # (a retry, a double Enter) must not queue a second identical extraction,
    # and this is the one place that can still say no before anything is sent.
    running = await idempotency.claim(task_name, args)

    async def events():
        if running is not None:
            # Something is already working on this exact text. No new ack to
            # persist — the original ack already told the user it was coming.
            yield _sse({"type": "delta", "text": "Already remembering that — hang tight."})
            yield _sse({"type": "done"})
            return

        try:
            # extract_memory_task's signature is (chat_id, user_id, text, lang)
            # -- in that order. Swapped here once already: every fact ever
            # saved was filed under the chat's id instead of the user's,
            # which is why automatic fold (ChatService.answer_stream,
            # which searches by the real user_id) never found anything a
            # /memory message had just saved.
            result = extract_memory_task.apply_async(args=[chat.chat_id, chat.user_id, remainder, chat.lang])
        except CELERY_BROKER_EXCEPTIONS as exc:
            logger.warning("Could not queue /memory extraction for chat %r: %s", chat.chat_id, exc)
            yield _sse({"type": "error", "detail": "Could not save that right now — try again in a moment."})
            return

        mark_queued(result.id)
        await idempotency.record(task_id=result.id, task_name=task_name, project_id=chat.chat_id, args=args)

        ack = "✅ Memory updated — I'll let you know once I've gone through that."
        yield _sse({"type": "delta", "text": ack})
        yield _sse({"type": "done"})

        await messages.add_assistant_message(chat.chat_id, ack)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@memory_router.get("/chats/{chat_id}/memory")
async def search_memory(chat_id: str, http_request: Request, q: str = ""):
    """The composer's memory button: up to `MemoryService.MEMORY_TOP_K`
    stored facts closest to *q*, the current composer draft.

    Distinct from what `ChatService.answer_stream` folds into an actual
    answer — that reads every fact, unranked; this is a real search, and
    exists so someone can preview what's relevant before they send anything.
    """
    chat = await deps.conversations(http_request).get_chat(chat_id)

    q = q.strip()
    if not q:
        return JSONResponse(status_code=200, content={"memories": []})

    controller = deps.memory_service(http_request, chat)
    hits = await controller.search(chat.user_id, q)

    return JSONResponse(status_code=200, content={"memories": MemoryService.to_citations(hits)})
