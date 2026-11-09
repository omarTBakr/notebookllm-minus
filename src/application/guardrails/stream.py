"""Judging a streamed answer passage by passage, without stalling the stream.

The output guardrail cannot see an answer before it exists, and holding the
whole answer back until it is finished would throw away streaming. So the
answer is cut into passages, each at least GUARDRAIL_OUTPUT_CHUNK_CHARS long
and ending at a sentence boundary, and each passage is judged while the model
keeps writing the next one:

    model ──deltas──▶ buffer ──passage──▶ check (in flight) ──▶ released in order

A passage reaches the reader only once its own check has passed, and always in
order: passage 3 waits for passage 2's verdict even if its own came back first.
When a check flags a passage, generation is stopped, everything not yet
released is dropped, and a ``guardrail`` event takes its place. The reader
keeps what was already shown, since it passed.

``thinking`` events pass straight through: the scratchpad is shown folded,
labelled as reasoning, and kept out of the stored answer.
"""

import asyncio
import re
from collections import deque
from collections.abc import AsyncIterator

from shared.utils import get_logger

from .base import OutputGuardrail

logger = get_logger(__name__)

#: A sentence ends at . ! ? (and Arabic ؟ ، ؛) followed by whitespace, or at a
#: line break. Markdown list items and headings end at the line break.
_BOUNDARY = re.compile(r"(?:[.!?؟؛]+[\"')\]]*\s+)|\n+")


def split_point(buffer: str, min_chars: int) -> int | None:
    """Where to cut *buffer* into a passage, or None to keep buffering.

    The first sentence boundary at or after *min_chars*. If none arrives by
    twice that length (one long run-on sentence, a code block), cut at the last
    space instead, so the reader is never held back indefinitely.
    """
    if len(buffer) < min_chars:
        return None

    match = _BOUNDARY.search(buffer, min_chars - 1)
    if match:
        return match.end()

    if len(buffer) >= 2 * min_chars:
        space = buffer.rfind(" ", min_chars)
        return space + 1 if space > 0 else len(buffer)

    return None


async def guard_stream(
    events: AsyncIterator[dict],
    guard: OutputGuardrail,
    question: str,
    min_chars: int,
) -> AsyncIterator[dict]:
    """Re-emit *events* with every ``delta`` held until its passage passes."""
    buffer = ""
    released: list[str] = []
    pending: deque[tuple[str, asyncio.Task]] = deque()
    done_event: dict | None = None
    blocked = None

    def enqueue(passage: str) -> None:
        # Context is everything ahead of this passage, shown or still pending:
        # a passage is judged as a continuation, not as a fragment.
        context = "".join(released) + "".join(text for text, _ in pending)
        task = asyncio.create_task(guard.check_passage(question, context, passage))
        pending.append((passage, task))

    try:
        async for event in events:
            kind = event.get("type")

            if kind == "delta":
                buffer += event.get("text") or ""
                while (cut := split_point(buffer, min_chars)) is not None:
                    passage, buffer = buffer[:cut], buffer[cut:]
                    enqueue(passage)
            elif kind == "done":
                done_event = event
                break
            else:
                yield event

            # Release whatever has already passed, in order, without waiting.
            while pending and pending[0][1].done():
                passage, task = pending.popleft()
                verdict = task.result()
                if not verdict.safe:
                    blocked = verdict
                    break
                released.append(passage)
                yield {"type": "delta", "text": passage}

            if blocked is not None:
                break

        if blocked is None:
            # The model has finished: judge the tail, then wait for every
            # verdict still in flight, in order.
            if buffer:
                enqueue(buffer)
                buffer = ""

            while pending:
                passage, task = pending.popleft()
                verdict = await task
                if not verdict.safe:
                    blocked = verdict
                    break
                released.append(passage)
                yield {"type": "delta", "text": passage}

    finally:
        for _, task in pending:
            task.cancel()
        # Stop the model: closing the generator closes the provider's stream.
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Could not close the answer stream: %s", exc)

    if blocked is not None:
        yield {
            "type": "guardrail",
            "stage": "output",
            "category": blocked.category,
            "message": guard.refusal_text(),
        }

    yield done_event or {"type": "done"}
