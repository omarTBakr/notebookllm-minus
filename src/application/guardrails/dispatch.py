"""Getting a verdict from the guardrail Celery queues without blocking the loop.

This is the first place the API waits on a Celery result: everything else it
queues is fire-and-forget. Both halves are blocking calls, publishing to
RabbitMQ and ``AsyncResult.get`` on Redis, so both run on a thread pool of
their own. That pool is not asyncio's default executor: a burst of chats, each
with a few output checks in flight, would otherwise take every default thread
for up to GUARDRAIL_TIMEOUT_S and stall unrelated work queued with
``asyncio.to_thread``, such as ingest.
"""

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor

from shared.utils import get_logger

logger = get_logger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=64, thread_name_prefix="guardrail-wait")


class CeleryGuardrailDispatcher:
    """Submits one item to its stage's batched task and waits for the verdict."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def submit(self, stage: str, item: dict) -> dict:
        from application.tasks.jobs.guardrails import task_for

        task = task_for(stage)
        loop = asyncio.get_running_loop()

        # run_in_executor does not copy contextvars the way to_thread does, and
        # the trace headers are attached at publish time from the current
        # Langfuse span, so carry the context across explicitly.
        context = contextvars.copy_context()
        result = await loop.run_in_executor(_EXECUTOR, context.run, lambda: task.apply_async(kwargs={"item": item}))

        try:
            return await loop.run_in_executor(
                _EXECUTOR,
                lambda: result.get(timeout=self.timeout, interval=0.05, propagate=True),
            )
        finally:
            # The verdict has been read; do not leave it in Redis until
            # CELERY_RESULT_EXPIRES. Best-effort, on the same pool.
            loop.run_in_executor(_EXECUTOR, _forget, result)


def _forget(result) -> None:
    try:
        result.forget()
    except Exception as exc:  # pragma: no cover - cleanup only
        logger.debug("Could not forget guardrail result %s: %s", result.id, exc)
