"""Langfuse tracing behind a switch.

Everything in the app traces through this module, never through ``langfuse``
directly, for two reasons:

* **Off means off.** With ``LANGFUSE_ENABLED=false``, or no keys set, every
  helper hands back a no-op stand-in. Tests, a bare dev box and a deploy that
  has not started the Langfuse containers yet all run unchanged, with no
  exporter thread and no network calls.
* **One shape.** Spans, generations and Celery propagation all go through the
  same few helpers, so a trace looks the same whether it started in the API or
  in a worker.

Nesting follows OpenTelemetry's current context: an observation opened with
:func:`observation` becomes the parent of anything opened inside it, including
inside ``asyncio.gather`` children, which copy the context. An async generator
consumed by a single task (the SSE answer stream) keeps that context across
yields as well.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from shared.utils.logging_config import get_logger

logger = get_logger(__name__)

# Celery message headers that carry a trace from the API into a worker.
TRACE_ID_HEADER = "langfuse_trace_id"
PARENT_SPAN_HEADER = "langfuse_parent_span_id"

_client = None
_client_lock = threading.Lock()
_client_resolved = False

# The generation currently being produced, so a provider's _log_usage can
# attach token counts without being handed the observation. Set and read
# within one coroutine or one async generator step, so it never leaks.
_current_generation: contextvars.ContextVar[Any] = contextvars.ContextVar("langfuse_generation", default=None)

# The Langfuse prompt a caller is about to send, linked to the next generation
# so the trace shows which prompt *version* produced the answer.
_active_prompt: contextvars.ContextVar[Any] = contextvars.ContextVar("langfuse_prompt", default=None)


class _NoopObservation:
    """Stands in for a Langfuse span/generation when tracing is off."""

    id = None
    trace_id = None

    def update(self, **_: Any) -> "_NoopObservation":
        return self

    def end(self, **_: Any) -> None:
        return None

    def create_event(self, **_: Any) -> None:
        return None


NOOP = _NoopObservation()


def get_langfuse():
    """The process-wide Langfuse client, or None when tracing is off.

    Built lazily on first use, and in each Celery child after the fork rather
    than in the parent, because the OTel exporter thread does not survive a fork.
    """
    global _client, _client_resolved

    if _client_resolved:
        return _client

    with _client_lock:
        if _client_resolved:
            return _client

        from shared.utils.config import get_settings

        settings = get_settings()

        if not (settings.LANGFUSE_ENABLED and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
            _client_resolved = True
            return None

        try:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                base_url=settings.LANGFUSE_HOST,
                sample_rate=settings.LANGFUSE_SAMPLE_RATE,
                environment=settings.LANGFUSE_ENVIRONMENT,
                release=settings.APP_VERSION,
                # Prompt and answer text is what makes a trace worth reading,
                # but it is also the user's documents. LANGFUSE_CAPTURE_IO=false
                # keeps the timings, models and token counts and blanks the text.
                mask=None if settings.LANGFUSE_CAPTURE_IO else _mask_everything,
            )
            logger.info("Langfuse tracing on (host=%s)", settings.LANGFUSE_HOST)
        except Exception as exc:
            # Tracing must never take the app down with it.
            logger.warning("Langfuse could not start; tracing is off: %s", exc)
            _client = None

        _client_resolved = True
        return _client


def _mask_everything(*, data: Any, **_: Any) -> Any:
    return "[redacted]" if data is not None else None


def reset_for_tests() -> None:
    """Forget the resolved client so a test can flip LANGFUSE_ENABLED."""
    global _client, _client_resolved
    _client, _client_resolved = None, False


def enabled() -> bool:
    return get_langfuse() is not None


@contextmanager
def observation(name: str, as_type: str = "span", **attrs: Any) -> Iterator[Any]:
    """Open a span (or generation, guardrail, retriever, ...) as the current one.

    Yields the observation, or a no-op when tracing is off, so callers can
    always call ``.update(...)`` on it. An exception inside marks it ERROR and
    is re-raised unchanged.
    """
    client = get_langfuse()

    if client is None:
        yield NOOP
        return

    try:
        cm = client.start_as_current_observation(name=name, as_type=as_type, **attrs)
        obs = cm.__enter__()
    except Exception as exc:
        logger.debug("Could not open observation %s: %s", name, exc)
        yield NOOP
        return

    try:
        yield obs
    except BaseException as exc:
        _safe(
            obs.update,
            level="ERROR",
            status_message=f"{type(exc).__name__}: {exc}"[:500],
        )
        _safe(cm.__exit__, type(exc), exc, exc.__traceback__)
        raise
    else:
        _safe(cm.__exit__, None, None, None)


def start_generation(name: str, **attrs: Any) -> Any:
    """Start a generation that is *not* made current, and remember it for usage.

    Used for streamed answers: the generation is ended explicitly once the last
    piece has arrived. A linked prompt (see :func:`linked_prompt`) is attached.
    """
    client = get_langfuse()

    if client is None:
        return NOOP

    prompt = _active_prompt.get()
    if prompt is not None:
        attrs.setdefault("prompt", prompt)

    try:
        gen = client.start_observation(name=name, as_type="generation", **attrs)
    except Exception as exc:
        logger.debug("Could not start generation %s: %s", name, exc)
        return NOOP

    _current_generation.set(gen)
    return gen


def end_generation(gen: Any, **attrs: Any) -> None:
    if gen is NOOP:
        return
    _safe(gen.update, **attrs)
    _safe(gen.end)
    if _current_generation.get() is gen:
        _current_generation.set(None)


def record_usage(
    input_tokens: int | None,
    output_tokens: int | None,
    cost: dict[str, float] | None = None,
) -> None:
    """Attach token counts, and the cost when known, to the generation in flight."""
    gen = _current_generation.get()
    if gen is None or gen is NOOP:
        return

    usage = {}
    if input_tokens is not None:
        usage["input"] = int(input_tokens)
    if output_tokens is not None:
        usage["output"] = int(output_tokens)

    if cost is not None:
        _safe(gen.update, usage_details=usage, cost_details=cost)
    elif usage:
        _safe(gen.update, usage_details=usage)


@contextmanager
def linked_prompt(prompt_ref: Any) -> Iterator[None]:
    """Link generations made inside this block to a Langfuse prompt version."""
    token = _active_prompt.set(prompt_ref)
    try:
        yield
    finally:
        _active_prompt.reset(token)


@contextmanager
def trace_attributes(**attrs: Any) -> Iterator[None]:
    """Set user_id / session_id / tags / metadata on the current trace.

    Applies to every observation opened inside the block.
    """
    if get_langfuse() is None:
        yield
        return

    try:
        from langfuse import propagate_attributes

        cm = propagate_attributes(**{k: v for k, v in attrs.items() if v is not None})
        cm.__enter__()
    except Exception as exc:
        logger.debug("Could not set trace attributes: %s", exc)
        yield
        return

    try:
        yield
    finally:
        _safe(cm.__exit__, None, None, None)


def event(name: str, **attrs: Any) -> None:
    """A point-in-time marker on the current trace, e.g. a reranker fallback."""
    client = get_langfuse()
    if client is None:
        return
    _safe(client.create_event, name=name, **attrs)


# --- Celery propagation -------------------------------------------------------


def current_trace_headers() -> dict[str, str]:
    """Headers that let a worker continue the current trace, or {}."""
    client = get_langfuse()
    if client is None:
        return {}

    # Most tasks (an upload's ingest, a scheduled sweep) are queued outside
    # any trace. Asking the SDK for ids then logs a "No active span" warning
    # per task, so check the OpenTelemetry context first.
    try:
        from opentelemetry import trace as otel_trace

        if not otel_trace.get_current_span().get_span_context().is_valid:
            return {}
    except Exception:
        return {}

    try:
        trace_id = client.get_current_trace_id()
        span_id = client.get_current_observation_id()
    except Exception:
        return {}

    if not trace_id:
        return {}

    headers = {TRACE_ID_HEADER: trace_id}
    if span_id:
        headers[PARENT_SPAN_HEADER] = span_id
    return headers


def trace_context_from(headers: dict | None) -> dict | None:
    """The ``trace_context`` argument Langfuse wants, from task headers."""
    if not headers:
        return None

    trace_id = headers.get(TRACE_ID_HEADER)
    if not trace_id:
        return None

    context = {"trace_id": trace_id}
    parent = headers.get(PARENT_SPAN_HEADER)
    if parent:
        context["parent_span_id"] = parent
    return context


def flush() -> None:
    client = _client if _client_resolved else None
    if client is not None:
        _safe(client.flush)


def shutdown() -> None:
    client = _client if _client_resolved else None
    if client is not None:
        _safe(client.shutdown)


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.debug("Langfuse call %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None
