"""The worker side: judge a whole micro-batch in one guard-model call.

Runs inside the guardrail Celery workers. Each request in the batch becomes a
numbered item in one prompt, and the structured answer maps back to requests
by that batch-local number. Anything the model skipped, or numbered outside
the batch, stays undecided, and the API resolves it with GUARDRAIL_FAIL_MODE.
"""

from collections import Counter

from application.prompts import TemplateParser
from shared.exceptions import StructuredOutputError
from shared.utils import get_logger

from .verdict import GuardVerdicts, Verdict

logger = get_logger(__name__)

STAGES = ("input", "output")

#: Bounds one item's share of the prompt. A guard reads the start of a long
#: message as well as the whole of it, and a batch of eight long messages
#: would otherwise overflow a small guard model's context.
MAX_ITEM_CHARS = 4000
#: The output stage only needs the tail of what was already shown.
MAX_CONTEXT_CHARS = 1500


def batch_lang(items: list[dict], default: str) -> str:
    """The language the instructions are written in: the batch's majority."""
    langs = Counter(item.get("lang") or default for item in items)
    return langs.most_common(1)[0][0] if langs else default


def format_items(parser: TemplateParser, stage: str, items: list[dict]) -> str:
    blocks = []
    for idx, item in enumerate(items):
        text = str(item.get("text") or "")[:MAX_ITEM_CHARS]
        if stage == "input":
            blocks.append(parser.get("guardrails", "input_item", {"idx": idx, "text": text}))
        else:
            context = str(item.get("context") or "")[-MAX_CONTEXT_CHARS:] or "(nothing yet)"
            question = str(item.get("question") or "")[:MAX_ITEM_CHARS]
            blocks.append(
                parser.get(
                    "guardrails",
                    "output_item",
                    {"idx": idx, "question": question, "context": context, "text": text},
                )
            )
    return "\n\n".join(blocks)


async def judge_batch(client, stage: str, items: list[dict], *, default_lang: str = "en") -> list[Verdict]:
    """One verdict per item, in the order the items were given."""
    if stage not in STAGES:
        raise ValueError(f"unknown guardrail stage {stage!r}")

    if not items:
        return []

    # Late: application.services imports ChatService, which imports the
    # guardrails, so a top-level import here would be circular.
    from application.services.llm.structured_generation import generate_structured

    parser = TemplateParser(lang=batch_lang(items, default_lang), default_lang=default_lang)
    prompt = parser.get("guardrails", f"{stage}_prompt", {"items": format_items(parser, stage, items)})

    try:
        answer = await generate_structured(client, prompt, GuardVerdicts, max_items=len(items))
    except StructuredOutputError as exc:
        logger.warning("Guard model gave no usable %s verdicts for %d item(s): %s", stage, len(items), exc)
        return [Verdict.undecided("guard model output did not parse")] * len(items)

    verdicts: list[Verdict | None] = [None] * len(items)
    for item in answer.verdicts:
        # Out-of-range numbers are dropped, not clamped: a verdict that cannot
        # be tied to an item must not land on the wrong one.
        if 0 <= item.idx < len(items) and verdicts[item.idx] is None:
            category = "safe" if item.safe else item.category.strip().lower()
            verdicts[item.idx] = Verdict(safe=item.safe, category=category, reason=item.reason)

    missing = sum(v is None for v in verdicts)
    if missing:
        logger.warning("Guard model skipped %d of %d %s item(s)", missing, len(items), stage)

    return [v or Verdict.undecided("guard model skipped this item") for v in verdicts]
