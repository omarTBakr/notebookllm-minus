"""The gemma4 correction pass: what it is allowed to change, and what happens
when it misbehaves.

The pass rewrites a page, and a rewrite is indistinguishable from a fabrication
once it has been stored. Every test here is about a way that goes wrong
*silently* -- a summary that parses cleanly, a schema echoed back as an empty
answer, a page number the model invented, a highlight that stops pointing at the
right part of the paper. None of them raises on its own.
"""

import json

import pytest

from application.services.ingest import CorrectionSet, TextCorrectionService

PAGE = "The quick brown fox jumps over the lazy dog and keeps on running. " * 8


class FakeClient:
    """Returns canned responses in order, recording what it was asked.

    Shaped like the two ad-hoc fakes already in the suite (test_structured_output,
    test/tasks/test_studio): `generate_structured` only ever calls
    `generate_text`, so that is the whole surface a correction client needs.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate_text(self, prompt, max_tokens=None, temperature=None, **_):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


def _answer(pages):
    """*pages* are (batch-local number, text) -- 1-based, as the prompt numbers them."""
    return json.dumps({"pages": [{"num": n, "text": t} for n, t in pages]})


@pytest.fixture
def controller():
    def build(client, **overrides):
        controller = TextCorrectionService(client)
        for key, value in overrides.items():
            setattr(controller, key, value)
        return controller

    return build


# --- the happy path, and the invariant it has to preserve ---------------------


async def test_an_unchanged_page_is_still_recorded_with_a_scale_of_one(controller):
    """A clean page comes back identical, and must still carry scale 1.0.

    The scale is what `highlight_metadata` multiplies a chunk's offsets by. A
    page whose text did not change needs 1.0 exactly -- anything else moves a
    citation rectangle off the words it is quoting, on a page nothing was wrong
    with."""
    client = FakeClient(_answer([(1, PAGE)]))

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result[0]["text"] == PAGE
    assert result[0]["scale"] == 1.0


async def test_a_repaired_page_records_the_length_ratio(controller):
    """Joining split words shortens the page, so offsets into the corrected
    string no longer index the original the boxes were measured against. The
    ratio is the only thing that maps one onto the other."""
    original = "inter national govern ment " * 20
    repaired = "international government " * 20

    client = FakeClient(_answer([(1, repaired)]))

    result = await controller(client).correct([{"page_index": 0, "text": original}])

    assert result[0]["text"] == repaired
    assert result[0]["scale"] == pytest.approx(len(original) / len(repaired))
    assert result[0]["scale"] > 1.0, "a shorter correction must scale offsets up"


# --- the failures that produce well-formed, wrong answers ---------------------


async def test_a_summarised_page_is_rejected(controller):
    """The single most likely bad answer, and the one no schema can catch.

    Asked to reproduce a page, a small model will sometimes summarise it. The
    result is fluent, valid JSON, the right shape, and a quarter the length --
    so it validates, `generate_structured` calls it a success, and the page is
    replaced by a paraphrase of itself that then gets indexed and cited."""
    client = FakeClient(_answer([(1, "The passage is about a fox and a dog.")]))

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}, "a page that came back a fraction of its length was accepted"


async def test_a_page_that_doubled_in_length_is_rejected(controller):
    """The other direction: the model answered the page, or continued it, or
    explained itself at length. Same verdict, same reason."""
    client = FakeClient(_answer([(1, PAGE + " " + PAGE + " " + PAGE)]))

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}


async def test_a_number_outside_the_batch_is_dropped(controller):
    """A correction under a number that was never shown belongs to no page.

    This is not hypothetical. Shown a 0-based page_index, gemma4 answered 12
    for the page whose index was 11 and whose text began "Page 12" -- it took
    the number printed on the paper. The numbering is batch-local now so the
    two cannot be confused, and anything outside it is still dropped rather
    than attached by position."""
    client = FakeClient(_answer([(7, PAGE)]))

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}


async def test_a_page_number_printed_in_the_text_is_not_mistaken_for_the_label(controller):
    """The regression the numbering change exists for: a page whose body starts
    with its own printed number still maps back to its index."""
    body = "Page 12 " + PAGE
    client = FakeClient(_answer([(1, "Page 12 " + PAGE)]))

    result = await controller(client).correct([{"page_index": 11, "text": body}])

    assert 11 in result, "the correction was not mapped back to its page index"


async def test_an_empty_correction_is_rejected(controller):
    """An empty page would replace real text with nothing and index it."""
    client = FakeClient(json.dumps({"pages": [{"num": 1, "text": "   "}]}))

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}


# --- failures that should not take the upload down ---------------------------


async def test_a_model_that_never_returns_valid_json_keeps_the_extracted_text(controller):
    """The local model being down, slow or bad at JSON is not a reason to fail
    an upload whose text layer was already readable. The page keeps what
    PyMuPDF gave it and ingestion continues."""
    client = FakeClient("I'm afraid I can't do that.", "Still not JSON.", "Nor this.")

    result = await controller(client).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}


async def test_a_client_that_raises_keeps_the_extracted_text(controller):
    """A dead socket mid-batch, rather than a bad answer."""

    class Broken:
        async def generate_text(self, *a, **k):
            raise ConnectionError("ollama serve is not running")

    result = await controller(Broken()).correct([{"page_index": 0, "text": PAGE}])

    assert result == {}


# --- the schema itself --------------------------------------------------------


def test_the_page_list_is_required_not_defaulted():
    """`Field(...)`, never `default_factory=list`.

    With a default, a model that echoes the schema back instead of filling it
    in validates cleanly as zero pages: `generate_structured` reports success,
    its repair loop never fires, and every page in the batch silently keeps its
    damaged text -- which looks exactly like the feature being switched off."""
    assert CorrectionSet.model_json_schema()["required"] == ["pages"]

    with pytest.raises(Exception):
        CorrectionSet.model_validate_json("{}")


def test_the_schema_carries_a_worked_example():
    """schema_instruction pops `example` and shows it last. Without one, a small
    model given only a specification tends to return the specification."""
    example = CorrectionSet.model_config["json_schema_extra"]["example"]

    assert CorrectionSet.model_validate(example).pages[0].num == 1


# --- batching ------------------------------------------------------------------


async def test_pages_are_split_into_calls_of_the_configured_size(controller):
    """One call per page by default. The batch is the queueing unit; the call
    size is a separate knob, and conflating them is what makes one bad page
    take out nine good ones."""
    pages = [{"page_index": i, "text": PAGE} for i in range(4)]

    # One call per page, so every answer is numbered 1 within its own call.
    client = FakeClient(*[_answer([(1, PAGE)]) for _ in range(4)])

    result = await controller(client, pages_per_call=1).correct(pages)

    assert len(client.prompts) == 4, "expected one model call per page"
    assert sorted(result) == [0, 1, 2, 3]


async def test_the_prompt_forbids_translating_and_summarising(controller):
    """The two rules the model breaks most often have to actually reach it."""
    client = FakeClient(_answer([(1, PAGE)]))

    await controller(client).correct([{"page_index": 0, "text": PAGE}])

    prompt = client.prompts[0].lower()

    assert "do not translate" in prompt
    assert "do not summarise" in prompt
    assert PAGE.strip()[:40].lower() in prompt, "the page itself never reached the model"


# --- pages with nothing on them -----------------------------------------------


async def test_a_blank_page_never_reaches_the_model(controller):
    """A page with no text layer has nothing to repair, and sending it loses
    the whole call it travels in: the model answers a blank page with a blank
    string, CorrectedPage requires a non-empty one, and the group fails
    validation three times and is dropped. Measured on a 222-page book with six
    blank pages -- every batch carrying one lost all ten of its pages."""
    client = FakeClient(_answer([(1, PAGE)]))

    result = await controller(client, pages_per_call=10).correct(
        [
            {"page_index": 0, "text": "   "},
            {"page_index": 1, "text": ""},
            {"page_index": 2, "text": PAGE},
        ]
    )

    assert len(client.prompts) == 1, "expected one call for the one page worth correcting"
    assert "PAGE 2" not in client.prompts[0], "a blank page was numbered into the prompt"
    assert sorted(result) == [2], "only the page with text should come back corrected"


async def test_an_entirely_blank_batch_costs_no_call(controller):
    """A cover, a plate section, a scanned insert: no text, no model call."""
    client = FakeClient(_answer([(1, PAGE)]))

    result = await controller(client).correct(
        [{"page_index": 0, "text": ""}, {"page_index": 1, "text": "  \n "}]
    )

    assert result == {}
    assert client.prompts == [], "a batch with no text still called the model"
