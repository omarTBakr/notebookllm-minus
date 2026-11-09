"""The guardrail queues: micro-batched guard-model calls, one queue per stage.

Each task is a celery-batches ``Batches`` task. The worker buffers incoming
requests and runs the task on the buffer as soon as either

* GUARDRAIL_BATCH_SIZE requests are waiting (``flush_every``), or
* GUARDRAIL_BATCH_MAX_WAIT_S has passed (``flush_interval``),

whichever comes first. A lone request therefore waits at most the interval,
never for the batch to fill. One guard-model call then judges the whole batch
(see application/guardrails/judge.py), and each request's verdict is written
to the result backend under its own task id, where the API is waiting for it
(see application/guardrails/dispatch.py).

Deployment notes, enforced in Docker/docker-compose.yml:

* each queue has its own worker (celery-guardrail-input / -output); a queue
  with no ``-Q`` consumer swallows tasks silently, and an unanswered guardrail
  check costs every chat GUARDRAIL_TIMEOUT_S;
* the worker's prefetch (``--prefetch-multiplier`` x concurrency) must be at
  least GUARDRAIL_BATCH_SIZE, or the broker never hands over enough unacked
  messages for a batch to fill;
* ``acks_late`` is off for these tasks: a check is short and safe to lose, and
  the API resolves a lost one with GUARDRAIL_FAIL_MODE when it times out.
"""

import asyncio

from celery_batches import Batches

from application.guardrails.judge import judge_batch
from application.guardrails.verdict import Verdict
from application.providers import ProviderCache
from celery_app import celery_app
from shared.enums import CeleryTaskFunction
from shared.utils import get_logger, get_settings, tracing
from shared.utils.metrics import GUARDRAIL_BATCH_SIZE

logger = get_logger(__name__)

SETTINGS = get_settings()

# A guard call is seconds, not minutes. Well inside the global 30-minute limit
# so a hung model call frees the worker long before the API gives up waiting.
_TIME_LIMIT = max(30, int(SETTINGS.GUARDRAIL_TIMEOUT_S * 3))


def _model_for(stage: str) -> str | None:
    return SETTINGS.GUARDRAIL_INPUT_MODEL if stage == "input" else SETTINGS.GUARDRAIL_OUTPUT_MODEL


async def _judge(stage: str, items: list[dict]) -> list[Verdict]:
    """One guard-model call for the batch, on a client built for this run.

    The client is built and closed per batch rather than kept per process:
    every batch runs in its own asyncio.run, and an HTTP client pool bound to a
    previous run's closed event loop fails on its next request.
    """
    providers = ProviderCache(SETTINGS)
    try:
        client = providers.chatting(_model_for(stage), thinking=False)
        return await judge_batch(client, stage, items, default_lang=SETTINGS.DEFAULT_LANG)
    finally:
        await providers.aclose_all()


def _trace_context(request) -> dict | None:
    """The trace of the chat that queued *request*, from its headers."""
    request_dict = getattr(request, "request_dict", None) or {}
    headers = request_dict.get("headers") if isinstance(request_dict.get("headers"), dict) else request_dict
    return tracing.trace_context_from(headers)


def run_batch(task, stage: str, requests) -> list[dict]:
    """Judge *requests* together and store each verdict under its own id."""
    GUARDRAIL_BATCH_SIZE.labels(stage).observe(len(requests))

    items = []
    for request in requests:
        item = (request.kwargs or {}).get("item")
        items.append(item if isinstance(item, dict) else {"text": ""})

    with tracing.observation(
        f"guardrail.batch.{stage}",
        as_type="guardrail",
        metadata={"batch_size": len(requests), "model": _model_for(stage) or SETTINGS.GENERATION_MODEL_ID},
    ):
        try:
            verdicts = asyncio.run(_judge(stage, items))
        except Exception as exc:
            # Every request still gets an answer, now, instead of the API
            # waiting out GUARDRAIL_TIMEOUT_S for each one.
            logger.warning("Guardrail %s batch of %d failed: %s", stage, len(requests), exc)
            verdicts = [Verdict.undecided(f"guard batch failed: {type(exc).__name__}")] * len(requests)

    results = []
    for request, verdict in zip(requests, verdicts):
        payload = verdict.to_dict()
        task.backend.mark_as_done(request.id, payload, request=request)
        results.append(payload)

        # The verdict also lands on the chat's own trace, so a refusal can be
        # read next to the question that caused it.
        context = _trace_context(request)
        if context:
            tracing.event(
                f"guardrail.{stage}.verdict",
                trace_context=context,
                output=payload,
                level="DEFAULT" if verdict.safe else "WARNING",
                metadata={"batch_size": len(requests)},
            )

    logger.info(
        "Judged %d %s item(s) in one batch (%d flagged, %d undecided)",
        len(requests),
        stage,
        sum(v.safe is False for v in verdicts),
        sum(v.safe is None for v in verdicts),
    )
    return results


@celery_app.task(
    bind=True,
    base=Batches,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.GUARDRAIL_INPUT.value}",
    queue=SETTINGS.CELERY_QUEUE_GUARDRAIL_INPUT,
    flush_every=SETTINGS.GUARDRAIL_BATCH_SIZE,
    flush_interval=SETTINGS.GUARDRAIL_BATCH_MAX_WAIT_S,
    acks_late=False,
    time_limit=_TIME_LIMIT,
    soft_time_limit=_TIME_LIMIT - 5,
)
def guardrail_input_task(self, requests):
    """Judge a batch of user questions before the answer model sees them."""
    return run_batch(self, "input", requests)


@celery_app.task(
    bind=True,
    base=Batches,
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.GUARDRAIL_OUTPUT.value}",
    queue=SETTINGS.CELERY_QUEUE_GUARDRAIL_OUTPUT,
    flush_every=SETTINGS.GUARDRAIL_BATCH_SIZE,
    flush_interval=SETTINGS.GUARDRAIL_BATCH_MAX_WAIT_S,
    acks_late=False,
    time_limit=_TIME_LIMIT,
    soft_time_limit=_TIME_LIMIT - 5,
)
def guardrail_output_task(self, requests):
    """Judge a batch of answer passages before they are shown."""
    return run_batch(self, "output", requests)


def task_for(stage: str):
    if stage == "input":
        return guardrail_input_task
    if stage == "output":
        return guardrail_output_task
    raise ValueError(f"unknown guardrail stage {stage!r}")
