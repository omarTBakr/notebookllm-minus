"""Chunk summaries written during ingestion, for Studio to generate from.

The properties that matter: every summary lands on its own chunk, nothing is
paid for twice, a model failure never fails anything, and the batches tile the
document so no chunk is left out.
"""

import json

import pytest

from application.tasks.jobs.ingest import summarise
from data.models import DataChunk


class FakeClient:
    """Answers the summarising prompt, echoing each excerpt's own text.

    Echoing the content rather than the number is what makes a misdelivered
    summary visible: "summary of content 7" on chunk 3 is plainly wrong.
    """

    def __init__(self, fail=False, returned=None):
        self.calls = 0
        self.fail = fail
        self.returned = returned

    async def generate_text(self, prompt, max_tokens=None, temperature=None, **_):
        self.calls += 1

        if self.fail:
            raise RuntimeError("the model is down")

        lines = prompt.splitlines()
        seen = [
            (int(line.split()[-1]), lines[i + 1].strip())
            for i, line in enumerate(lines)
            if line.startswith("### Excerpt")
        ]

        return json.dumps(
            {
                "summaries": [
                    {"num": num, "summary": f"summary of {content}"}
                    for num, content in seen[: self.returned or len(seen)]
                ]
            }
        )


class FakeProviders:
    def __init__(self, client):
        self.client = client
        self.models = []

    def chatting(self, model=None, **_):
        self.models.append(model)
        return self.client


@pytest.fixture
def document(fake_db):
    """One notebook with 25 chunks of asset a1, and its chat."""

    async def build(chunk_count=25):
        from data.models import Chat, Project

        fake_db.chats().items["c1"] = Chat(
            chat_id="c1",
            session_id="s1",
            user_id="u1",
            title="A notebook",
            generation_model="the-notebooks-model",
        )
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

        return {c.chunk_order: c for c in fake_db.chunks().items}

    return build


async def _run(fake_db, client, start=0, end=10):
    from shared.utils import get_settings

    providers = FakeProviders(client)
    done = await summarise.summarise_range(
        "c1", "a1", start, end, fake_db, providers, get_settings()
    )
    return done, providers


async def test_each_summary_lands_on_its_own_chunk(fake_db, document):
    chunks = await document()

    done, _ = await _run(fake_db, FakeClient(), 10, 20)

    assert done == 10
    for order in range(10, 20):
        assert chunks[order].summary == f"summary of content {order}"
    assert not chunks[9].summary and not chunks[20].summary, "outside the range"


async def test_the_notebooks_own_model_is_used(fake_db, document):
    """Not the fixed correction model: summaries follow the notebook's model,
    which is what keeps them in the notebook's language and quality."""
    await document()

    _, providers = await _run(fake_db, FakeClient())

    assert providers.models == ["the-notebooks-model"]


async def test_summarised_chunks_cost_nothing(fake_db, document):
    """A redelivered message, or Studio having got there first."""
    await document()
    client = FakeClient()

    await _run(fake_db, client)
    done, _ = await _run(fake_db, client)

    assert done == 0
    assert client.calls == 1


async def test_a_failing_model_fails_nothing(fake_db, document, caplog):
    """Nothing waits on this, and Studio summarises whatever is left."""
    chunks = await document()

    with caplog.at_level("WARNING"):
        done, _ = await _run(fake_db, FakeClient(fail=True))

    assert done == 0
    assert not any(chunks[order].summary for order in range(10))
    assert any("Studio will retry" in record.message for record in caplog.records)


async def test_a_skipped_chunk_stays_unsummarised_not_mislabelled(
    fake_db, document
):
    """The model answered 6 of 10: the other 4 get nothing, not a neighbour's."""
    chunks = await document()

    done, _ = await _run(fake_db, FakeClient(returned=6))

    assert done == 6
    assert [bool(chunks[order].summary) for order in range(10)] == [True] * 6 + [
        False
    ] * 4


# --- publishing ------------------------------------------------------------------


def test_the_batches_tile_the_document(monkeypatch):
    """No gap and no overlap: a gap is a chunk Studio has to summarise later,
    an overlap is a call paid for twice."""
    published = []
    monkeypatch.setattr(summarise.SETTINGS, "INGEST_SUMMARISE", True)
    monkeypatch.setattr(
        summarise.summarise_chunks_task,
        "apply_async",
        lambda args: published.append(tuple(args)),
    )

    assert summarise.publish_summaries("c1", "a1", 25) == 3
    assert published == [
        ("c1", "a1", 0, 10),
        ("c1", "a1", 10, 20),
        ("c1", "a1", 20, 25),
    ]


def test_nothing_is_published_when_disabled(monkeypatch):
    published = []
    monkeypatch.setattr(summarise.SETTINGS, "INGEST_SUMMARISE", False)
    monkeypatch.setattr(
        summarise.summarise_chunks_task,
        "apply_async",
        lambda args: published.append(args),
    )

    assert summarise.publish_summaries("c1", "a1", 25) == 0
    assert published == []


def test_the_task_routes_to_a_queue_a_worker_consumes():
    """A queue with no -Q consumer holds its messages for ever, silently."""
    from celery_app import SETTINGS, celery_app
    from shared.enums import CeleryTaskFunction
    from tests.tasks.test_workflows import _compose_queues

    name = f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.SUMMARISE.value}"
    queue = celery_app.conf.task_routes[name]["queue"]

    assert queue == summarise.summarise_chunks_task.queue
    assert queue.removeprefix(f"{SETTINGS.CELERY_PROJECT_NAME}.") in _compose_queues()


def test_assembling_a_document_publishes_its_summaries_after_indexing(monkeypatch):
    """Indexing first: the document is searchable without summaries, so they
    must never be what it waits behind."""
    from application.tasks.jobs import index
    from application.tasks.jobs.ingest import assemble

    order = []
    result = {
        "project_id": "c1",
        "asset_id": "a1",
        "chunks_saved": 25,
        "index_task_id": None,
        "build_task_id": None,
    }

    monkeypatch.setattr(assemble, "run_job", lambda body, what: result)
    monkeypatch.setattr(
        index.index_project_task,
        "apply_async",
        lambda *a, **k: order.append("index"),
    )
    monkeypatch.setattr(
        summarise,
        "publish_summaries",
        lambda project_id, asset_id, count: order.append(
            ("summaries", project_id, asset_id, count)
        ),
    )

    assemble.assemble_chunks_task("a1")

    assert order == ["index", ("summaries", "c1", "a1", 25)]
