"""Extracting durable facts from a `/memory` chat message, off the request path.

Moved out of the request/response cycle for the same reason Studio generation
is a task rather than an inline call: the work is a model call plus a DB write
plus an embed call, and none of that belongs on the thread a browser is
waiting on. The route (`routes/chat/_memory.py`) queues this and replies with
an immediate ack; this task does the actual extraction and appends the real
answer as a second assistant message once it is done — the chat-side analogue
of Studio appending items as they are produced.

`MemoryService` itself (`controllers/memory/MemoryService.py`) is
unchanged by this move: it was already async, DB/provider-layer only, with no
dependency on a FastAPI request. Only the caller moved.
"""

import uuid

from application.services import MemoryService
from celery_app import SETTINGS, celery_app
from data.models import Message, MessageModel
from shared.enums import ChatRole
from shared.exceptions import StructuredOutputError
from shared.utils import get_logger

from ..runtime import job_resources, run_job
from ..tracking.recorder import TaskRecorder

logger = get_logger(__name__)


def _reply_for(fact_keys: list[str]) -> str:
    return "Saved: " + ", ".join(fact_keys) if fact_keys else "Nothing new to remember there."


async def _tell_chat(db, chat_id: str, content: str) -> None:
    """Append one assistant message. Its own function so both the happy path
    and the failure path below write it the same way."""
    await MessageModel(db).create_message(
        Message(message_id=str(uuid.uuid4()), chat_id=chat_id, role=ChatRole.ASSISTANT, content=content)
    )


async def _run_extraction(chat_id: str, user_id: str, text: str, lang: str, task_id: str | None = None) -> dict:
    async with job_resources(providers=True) as job:
        db, providers = job.db, job.providers

        recorder = TaskRecorder(db, task_id)
        await recorder.started()

        controller = MemoryService(
            chat_client=providers.chatting(None, thinking=False),
            embedding_client=providers.embedding(None, None),
            vector_repo=db.vectors(),
            personal_info_repo=db.personal_user_info(),
        )

        try:
            try:
                facts = await controller.extract_and_store(user_id, text, lang)
                reply = _reply_for([fact.key for fact in facts])
            except StructuredOutputError:
                # Not a task failure — the model simply would not return
                # anything usable. Told to the chat as a normal reply, same as
                # the inline version used to, rather than surfaced as an error.
                reply = "Couldn't extract anything usable from that — try rephrasing."

            await _tell_chat(db, chat_id, reply)

            result = {"chat_id": chat_id, "reply": reply}
            await recorder.succeeded(result)

            return result

        except BaseException as exc:
            # The user is left staring at "Got it, I'll let you know..." forever
            # otherwise — a failure here still deserves a reply, the same way a
            # failed Studio run still marks its artifact FAILED instead of
            # leaving it GENERATING.
            try:
                await _tell_chat(db, chat_id, "Something went wrong saving that.")
            except Exception:  # noqa: BLE001 - never mask the original failure
                logger.warning("Could not write the failure message for chat %r", chat_id, exc_info=True)

            await recorder.failed(exc)
            raise


@celery_app.task(
    bind=True,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.extract_memory_task",
    queue=SETTINGS.CELERY_QUEUE_MEMORY,
)
def extract_memory_task(self, chat_id: str, user_id: str, text: str, lang: str) -> dict:
    """Extract facts from one `/memory` message and reply in the chat."""
    return run_job(
        lambda: _run_extraction(chat_id, user_id, text, lang, task_id=self.request.id),
        what=f"Extracting memory for chat {chat_id!r}",
    )
