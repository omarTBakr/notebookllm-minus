"""The Studio endpoints: start a generation, read what exists so far.

The behaviour worth pinning is the partial read. A 694-chunk book takes minutes,
so `GET` returning only finished sets would leave the panel blank for the whole
run — which is the design this replaced, and the complaint that prompted it.
"""

import pytest

from enums import ArtifactKind, ArtifactStatus
from models.db_schema import Artifact, DataChunk


@pytest.fixture
def queued(monkeypatch):
    """Stand in for the broker, recording what would have been published."""
    import routes.chat.studio as studio_route

    published = []

    class FakeResult:
        id = "task-generated-1"

    class FakeTask:
        name = "notebookllm.generate_artifact_task"

        @staticmethod
        def apply_async(args=None, **_):
            published.append(args)
            return FakeResult()

    monkeypatch.setattr(studio_route, "generate_artifact_task", FakeTask)
    monkeypatch.setattr(studio_route, "mark_queued", lambda task_id: None)

    return published


@pytest.fixture
async def indexed(fake_db, seed):
    """`seed` gives the notebook a project but no chunks.

    The route refuses to start on a notebook with nothing in it, so anything
    testing a successful start has to put chunks there. Kept local rather than
    added to `seed`, which half the suite shares.
    """
    project = await fake_db.projects().get_project("c1")

    await fake_db.chunks().create_chunks(
        [
            DataChunk(
                project_id=project.id,
                asset_id="a1",
                chunk_order=i,
                chunk_content=f"content {i}",
            )
            for i in range(4)
        ]
    )

    return seed


# --- starting a generation -----------------------------------------------------


async def test_a_notebook_with_no_documents_is_refused_immediately(
    client, seed, queued
):
    """The bug that sent a panel spinning for four minutes.

    Clicking a Studio tile while the upload is still indexing used to queue a
    task that died on its first line with ProjectNotFoundError. Nothing had
    created the artifact row by then, so `GET` answered `exists: false`, the
    browser could not tell that apart from "not written yet", and the panel sat
    on "reading the documents…" long after the task was dead.

    Answered here instead, while the caller is still listening, and nothing is
    queued.
    """
    response = await client.post("/chat/chats/c1/studio/flashcards")

    assert response.status_code == 400
    assert "document" in response.json()["detail"].lower()
    assert queued == [], "a task was queued for a notebook with nothing to read"


async def test_the_artifact_exists_as_soon_as_the_post_returns(client, indexed, queued):
    """So a poll that lands immediately after the 202 finds a row.

    The task used to create this, which left a window where `GET` said
    `exists: false` for a run that was perfectly healthy — indistinguishable,
    from the browser, from a run that had already died.
    """
    response = await client.post("/chat/chats/c1/studio/flashcards")

    assert response.status_code == 202

    read = await client.get("/chat/chats/c1/studio/flashcards")

    assert read.json()["exists"] is True
    assert read.json()["status"] == ArtifactStatus.GENERATING.value
    assert read.json()["items"] == []


async def test_a_generation_is_queued_and_reports_its_task(client, indexed, queued):
    response = await client.post("/chat/chats/c1/studio/flashcards")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"] == "task-generated-1"
    assert body["kind"] == "flashcards"
    assert queued == [["c1", "flashcards"]]


async def test_a_second_click_joins_the_run_already_going(client, indexed, queued):
    """202 means started, 200 means already going. Without the distinction a
    double-click queues a second identical job, and the two would race to
    upsert the same row."""
    first = await client.post("/chat/chats/c1/studio/quiz")
    second = await client.post("/chat/chats/c1/studio/quiz")

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]
    assert len(queued) == 1, "the second click published a duplicate job"


async def test_flashcards_and_quiz_are_separate_runs(client, indexed, queued):
    """Same notebook, different kind: the idempotency fingerprint covers the
    args, so asking for a quiz must not be mistaken for the deck in flight."""
    await client.post("/chat/chats/c1/studio/flashcards")
    response = await client.post("/chat/chats/c1/studio/quiz")

    assert response.status_code == 202
    assert len(queued) == 2


async def test_an_unbuilt_studio_tile_cannot_be_started_by_url(client, seed, queued):
    """The Studio panel shows nine tiles and two have generators. ArtifactKind
    holds only what can actually run, so guessing a name in a URL is a 400
    naming the alternatives rather than a queued task nothing will consume."""
    response = await client.post("/chat/chats/c1/studio/mindmap")

    assert response.status_code == 400
    assert "flashcards" in response.json()["detail"]
    assert not queued


async def test_a_missing_notebook_is_refused_before_queueing(client, seed, queued):
    response = await client.post("/chat/chats/nope/studio/flashcards")

    assert response.status_code == 404
    assert not queued, "a job was published for a notebook that does not exist"


# --- reading a set -------------------------------------------------------------


async def test_a_notebook_with_no_set_is_not_an_error(client, seed):
    """The browser asks on every panel open. A 404 there is an ordinary state
    dressed as a failure."""
    response = await client.get("/chat/chats/c1/studio/flashcards")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["items"] == []


async def test_a_set_is_readable_while_it_is_still_generating(client, seed, fake_db):
    """The point of the whole design: items appear as they are produced, so the
    panel fills while the rest of the book is still being summarised."""
    await fake_db.artifacts().create_artifact(
        Artifact(
            artifact_id="art-1",
            chat_id="c1",
            kind=ArtifactKind.FLASHCARDS,
            status=ArtifactStatus.GENERATING,
            items=[{"front": "Q", "back": "A", "chunk_order": 0}],
        )
    )

    body = (await client.get("/chat/chats/c1/studio/flashcards")).json()

    assert body["exists"] is True
    assert body["status"] == ArtifactStatus.GENERATING.value
    assert body["count"] == 1, "a partial set was hidden until it finished"


async def test_a_failed_set_still_returns_what_it_produced(client, seed, fake_db):
    """A deck of one card that stopped early beats an error page, and the
    status is what lets the UI say which it is."""
    await fake_db.artifacts().create_artifact(
        Artifact(
            artifact_id="art-2",
            chat_id="c1",
            kind=ArtifactKind.QUIZ,
            status=ArtifactStatus.FAILED,
            items=[
                {
                    "question": "Q",
                    "options": list("abcd"),
                    "answer_index": 0,
                    "chunk_order": 1,
                }
            ],
            error="provider fell over",
        )
    )

    body = (await client.get("/chat/chats/c1/studio/quiz")).json()

    assert body["status"] == ArtifactStatus.FAILED.value
    assert body["count"] == 1
    assert "provider fell over" in body["error"]


async def test_reading_one_kind_does_not_return_the_other(client, seed, fake_db):
    await fake_db.artifacts().create_artifact(
        Artifact(
            artifact_id="a-cards",
            chat_id="c1",
            kind=ArtifactKind.FLASHCARDS,
            items=[{"front": "Q", "back": "A", "chunk_order": 0}],
        )
    )

    quiz = (await client.get("/chat/chats/c1/studio/quiz")).json()

    assert quiz["exists"] is False


async def test_an_unknown_kind_is_refused_on_read_too(client, seed):
    response = await client.get("/chat/chats/c1/studio/infographic")

    assert response.status_code == 400
