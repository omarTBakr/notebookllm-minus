"""Generating a deck, a batch at a time.

The behaviours here are the ones the design exists for, and each would be
invisible if it broke: items appear before the book is finished, a second run
costs no summarising, and a card that cannot be traced to a page is dropped
rather than served.
"""

import json

import pytest

from enums import ArtifactKind, ArtifactStatus
from models.db_schema import DataChunk
from tasks import studio


class FakeClient:
    """Summarises and generates without a model.

    `generate_text` answers both prompts: the summariser asks for prose and the
    generator asks for JSON, told apart by the schema instruction that
    generate_structured appends.
    """

    def __init__(self, items_per_call=2, summaries_returned=None):
        self.items_per_call = items_per_call
        # None means "one per excerpt". A number short of the batch reproduces
        # a model that answers a ten-excerpt batch with fewer than ten lines.
        self.summaries_returned = summaries_returned
        self.summarise_calls = 0
        self.generate_calls = 0
        self.orders_seen: list[list[int]] = []

    async def generate_text(self, prompt, max_tokens=None, temperature=None, **_):
        # Both calls are structured now, told apart by which block the prompt
        # carries: excerpts go to the summariser, summaries to the generator.
        if "### Excerpt" in prompt:
            self.summarise_calls += 1
            # Each summary echoes the excerpt's own text, not its number. That
            # is what makes a misdelivered summary visible: "summary of content
            # 7" sitting on chunk 3 is obviously wrong, where "summary of
            # excerpt 1" is ambiguous once the numbering restarts per batch.
            lines = prompt.splitlines()
            seen = []

            for i, line in enumerate(lines):
                if line.startswith("### Excerpt"):
                    seen.append((int(line.split()[-1]), lines[i + 1].strip()))

            return json.dumps(
                {
                    "summaries": [
                        {"num": num, "summary": f"summary of {content}"}
                        for num, content in seen[: self.summaries_returned or len(seen)]
                    ]
                }
            )

        self.generate_calls += 1
        orders = [
            int(line.split()[-1])
            for line in prompt.splitlines()
            if line.startswith("### Summary")
        ]
        self.orders_seen.append(orders)

        key = "cards" if "flashcard" in prompt.lower() else "questions"
        made = []

        for order in orders[: self.items_per_call]:
            if key == "cards":
                made.append(
                    {"front": f"Q{order}", "back": f"A{order}", "chunk_order": order}
                )
            else:
                made.append(
                    {
                        "question": f"Q{order}",
                        "options": ["a", "b", "c", "d"],
                        "answer_index": 0,
                        "chunk_order": order,
                    }
                )

        return json.dumps({key: made})


@pytest.fixture
def notebook(fake_db, monkeypatch):
    """A notebook with 25 chunks, and the task pointed at fakes."""

    async def build(chunk_count=25, client=None):
        from models.db_schema import Chat, Project

        # The task reads the chat for its language and generation model, and
        # the project for the ObjectId that chunks are keyed by -- the two
        # identifiers this codebase has already confused once.
        fake_db.chats().items["c1"] = Chat(
            chat_id="c1", session_id="s1", user_id="u1", title="A notebook"
        )
        # Only minted once. Re-running this fixture with a fresh Project would
        # give it a new ObjectId and orphan the chunks from the previous call,
        # which is precisely what a "second run reuses summaries" test must not
        # do to itself.
        if "c1" not in fake_db.projects().items:
            fake_db.projects().items["c1"] = Project(project_id="c1", name="A notebook")

        project = await fake_db.projects().get_project("c1")

        await fake_db.chunks().create_chunks(
            [
                DataChunk(
                    project_id=project.id,
                    asset_id="a1",
                    chunk_order=i,
                    chunk_content=f"content {i}",
                )
                for i in range(chunk_count)
            ]
        )

        the_client = client or FakeClient()

        class FakeProviders:
            def chatting(self, *_a, **_k):
                return the_client

            async def aclose_all(self):
                pass

        monkeypatch.setattr(studio, "DbFactory", lambda _s: _Factory(fake_db))
        monkeypatch.setattr(studio, "ProviderCache", lambda _s: FakeProviders())

        return the_client

    return build


class _Factory:
    def __init__(self, db):
        self._db = db

    def create(self):
        return self._db


async def test_items_appear_before_the_whole_book_is_processed(notebook, fake_db):
    """The reason this is a streaming loop and not summarise-then-generate.

    With 25 chunks the set is written four times, so a reader sees cards long
    before the last chunk is reached. Summarising everything first would be
    minutes of blank screen.

    The batches are 3, 10, 10, 2: the first is deliberately small so the first
    cards arrive in seconds rather than after ten summaries and a generation
    call. Ten is the steady-state size once something is on screen.
    """
    client = await notebook(chunk_count=25)

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    assert client.generate_calls == 4
    assert client.summarise_calls == 4

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assert artifact.status == ArtifactStatus.COMPLETE.value
    assert len(artifact.items) == 8, "two items per batch across four batches"


async def test_a_second_run_does_no_summarising(notebook, fake_db):
    """Summaries are written back onto the chunks, so the expensive half is
    paid once per notebook rather than once per deck."""
    first = await notebook(chunk_count=20)
    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    # Batches of 3, 10, 7 -- the first is small on purpose, see FIRST_BATCH.
    assert first.summarise_calls == 3

    second = FakeClient()
    await notebook(chunk_count=0, client=second)

    await studio._run_generation("c1", ArtifactKind.QUIZ.value)

    assert second.summarise_calls == 0, "summaries should have been reused"
    assert second.generate_calls == 3, "but items still had to be generated"


async def test_regenerating_replaces_rather_than_accumulates(notebook, fake_db):
    await notebook(chunk_count=10)

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)
    first = await fake_db.artifacts().find_artifact("c1", "flashcards")

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)
    second = await fake_db.artifacts().find_artifact("c1", "flashcards")

    assert len(second.items) == len(first.items), "a rerun doubled the deck"

    # The id is stable, deliberately. The row is an upsert on (chat_id, kind),
    # so that pair is the real identity of "this notebook's flashcards"; the
    # surrogate id naming the same row differently after every regeneration
    # served no reader, and the browser polls by kind rather than by id.
    # Replacement is what the length assertion above pins.
    assert second.artifact_id == first.artifact_id


async def test_an_item_citing_a_chunk_outside_its_batch_is_dropped(notebook, fake_db):
    """A citation pointing at the wrong chunk is worse than none: it opens the
    wrong page confidently. The model mostly carries chunk_order through, and
    when it does not the item is discarded."""

    class Liar(FakeClient):
        async def generate_text(self, prompt, **kw):
            answer = await super().generate_text(prompt, **kw)
            payload = json.loads(answer)

            if "cards" in payload:
                payload["cards"].append(
                    {"front": "made up", "back": "nowhere", "chunk_order": 9999}
                )
                return json.dumps(payload)

            return answer

    await notebook(chunk_count=10, client=Liar())

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assert all(item["chunk_order"] != 9999 for item in artifact.items)


async def test_a_failure_keeps_what_was_produced(notebook, fake_db, monkeypatch):
    """Twelve cards that stopped early beat an error page, and the status is
    what lets the UI say which it is."""

    class Flaky(FakeClient):
        async def generate_text(self, prompt, **kw):
            if self.generate_calls >= 1 and "schema" in prompt.lower():
                raise RuntimeError("provider fell over")

            return await super().generate_text(prompt, **kw)

    await notebook(chunk_count=30, client=Flaky())

    with pytest.raises(RuntimeError):
        await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assert artifact.status == ArtifactStatus.FAILED.value
    assert artifact.items, "the items produced before the failure were thrown away"
    assert "provider fell over" in artifact.error


async def test_a_notebook_with_no_chunks_is_refused(notebook, fake_db):
    await notebook(chunk_count=0)

    with pytest.raises(ValueError, match="no chunks"):
        await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)


async def test_quiz_questions_are_shaped_for_marking(notebook, fake_db):
    """Four options and an index, not answer text: comparing a user's choice by
    string would break on any whitespace the model varies between runs."""
    await notebook(chunk_count=10)

    await studio._run_generation("c1", ArtifactKind.QUIZ.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "quiz")
    assert artifact.items

    for item in artifact.items:
        assert len(item["options"]) == 4
        assert 0 <= item["answer_index"] <= 3


async def test_every_item_carries_the_asset_it_came_from(notebook, fake_db):
    """chunk_order alone cannot open a page.

    It counts *within one document*, so a notebook holding two files has two
    chunk 5s and "open chunk 5" would be a coin flip between them. The model
    has never seen an asset id, so the task attaches it from the chunk the
    summary actually came from.
    """
    await notebook(chunk_count=10)

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assert artifact.items

    for item in artifact.items:
        assert item.get("asset_id") == "a1", (
            "an item cannot be traced to a document, so its citation would "
            f"open whichever file happened to match: {item}"
        )


async def test_items_from_different_documents_cite_different_assets(notebook, fake_db):
    """The case the bug would have broken: two documents, overlapping
    chunk_order, and a citation that must still land on the right one."""
    from models.db_schema import DataChunk

    await notebook(chunk_count=5)
    project = await fake_db.projects().get_project("c1")

    # A second document whose chunk_order range overlaps the first exactly.
    await fake_db.chunks().create_chunks(
        [
            DataChunk(
                project_id=project.id,
                asset_id="a2",
                chunk_order=i,
                chunk_content=f"other {i}",
            )
            for i in range(5)
        ]
    )

    # One item per summary, so both documents are represented -- the default
    # fake answers only the first two summaries, which would both come from
    # the first document and prove nothing.
    await notebook(chunk_count=0, client=FakeClient(items_per_call=99))

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assets = {item["asset_id"] for item in artifact.items}

    assert assets == {
        "a1",
        "a2",
    }, f"items were all attributed to one document: {assets}"

    # And the orders are the real ones, not the batch-local numbering.
    a1_orders = {i["chunk_order"] for i in artifact.items if i["asset_id"] == "a1"}
    a2_orders = {i["chunk_order"] for i in artifact.items if i["asset_id"] == "a2"}
    assert a1_orders & a2_orders, (
        "the two documents should share chunk_order values -- that overlap is "
        "the whole reason the batch numbering has to be local"
    )


async def test_a_short_summarising_answer_leaves_chunks_unsummarised(notebook, fake_db):
    """The bug a real run found, which no fake had reproduced.

    A small model answering a multi-excerpt batch with a single line used to
    have that line assigned to *every* chunk in the batch -- so chunks carried
    a summary describing a different passage, and every card built from them
    cited the wrong one. Observed live: two chunks, one summary, both stored
    identically, all three generated cards citing chunk 0.

    A chunk the model skips must end up with no summary rather than someone
    else's. It stays unsummarised, so the next run picks it up.

    Asserted against each chunk's own text rather than against a count, so it
    cannot quietly re-pass when the batch sizes change: chunk 4 holds the
    summary of chunk 4's content, or it holds nothing.
    """
    # A model that describes only the first three excerpts of any batch.
    await notebook(chunk_count=10, client=FakeClient(summaries_returned=3))

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    project = await fake_db.projects().get_project("c1")
    chunks = [c async for c in fake_db.chunks().iter_project_chunks(project.id)]

    for chunk in chunks:
        if chunk.summary:
            assert (
                chunk.summary == f"summary of {chunk.chunk_content}"
            ), f"chunk {chunk.chunk_order} holds another chunk's summary"

    assert any(
        not c.summary for c in chunks
    ), "the skipped chunks were filled in from somewhere"
    assert any(c.summary for c in chunks), "nothing was summarised at all"


async def test_only_summarised_chunks_can_produce_items(notebook, fake_db):
    """A chunk with no summary contributes nothing, rather than being sent to
    the generator empty and inviting an invented card."""
    await notebook(chunk_count=10, client=FakeClient(summaries_returned=2))

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    project = await fake_db.projects().get_project("c1")
    chunks = {
        c.chunk_order: c async for c in fake_db.chunks().iter_project_chunks(project.id)
    }

    for item in artifact.items:
        assert chunks[
            item["chunk_order"]
        ].summary, f"item {item} cites a chunk that was never summarised"


async def test_a_question_offering_the_same_option_twice_is_not_stored(
    notebook, fake_db
):
    """Seen in a real run: four options, two of them the identical string.

    That question cannot be answered — if the repeated option is the correct
    one then two indexes are right and only one is accepted, and if it is wrong
    the question is a choice of three dressed up as four. Unlike an answer
    being *wrong*, which needs a model that can read, this is checkable, so it
    is checked and the model is asked again.
    """

    class Repeater(FakeClient):
        """Returns a duplicate pair on its first attempt, then behaves."""

        first = True

        async def generate_text(self, prompt, **kw):
            answer = await super().generate_text(prompt, **kw)
            payload = json.loads(answer)

            if "questions" in payload and payload["questions"] and Repeater.first:
                Repeater.first = False
                payload["questions"][0]["options"] = [
                    "same",
                    "same",
                    "other",
                    "another",
                ]
                return json.dumps(payload)

            return answer

    Repeater.first = True
    await notebook(chunk_count=3, client=Repeater())

    await studio._run_generation("c1", ArtifactKind.QUIZ.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "quiz")

    for item in artifact.items:
        options = [o.strip().casefold() for o in item["options"]]
        assert (
            len(set(options)) == 4
        ), f"stored a question with a repeated option: {item['options']}"



class MindMapClient(FakeClient):
    """FakeClient, plus the two mind-map calls.

    Topics come back two per batch like cards do. The outline puts odd-numbered
    topics on one branch and even ones on another, unless `outline_fails`, in
    which case it answers with something that is never valid JSON.
    """

    def __init__(self, outline_fails=False, **kwargs):
        super().__init__(**kwargs)
        self.outline_fails = outline_fails
        self.outline_calls = 0

    async def generate_text(self, prompt, max_tokens=None, temperature=None, **_):
        if "list of topics" in prompt:
            self.outline_calls += 1

            if self.outline_fails:
                return "not json"

            numbers = [int(line.split(".")[0]) for line in prompt.splitlines() if line[:1].isdigit()]

            return json.dumps(
                {
                    "branches": [
                        {"title": "Odd", "members": [n for n in numbers if n % 2]},
                        {"title": "Even", "members": [n for n in numbers if not n % 2] or [1]},
                    ]
                }
            )

        if "mind map" in prompt and "### Summary" in prompt:
            self.generate_calls += 1
            orders = [int(line.split()[-1]) for line in prompt.splitlines() if line.startswith("### Summary")]

            return json.dumps(
                {
                    "nodes": [
                        {"topic": f"T{order}", "detail": f"D{order}", "chunk_order": order}
                        for order in orders[: self.items_per_call]
                    ]
                }
            )

        return await super().generate_text(prompt, max_tokens=max_tokens, temperature=temperature)


async def test_a_mind_map_groups_every_topic_into_a_branch(notebook, fake_db):
    """Topics stream in per batch, then one outline call sees them all and
    each is stamped with its branch -- none lost, each still citable."""
    client = await notebook(chunk_count=25, client=MindMapClient())

    await studio._run_generation("c1", ArtifactKind.MIND_MAP.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "mindmap")

    assert artifact.status == ArtifactStatus.COMPLETE.value
    assert client.outline_calls == 1, "branches are chosen once, over the whole set"
    assert len(artifact.items) == 8
    assert {item["branch"] for item in artifact.items} == {"Odd", "Even"}
    assert all(item["asset_id"] == "a1" for item in artifact.items)

    branches = [item["branch"] for item in artifact.items]
    assert branches == sorted(branches, key=["Odd", "Even"].index), "items are ordered branch by branch"


async def test_a_failed_outline_keeps_the_topics(notebook, fake_db):
    """An ungrouped map still has every topic and citation; failing the whole
    run over the grouping would throw that away."""
    client = await notebook(chunk_count=13, client=MindMapClient(outline_fails=True))

    await studio._run_generation("c1", ArtifactKind.MIND_MAP.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "mindmap")

    assert client.outline_calls >= 1
    assert artifact.status == ArtifactStatus.COMPLETE.value
    assert len(artifact.items) == 4
    assert not any("branch" in item for item in artifact.items)


async def test_flashcards_are_not_regrouped(notebook, fake_db):
    """finalize is a no-op for the kinds that do not need it."""
    await notebook(chunk_count=10)

    await studio._run_generation("c1", ArtifactKind.FLASHCARDS.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "flashcards")
    assert not any("branch" in item for item in artifact.items)

async def test_a_question_offered_as_its_own_option_is_not_stored(notebook, fake_db):
    """From a real quiz: "What technical skills does X have experience with?"
    offering "X has experience with which of the following?" as an option —
    and marking that one correct, while the option that actually listed the
    skills was marked wrong.

    An option that is itself a question cannot answer one. Decidable here,
    unlike the answer merely being wrong, so the model is asked again.
    """

    class Padder(FakeClient):
        first = True

        async def generate_text(self, prompt, **kw):
            answer = await super().generate_text(prompt, **kw)
            payload = json.loads(answer)

            if "questions" in payload and payload["questions"] and Padder.first:
                Padder.first = False
                payload["questions"][0]["options"][
                    0
                ] = "which of the following is true?"
                return json.dumps(payload)

            return answer

    Padder.first = True
    await notebook(chunk_count=3, client=Padder())

    await studio._run_generation("c1", ArtifactKind.QUIZ.value)

    artifact = await fake_db.artifacts().find_artifact("c1", "quiz")

    for item in artifact.items:
        for option in item["options"]:
            assert not option.rstrip().endswith(
                "?"
            ), f"stored a question as an option: {option!r}"
