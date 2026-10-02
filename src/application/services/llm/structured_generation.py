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

import asyncio
import json
import re
from typing import get_args, get_origin

from pydantic import BaseModel, ValidationError

from shared.exceptions import StructuredOutputError
from shared.utils import get_logger

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

# A backslash JSON does not allow after it. llama-3.2-11b writes `\'` for an
# apostrophe inside a summary -- valid in Python, not in JSON -- and one of
# them failed a whole batch of ten summaries three times over. Matched only
# at an odd run of backslashes, so an escaped backslash (`\\`) is left alone.
_BAD_ESCAPE = re.compile(r'(?<!\\)((?:\\\\)*)\\([^"\\/bfnrtu])')


def _escape_inner_quotes(text: str) -> str:
    """Escape a `"` inside a string that cannot be the string's closing quote.

    Models write `"summary": "The term "large language model" means..."` and
    the bare inner quote ends the string early; a summary batch failed at
    column 79 of an answer this way, three attempts running. In valid JSON a
    closing quote is always followed -- after whitespace -- by `,` `:` `}` `]`
    or the end, so a quote followed by anything else is escaped. That rule
    never fires on valid JSON, which is why it is safe to apply; it cannot
    rescue an inner quote that happens to sit right before a comma.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    length = len(text)

    for index, char in enumerate(text):
        if escaped:
            escaped = False
            out.append(char)
            continue

        if char == "\\":
            escaped = True
            out.append(char)
            continue

        if char == '"':
            if not in_string:
                in_string = True
            else:
                ahead = index + 1

                while ahead < length and text[ahead] in " \t\r\n":
                    ahead += 1

                if ahead < length and text[ahead] not in ",:}]":
                    out.append('\\"')
                    continue

                in_string = False

        out.append(char)

    return "".join(out)


def _fix_escape(match: re.Match) -> str:
    """`\'` is an apostrophe; any other stray backslash is kept as a literal one."""
    even, char = match.group(1), match.group(2)

    if char == "'":
        return even + "'"

    return even + "\\\\" + char


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
    # Square brackets, counted separately, only to recognise the one repair
    # below. Braces inside an object answer are what `depth` tracks.
    lists = 0
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

        if opening == "{":
            if char == "[":
                lists += 1
            elif char == "]":
                lists -= 1

    body = text[start:].rstrip()

    # The one gap that is safe to close: everything is finished except the
    # outermost object, and the answer ends on the `]` of its list. Seen from
    # llama-3.2-11b on quizzes, attempt after attempt at temperature 0:
    # `{"questions": [{...}, {...}]` and then nothing. Every item is complete
    # and the only valid ending is the one brace -- unlike real truncation,
    # which stops inside a string or an unclosed item (`{"pages": [{...}` with
    # more to come), where completing it would store something silently short.
    # Those still fall through and fail below.
    if opening == "{" and depth == 1 and lists == 0 and not in_string and body.endswith("]"):
        return body + "}"

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

    Also closes an outermost object left open after its list was finished --
    the one missing brace, see `_balanced`; nothing is guessed there.

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
    stripped = _BAD_ESCAPE.sub(_fix_escape, stripped)
    stripped = _TRAILING_COMMA.sub(r"\1", stripped)

    # Only when the text does not already parse: the rule is safe on valid
    # JSON, but there is no reason to run it there.
    try:
        json.loads(stripped)
    except ValueError:
        stripped = _escape_inner_quotes(stripped)

    return stripped


def schema_spec(schema: type[BaseModel], max_items: int | None = None) -> dict:
    """The JSON Schema to hand a provider that can constrain its answer to one.

    The same model the prompt and the validator use, minus the worked `example`
    (that is for the prompt, not the decoder). With *max_items*, the schema's
    one list gets a `maxItems`: a decoder constrained to the schema cannot ramble
    past the end of it, which is what a prompt that says "at most 3" never
    achieved -- llama-3.2-11b answered a three-question batch with 67. Applied to
    the schema sent, not to the model that validates, so a provider that cannot
    constrain still produces an answer the caller can truncate rather than reject.
    """
    spec = schema.model_json_schema()
    spec.pop("example", None)

    shape = _list_field(schema)

    if max_items is not None and shape is not None:
        spec["properties"][shape[0]]["maxItems"] = max_items

    return spec


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


def _size(result: BaseModel) -> int:
    """How many items a salvaged set holds."""
    return len(getattr(result, next(iter(type(result).model_fields))))


def _list_field(schema: type[BaseModel]) -> tuple[str, type[BaseModel]] | None:
    """The one list-of-models field of *schema*, if that is its whole shape.

    Salvage only makes sense for a set of independent items -- a quiz, a deck,
    a batch of summaries -- where eight good questions are worth keeping when
    the ninth is broken. A schema with any other shape is not salvaged.
    """
    fields = list(schema.model_fields.items())

    if len(fields) != 1:
        return None

    name, info = fields[0]
    args = get_args(info.annotation)

    if get_origin(info.annotation) is list and args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
        return name, args[0]

    return None


def _finished_items(text: str) -> list[str]:
    """*text* cut back to each complete item, latest first, and closed.

    For `{"questions": [{...}, {...}, {"question": "cut off mid-sen` the first
    candidate is `{"questions": [{...}, {...}]}` -- only what the model
    finished, nothing invented to finish what it did not. Every earlier cut
    follows, because the break is not always at the end: a summary batch was
    lost to one malformed character at column 4062 of an answer whose first
    items were fine, and the latest cut still contains that character.
    Empty when not one item was completed.
    """
    found = text.find("{")

    if found < 0:
        return []

    stack: list[str] = []
    in_string = False
    escaped = False
    item_ends: list[int] = []

    for index in range(found, len(text)):
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

        if char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                break
            stack.pop()

            # Back to "inside the outer object's list": an item just closed.
            if stack == ["{", "["]:
                item_ends.append(index + 1)

            if not stack:
                # Balanced to the end. That does not make it valid -- a
                # missing comma balances fine -- so the cuts still stand; the
                # whole text is tried before any of them anyway.
                break

    return [text[found:end] + "]}" for end in reversed(item_ends)]


def salvage_items(raw: str, schema: type[BaseModel]) -> tuple[BaseModel, int] | None:
    """The valid items out of an answer that failed as a whole.

    Two things are repaired, both *after* the model has answered and without
    asking it again:

      * **Truncation.** A model in a repetition loop ran a quiz answer to
        19,075 characters and was cut off mid-string -- after finishing several
        perfectly good questions. Those are kept; the unfinished one is not.
      * **One bad item.** A question offering the same option twice fails the
        set, and the eight good questions beside it went with it. Each item is
        validated on its own and only the bad ones are dropped.

    Returns the set and how many items were dropped, or None when nothing
    usable is left. Only for schemas that are a single list of items -- see
    `_list_field` -- and only as a fallback: `generate_structured` still asks
    the model again first, because a complete answer beats a salvaged one.
    """
    shape = _list_field(schema)

    if shape is None:
        return None

    name, item_type = shape
    cleaned = extract_json(raw)
    candidates = [cleaned, *_finished_items(cleaned)]

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue

        items = data.get(name) if isinstance(data, dict) else None

        if not isinstance(items, list):
            continue

        kept = []

        for item in items:
            try:
                kept.append(item_type.model_validate(item))
            except ValidationError:
                continue

        if kept:
            return schema.model_validate({name: kept}), len(items) - len(kept)

    return None


async def generate_structured(
    client,
    prompt: str,
    schema: type[BaseModel],
    *,
    retries: int = 2,
    max_tokens: int | None = None,
    salvage: bool = False,
    max_items: int | None = None,
    attempt_timeout: float | None = None,
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

    A provider that can constrain its answer to a JSON Schema (`ENFORCES_SCHEMA`)
    is handed one -- see `schema_spec`, and `max_items` for capping the list --
    so the answer cannot leave the shape in the first place. The prompt still
    carries the schema and the repair loop still runs: a model behind a hosted
    endpoint may ignore the constraint (llama-3.2-11b on NVIDIA does), and then
    this behaves as it always did.

    `attempt_timeout` bounds each call. A model in a repetition run writes until
    it is out of tokens, which on a slow endpoint is minutes per attempt; a
    timed-out attempt counts as a failed one and is retried or salvaged like any
    other.

    With `salvage`, a set of independent items does not have to fail whole:
    when every attempt has failed, the valid items are taken from the best
    attempt (`salvage_items`) instead. Off by default, because for a page of
    text a partial answer is a silently short document, not a smaller deck.
    """
    instructed = f"{prompt}\n\n{schema_instruction(schema)}"
    last_error: Exception | None = None
    raw = ""
    best: tuple[BaseModel, int] | None = None

    # Only passed to a client that says it can use it: a stand-in, or a provider
    # that has not been taught, keeps the call it always had.
    extra = {"json_schema": schema_spec(schema, max_items)} if getattr(client, "ENFORCES_SCHEMA", False) else {}

    for attempt in range(retries + 1):
        call = client.generate_text(prompt=instructed, max_tokens=max_tokens, temperature=0, **extra)

        try:
            raw = await (asyncio.wait_for(call, attempt_timeout) if attempt_timeout else call)
        except (asyncio.TimeoutError, TimeoutError):
            raw = ""
            last_error = TimeoutError(f"no answer within {attempt_timeout:g}s")
            logger.warning(
                "Structured output for %s timed out after %gs (attempt %d of %d)",
                schema.__name__,
                attempt_timeout,
                attempt + 1,
                retries + 1,
            )

            if attempt == retries:
                break

            continue

        try:
            return schema.model_validate_json(extract_json(raw))
        except (ValidationError, ValueError) as exc:
            last_error = exc

            if salvage:
                rescued = salvage_items(raw, schema)

                # Keep the attempt that rescued the most, not the latest.
                if rescued is not None and (best is None or _size(rescued[0]) > _size(best[0])):
                    best = rescued

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

    if best is not None:
        rescued, dropped = best
        logger.warning(
            "%s failed %d attempt(s); kept %d valid item(s) from the answer and dropped %d: %s",
            schema.__name__,
            retries + 1,
            _size(rescued),
            dropped,
            str(last_error)[:300].replace("\n", " "),
        )
        return rescued

    raise StructuredOutputError(
        f"{schema.__name__} could not be parsed from the model's output after "
        f"{retries + 1} attempt(s): {last_error}",
        raw=raw,
    )
