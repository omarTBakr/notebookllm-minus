# Guardrails

## What it does

A guard model checks the question before the answer model sees it, and checks the answer passage
by passage as it streams. A flagged question gets a refusal instead of an answer; a flagged
passage stops the stream, and the reader keeps what had already passed.

## How it works

`src/application/guardrails/`:

| Module | Holds |
| --- | --- |
| `base.py` | `ModelGuardrail` (a LangChain `AgentMiddleware`), `InputGuardrail.abefore_model`, `OutputGuardrail.aafter_model` |
| `stream.py` | `guard_stream`: the output guardrail on a streamed answer |
| `dispatch.py` | `CeleryGuardrailDispatcher`: the API side of the batched queues |
| `judge.py` | `judge_batch`: the worker side, one guard-model call per batch |
| `verdict.py` | `Verdict` and the guard model's answer schema |

- **Output, without losing streaming.** The answer is cut into passages of at least
  `GUARDRAIL_OUTPUT_CHUNK_CHARS`, each ending at a sentence boundary, and each is judged while
  the model writes the next. A passage reaches the reader only once its own check has passed, and
  always in order.
- **Micro-batching** (`src/application/tasks/jobs/guardrails.py`). Each stage is a
  celery-batches task on its own queue with its own worker and model. A batch goes when
  `GUARDRAIL_BATCH_SIZE` checks are waiting or `GUARDRAIL_BATCH_MAX_WAIT_S` has passed, whichever
  comes first, so a lone request never waits for the batch to fill. One guard-model call judges
  the whole batch.
- **Waiting without blocking.** This is the one place the API waits on a Celery result. Both
  halves are blocking calls, so they run on a thread pool of their own, not asyncio's default.
- **Fail mode.** A check that cannot complete (worker down, timeout, unparseable verdict) lets
  the text through when `GUARDRAIL_FAIL_MODE=open`, logged and counted, or refuses when `closed`.

**Deployment rules**, enforced in `Docker/docker-compose.yml`: each queue has its own worker; the
worker's prefetch must be at least `GUARDRAIL_BATCH_SIZE` or a batch never fills; `acks_late` is
off, because a check is short and safe to lose.

## Settings

| Setting | Does what |
| --- | --- |
| `GUARDRAILS_ENABLED` | both stages on or off |
| `GUARDRAIL_INPUT_MODEL` / `GUARDRAIL_OUTPUT_MODEL` | provider-qualified ids; unset uses `GENERATION_MODEL_ID`. Thinking is always off |
| `GUARDRAIL_BATCH_SIZE` / `GUARDRAIL_BATCH_MAX_WAIT_S` | the N and the T of the micro-batch |
| `GUARDRAIL_TIMEOUT_S` | how long the API waits for a verdict |
| `GUARDRAIL_FAIL_MODE` | `open` or `closed` |
| `GUARDRAIL_OUTPUT_CHUNK_CHARS` | passage size; smaller is safer and costs more checks |
| `CELERY_QUEUE_GUARDRAIL_INPUT` / `_OUTPUT` | the two queues |

## Tests

`tests/guardrails/test_guardrails.py`, `test_guardrail_integration.py` (a real in-process worker).

## Diagrams

[Guardrail micro-batching](../../software_engineering/3_sequence_diagrams/guardrail_microbatch.png) · [class diagram](../../software_engineering/4_class_diagrams/guardrails_and_tasks.png) ·
[user stories](../../software_engineering/1_user_stories/04_guardrails.png) · [chat with guardrails](../../software_engineering/2_use_case_diagrams/chat.png)

## Known limits

`test_a_lone_request_is_flushed_by_the_timer_not_held_for_the_batch` is timing-based and can fail
on a heavily loaded disk; see [cleanup follow-ups](../roadmap/cleanup_followups.md).
