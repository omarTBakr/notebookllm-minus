"""Getting a specific shape out of a model, and refusing anything else.

This is the layer every Studio feature stands on: a flashcard, a quiz question
and a mind-map node are all "the model must return exactly this structure,
hundreds of times". Prose is not a useful answer, and half a deck is worse than
none — so the interesting behaviour here is what happens when the model does
not comply.
"""

import json

import pytest
from pydantic import BaseModel, Field

from application.services.llm.structured_generation import (
    extract_json,
    generate_structured,
    schema_instruction,
)
from shared.exceptions import StructuredOutputError


class Card(BaseModel):
    front: str
    back: str
    chunk_order: int = Field(ge=0)


class Deck(BaseModel):
    cards: list[Card]


class FakeClient:
    """Returns canned responses in order, and records what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.temperatures: list[float | None] = []

    async def generate_text(self, prompt, max_tokens=None, temperature=None, **_):
        self.prompts.append(prompt)
        self.temperatures.append(temperature)

        return self.responses.pop(0)


VALID = json.dumps({"cards": [{"front": "Q", "back": "A", "chunk_order": 3}]})


# --- unwrapping what models put around JSON ------------------------------------


def test_a_fenced_response_is_unwrapped():
    """Models wrap JSON in fences despite being told not to. Charging that to
    the schema measures instruction-following, not the answer — the same
    mistake that scored an OCR model at four times its real error rate."""
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json("```\n[1, 2]\n```") == "[1, 2]"


def test_a_preamble_is_discarded():
    """ "Here is the JSON you asked for:" survives any amount of prompting."""
    assert extract_json('Sure! Here it is:\n{"a": 1}') == '{"a": 1}'


def test_bare_json_is_untouched():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_broken_json_is_not_repaired():
    """Forgiving in one direction only. A truncated object is a failure worth
    seeing — guessing at the missing half would invent content."""
    truncated = '{"cards": [{"front": "Q"'

    assert extract_json(truncated) == truncated


# --- the schema travels with the prompt ----------------------------------------


def test_the_prompt_carries_the_schema():
    """Generated from the Pydantic model, never written by hand, so a renamed
    field cannot leave the prompt asking for the old one — a drift that fails
    every generation for a reason nobody can see."""
    instruction = schema_instruction(Deck)

    assert "chunk_order" in instruction
    assert "front" in instruction and "back" in instruction


async def test_a_valid_response_is_parsed_into_the_model():
    client = FakeClient(VALID)

    deck = await generate_structured(client, "make one card", Deck)

    assert isinstance(deck, Deck)
    assert deck.cards[0].front == "Q"
    assert deck.cards[0].chunk_order == 3


async def test_extraction_runs_at_temperature_zero():
    """This is extraction, not writing. Sampling invents plausible structure
    where the source is thin, which scores worse than an honest failure and
    reads as though it worked."""
    client = FakeClient(VALID)

    await generate_structured(client, "make one card", Deck)

    assert client.temperatures == [0]


# --- what happens when the model does not comply -------------------------------


async def test_a_malformed_answer_is_retried_with_its_own_error():
    """The cheapest correction available: most models fix a named field on the
    second attempt. Repeating the schema alone does not help — it already had
    that and still got it wrong."""
    client = FakeClient("not json at all", VALID)

    deck = await generate_structured(client, "make one card", Deck)

    assert deck.cards[0].back == "A"
    assert len(client.prompts) == 2
    assert "could not be parsed" in client.prompts[1]


async def test_the_retry_shows_the_model_what_it_said():
    client = FakeClient('{"cards": [{"front": "Q"}]}', VALID)

    await generate_structured(client, "make one card", Deck)

    # Both the offending text and the validation error, or the model is being
    # asked to guess what was wrong.
    assert '"front": "Q"' in client.prompts[1]
    assert "back" in client.prompts[1]


async def test_a_schema_violation_is_retried_not_accepted():
    """Valid JSON that is the wrong shape must not slip through — a negative
    chunk_order cites a page that cannot exist."""
    client = FakeClient(json.dumps({"cards": [{"front": "Q", "back": "A", "chunk_order": -1}]}), VALID)

    deck = await generate_structured(client, "make one card", Deck)

    assert deck.cards[0].chunk_order == 3


async def test_exhausted_retries_raise_rather_than_return_something_partial():
    client = FakeClient("nope", "still nope", "nope again")

    with pytest.raises(StructuredOutputError) as caught:
        await generate_structured(client, "make one card", Deck, retries=2)

    assert "Deck" in str(caught.value)
    # The raw text rides along: "validation failed" with nothing that failed is
    # unactionable in a log.
    assert caught.value.raw == "nope again"


async def test_the_retry_budget_is_honoured_exactly():
    client = FakeClient("a", "b", "c")

    with pytest.raises(StructuredOutputError):
        await generate_structured(client, "p", Deck, retries=2)

    assert len(client.prompts) == 3, "retries=2 means one attempt plus two retries"


async def test_no_retry_means_one_attempt():
    client = FakeClient("nope")

    with pytest.raises(StructuredOutputError):
        await generate_structured(client, "p", Deck, retries=0)

    assert len(client.prompts) == 1


# --- the answer that is JSON, and still not an answer ---------------------------


async def test_the_schema_echoed_back_is_not_a_valid_answer():
    """A real failure, from a real run, that this loop was blind to.

    Asked for cards from ten summaries, llama-3.2-11b returned the JSON Schema
    it had been given — `$defs`, `properties`, `title` — rather than anything
    filling it. That is valid JSON and parses cleanly, so the only thing
    standing between it and being accepted is whether the set's list field is
    required. It was not: `default_factory=list` made every JSON object on
    earth a valid empty deck, so the batch was accepted as "zero cards", no
    retry fired, and the deck came out half size with nothing in the log.

    Required means this is a ValidationError and gets retried, which is what
    the repair loop is for.
    """
    echoed = json.dumps(
        {
            "$defs": {"Card": {"type": "object", "title": "Card"}},
            "properties": {"cards": {"type": "array"}},
            "title": "Deck",
            "type": "object",
        }
    )

    client = FakeClient(echoed, VALID)
    deck = await generate_structured(client, "prompt", Deck, retries=1)

    assert len(client.prompts) == 2, "the echoed schema was accepted instead of retried"
    assert deck.cards[0].front == "Q"


async def test_an_explicitly_empty_set_is_accepted_without_a_retry():
    """The other half of the same rule.

    "These summaries support no good card" is an answer the prompts explicitly
    invite, and a model that says so with `{"cards": []}` has engaged with the
    question. Retrying it would burn the budget arguing with a model that is
    right — the key being present is what separates it from the echo above.
    """
    client = FakeClient(json.dumps({"cards": []}))
    deck = await generate_structured(client, "prompt", Deck, retries=2)

    assert deck.cards == []
    assert len(client.prompts) == 1


async def test_the_example_is_what_a_weak_model_copies():
    """Why schema_instruction carries a worked example.

    Given only a JSON Schema, llama-3.2-11b answered a quiz request with the
    schema itself on all three attempts, so every quiz on that model failed
    outright. The example is the concrete thing to copy, and it goes last
    because the nearest concrete text is what gets echoed — better an answer
    than a specification.
    """

    class Example(BaseModel):
        cards: list[Card]
        model_config = {"json_schema_extra": {"example": {"cards": [{"front": "F", "back": "B", "chunk_order": 1}]}}}

    instruction = schema_instruction(Example)

    assert '"front": "F"' in instruction, "the example is not in the prompt"

    # After the schema, not before it.
    assert instruction.index('"front": "F"') > instruction.index("must match this schema")

    # And the example is not left sitting in the schema the validator describes.
    assert "example" not in json.loads(instruction[instruction.index("{") : instruction.index("\n\nAnswer in exactly")])


# --- what a model actually puts around its JSON --------------------------------
#
# Every case here was observed on a live Arabic ingest, where one bad character
# in 20KB of correct output failed the parse and cost a full retry -- three
# attempts, minutes of generation, and a page that kept its damaged text.


def test_a_stray_closing_brace_does_not_swallow_the_answer():
    """`trailing characters at line 1 column 20611`. A greedy match ran from the
    first brace to the last one anywhere in the response, so one appended `}`
    made a perfectly good object unparseable."""
    import json

    from application.services.llm.structured_generation import extract_json

    assert json.loads(extract_json('{"pages": [{"num": 1, "text": "a"}]}}'))["pages"]


def test_a_trailing_comma_is_dropped():
    """`trailing comma at line 6 column 5`. Illegal JSON, emitted anyway, and
    unambiguous to undo -- there is nothing to guess at."""
    import json

    from application.services.llm.structured_generation import extract_json

    assert json.loads(extract_json('{"pages": [{"num": 1, "text": "a",}]}'))["pages"]


def test_a_control_character_inside_a_string_is_stripped():
    """`control character (\\u0000-\\u001F) found while parsing a string`."""
    import json

    from application.services.llm.structured_generation import extract_json

    raw = '{"pages": [{"num": 1, "text": "a' + chr(0) + 'b"}]}'

    assert json.loads(extract_json(raw))["pages"][0]["text"] == "ab"


def test_a_brace_inside_the_page_text_is_not_a_brace():
    """Page text contains braces, and counting depth without tracking strings
    would end the object in the middle of a sentence."""
    import json

    from application.services.llm.structured_generation import extract_json

    raw = '{"pages": [{"num": 1, "text": "a } b"}]}'

    assert json.loads(extract_json(raw))["pages"][0]["text"] == "a } b"


def test_a_truncated_answer_is_still_a_failure():
    """The one thing extract_json must never paper over. A model that ran out
    of budget mid-string -- usually in a repetition loop -- produced half a
    page, and completing it would store a document that is silently short."""
    import json

    import pytest as _pytest

    from application.services.llm.structured_generation import extract_json

    with _pytest.raises(json.JSONDecodeError):
        json.loads(extract_json('{"pages": [{"num": 1, "text": "remained remained'))


def test_a_finished_list_missing_only_the_last_brace_is_closed():
    """llama-3.2-11b, on quizzes, at temperature 0, on every retry: the list of
    questions complete and closed, the outer object never. The only valid
    ending is one brace, so it is added rather than the batch thrown away."""
    import json

    from application.services.llm.structured_generation import extract_json

    raw = '{"questions": [{"question": "Q", "wrong_answers": ["a", "b", "c"]}]'

    assert json.loads(extract_json(raw))["questions"][0]["question"] == "Q"


def test_an_unfinished_item_is_not_closed():
    """The truncation the repair must not reach: an item still open, or a list
    with more to come. Completing either would store something silently short."""
    import json

    import pytest as _pytest

    from application.services.llm.structured_generation import extract_json

    for raw in (
        '{"pages": [{"num": 1, "text": "a"}',
        '{"pages": [{"num": 1, "text": "a"},',
        '{"questions": [{"wrong_answers": ["a", "b"]',
        '{"questions": [{"question": "ends in ]',
    ):
        with _pytest.raises(json.JSONDecodeError):
            json.loads(extract_json(raw))


# --- salvaging the good items when the answer as a whole fails ------------------

TRUNCATED = (
    '{"cards": [{"front": "Q1", "back": "A1", "chunk_order": 1}, '
    '{"front": "Q2", "back": "A2", "chunk_order": 2}, {"front": "Q3", "back": "remained remained'
)


async def test_a_truncated_set_keeps_the_items_the_model_finished():
    """A quiz answer ran to 19,075 characters in a repetition loop and was cut
    off mid-string -- after several good questions. Those are kept."""
    client = FakeClient(TRUNCATED, TRUNCATED, TRUNCATED)

    deck = await generate_structured(client, "make cards", Deck, salvage=True)

    assert [card.front for card in deck.cards] == ["Q1", "Q2"]


async def test_one_bad_item_does_not_sink_the_rest():
    bad = json.dumps({"cards": [{"front": "Q1", "back": "A1", "chunk_order": 1}, {"front": "Q2", "chunk_order": 2}]})
    client = FakeClient(bad, bad, bad)

    deck = await generate_structured(client, "make cards", Deck, salvage=True)

    assert [card.front for card in deck.cards] == ["Q1"]


async def test_salvage_is_a_fallback_not_a_shortcut():
    """A complete answer beats a salvaged one, so the model is still asked
    again first -- the salvage is only what is left when it never gets there."""
    client = FakeClient(TRUNCATED, VALID)

    deck = await generate_structured(client, "make cards", Deck, salvage=True)

    assert [card.front for card in deck.cards] == ["Q"]
    assert len(client.prompts) == 2


async def test_the_attempt_that_rescued_most_is_kept():
    one = '{"cards": [{"front": "Q1", "back": "A1", "chunk_order": 1}, {"front": "cut'
    client = FakeClient(TRUNCATED, one, "not json")

    deck = await generate_structured(client, "make cards", Deck, salvage=True)

    assert len(deck.cards) == 2


async def test_without_salvage_a_truncated_answer_still_fails():
    """Off by default: for a page of text a partial answer is a silently short
    document, not a smaller deck."""
    client = FakeClient(TRUNCATED, TRUNCATED, TRUNCATED)

    with pytest.raises(StructuredOutputError):
        await generate_structured(client, "make cards", Deck)


async def test_nothing_finished_is_still_a_failure():
    cut = '{"cards": [{"front": "Q1", "back": "cut'
    client = FakeClient(cut, cut, cut)

    with pytest.raises(StructuredOutputError):
        await generate_structured(client, "make cards", Deck, salvage=True)


def test_an_invalid_escape_is_repaired_not_dropped():
    """llama-3.2-11b writes \\' for an apostrophe, which JSON forbids; one of
    them failed a batch of ten summaries three times. Any other stray
    backslash is kept as a literal one, so a path is not mangled."""
    raw = '{"a": "it\\\'s in C:\\Users, and \\\\n stays"}'

    assert json.loads(extract_json(raw)) == {"a": "it's in C:\\Users, and \\n stays"}


async def test_a_break_mid_answer_keeps_the_items_before_it():
    """The break is not always at the end. One malformed character in the
    middle still leaves the items before it usable."""
    broken = (
        '{"cards": [{"front": "Q1", "back": "A1", "chunk_order": 1}, '
        '{"front": "Q2", "back": "A2" "chunk_order": 2}, '
        '{"front": "Q3", "back": "A3", "chunk_order": 3}]}'
    )
    client = FakeClient(broken, broken, broken)

    deck = await generate_structured(client, "make cards", Deck, salvage=True)

    assert [card.front for card in deck.cards] == ["Q1"]


def test_a_bare_quote_inside_a_string_is_escaped():
    """A summary batch failed at column 79, three attempts running, on
    `"The term "large language model" means..."`."""
    raw = '{"summaries": [{"num": 1, "summary": "The term "large language model" means an LLM."}]}'

    assert json.loads(extract_json(raw))["summaries"][0]["summary"] == 'The term "large language model" means an LLM.'


def test_valid_json_is_never_touched_by_the_quote_repair():
    from application.services.llm.structured_generation import _escape_inner_quotes

    valid = '{"a": "x", "b": ["y", "z"], "c": {"d": "e"}, "f": "g \\" h"}'

    assert _escape_inner_quotes(valid) == valid
