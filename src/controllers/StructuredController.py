"""Asking a model for a specific shape, and insisting on it.

Every Studio feature is the same problem wearing different clothes: make a
model return a structure, reliably, hundreds of times. A flashcard is
`{front, back, chunk_order}`; a quiz question adds options and an answer index.
Prose is not useful here — something has to parse.

This sits *on top of* `LLMChattingInterface.generate_text()` rather than inside
it. Two reasons, and both are about not making six providers worse:

  * Native JSON mode is spelled differently by every vendor (`response_format`,
    `response_mime_type`, tool schemas) and is not supported by all of them.
    Threading it through the ABC would mean changing all six implementations
    and still needing this fallback for the ones that cannot do it.
  * `generate_text` is already built, tested and provider-agnostic, and until
    now had exactly one caller — the model-usability probe. It is the right
    seam.

What it does not do: it does not stream, and it does not ask for a schema the
model can partially satisfy. A malformed answer is retried with the validation
error attached, then it fails loudly. Silently returning half a deck would be
worse than returning none.
"""

import json
import re

from pydantic import BaseModel, ValidationError

from exceptions import StructuredOutputError
from utils import get_logger

logger = get_logger(__name__)

# A fenced block, with or without a language tag. Models wrap JSON in these
# despite being told not to, and charging that to the schema would measure
# instruction-following rather than the answer. The OCR package learned the
# same lesson the expensive way -- see `arabic_extraction/extractors/hosted.py`, where a
# model was scored at four times its real error rate until its markup was
# stripped.
_FENCE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.DOTALL)

# Where the JSON starts. Some models prepend a sentence no amount of prompting
# removes ("Here is the JSON you asked for:").
_JSON_START = re.compile(r"[\{\[]")

# Control characters are illegal inside a JSON string and models emit them
# anyway -- observed on Arabic pages, where one of them in 20KB of otherwise
# correct output failed the whole parse. Tab, newline and carriage return are
# left alone: json accepts those escaped, and they carry meaning in a
# reproduced page.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# `{"a": 1,}` -- forbidden by JSON, emitted by models, unambiguous to drop.
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _balanced(text: str, start: int) -> str:
    r"""The one complete JSON value beginning at *start*, or everything after it.

    A greedy `\{.*\}` takes from the first brace to the *last* one anywhere in
    the response, so a model that appended a stray `}` -- or wrote a second
    object after the first -- produced "trailing characters at column 20611"
    even though a perfectly good object was sitting at the front. Counting
    depth takes the first complete value instead.

    Quotes and escapes are tracked because a brace inside a string is not a
    brace, and page text routinely contains them.
    """
    depth = 0
    in_string = False
    escaped = False
    opening = text[start]
    closing = "}" if opening == "{" else "]"

    for index in range(start, len(text)):
        char = text[index]

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    # Never closed. Returned whole so the parse fails on the truncation rather
    # than on something this function invented -- see the docstring below.
    return text[start:]


def extract_json(text: str) -> str:
    """The JSON inside a model's answer, with the packaging removed.

    Forgiving about what a model puts *around* valid JSON, and about the two
    syntax errors that are unambiguous to undo -- a control character inside a
    string, and a trailing comma before a closing brace. Both are illegal JSON
    that no reader would disagree about, and both were failing 20KB of
    otherwise correct output over one character.

    Still refuses to repair *truncation*. An object cut off mid-string is a
    real failure -- the model ran out of budget, usually in a repetition loop --
    and guessing at the missing half would store a document that is silently
    short. That case still parses as invalid and is retried, as before.
    """
    stripped = text.strip()

    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()

    found = _JSON_START.search(stripped)

    if found:
        stripped = _balanced(stripped, found.start())

    stripped = _CONTROL.sub("", stripped)

    return _TRAILING_COMMA.sub(r"\1", stripped)


def schema_instruction(schema: type[BaseModel]) -> str:
    """The JSON Schema of *schema*, as an instruction to append to a prompt.

    Generated from the Pydantic model rather than written by hand, so the
    prompt and the validator cannot drift apart -- the failure mode where a
    field is renamed in the model, the prompt keeps asking for the old name,
    and every generation fails validation for a reason no one can see.
    """
    spec = schema.model_json_schema()
    example = spec.pop("example", None)

    parts = [
        "Return only JSON, with no prose, no explanation and no markdown fences.",
        "",
        f"It must match this schema:\n{json.dumps(spec, ensure_ascii=False)}",
    ]

    if example is not None:
        # A worked example, because a schema alone is not enough for a small
        # model. Asked for a quiz with only the schema, llama-3.2-11b returned
        # the schema itself -- `$defs`, `properties`, `title` -- on all three
        # attempts, so every quiz on that model failed outright. Models that
        # cannot fill in a specification can usually still copy a shape.
        #
        # Last, and phrased as the thing to imitate, because the nearest
        # concrete text is what gets echoed: better that it echoes an answer
        # than the specification.
        parts += [
            "",
            "Answer in exactly this shape. Copy the structure, never the "
            "words: every value below is a placeholder and none of it belongs "
            "in your answer.\n"
            f"{json.dumps(example, ensure_ascii=False)}",
        ]

    return "\n".join(parts)


async def generate_structured(
    client,
    prompt: str,
    schema: type[BaseModel],
    *,
    retries: int = 2,
    max_tokens: int | None = None,
) -> BaseModel:
    """Ask *client* for *prompt* and return it parsed as *schema*.

    `temperature=0` throughout: this is extraction, not writing. Sampling
    invents plausible structure where the source is thin, which scores worse
    than an honest failure and reads as though it worked.

    On a parse or validation failure the model is asked again *with its own
    error attached*, which is the cheapest correction available -- most models
    fix a named field on the second attempt. After `retries` the error is
    raised carrying the last raw response, because "validation failed" without
    the text that failed is unactionable in a log.
    """
    instructed = f"{prompt}\n\n{schema_instruction(schema)}"
    last_error: Exception | None = None
    raw = ""

    for attempt in range(retries + 1):
        raw = await client.generate_text(prompt=instructed, max_tokens=max_tokens, temperature=0)

        try:
            return schema.model_validate_json(extract_json(raw))
        except (ValidationError, ValueError) as exc:
            last_error = exc

            if attempt == retries:
                break

            # The error itself, not just that there was one. "did not match
            # QuizSet" three times over says nothing about whether the model is
            # missing a field, inventing one, or returning prose -- and those
            # need different fixes. Truncated because a ValidationError over a
            # long list names every element.
            logger.warning(
                "Structured output did not match %s (attempt %d of %d); retrying " "with the error attached: %s",
                schema.__name__,
                attempt + 1,
                retries + 1,
                str(exc)[:400].replace("\n", " "),
            )
            # The model sees precisely what was wrong with its own previous
            # answer. Repeating the schema alone does not help -- it already
            # had that and still got it wrong.
            instructed = (
                f"{prompt}\n\n{schema_instruction(schema)}\n\n"
                f"Your previous answer could not be parsed:\n{raw[:1000]}\n\n"
                f"The error was:\n{exc}\n\nReturn corrected JSON only."
            )

    raise StructuredOutputError(
        f"{schema.__name__} could not be parsed from the model's output after "
        f"{retries + 1} attempt(s): {last_error}",
        raw=raw,
    )
