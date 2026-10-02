"""Adding a source from a link: what the route does, and only that.

The route no longer fetches. It checks the notebook, queues a task that will, and
answers 202 with that task's id. What the task then does -- fetch, attach, report
an unusable link on its row -- is covered in tests/ingestion/test_fetch_task.py;
the fetch itself in tests/domain/test_url_sources.py.
"""

import pytest
from kombu.exceptions import OperationalError as BrokerOperationalError

from application.tasks.jobs.ingest.fetch import fetch_url_task
from shared.enums import TaskExecutionStatus

URL = "https://news.test/rivers"


@pytest.fixture
def broker(monkeypatch):
    """Stand in for the broker, recording what would have been published."""
    import presentation.dependencies as dependencies

    published: list[dict] = []

    def apply_async(self_or_args=None, args=None, task_id=None, **kwargs):
        published.append({"args": args, "task_id": task_id})

    monkeypatch.setattr(fetch_url_task, "apply_async", apply_async)
    monkeypatch.setattr(dependencies, "mark_queued", lambda task_id: None)

    return published


async def _post(client, chat_id="c1", url=URL):
    return await client.post(f"/chat/chats/{chat_id}/sources/url", json={"url": url})


async def test_a_link_is_accepted_and_the_fetch_is_queued(client, seed, broker, fake_db):
    response = await _post(client)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued" and body["filename"] == URL

    assert broker == [{"args": ["c1", URL], "task_id": body["task_id"]}]


async def test_the_queued_task_has_a_row_the_progress_poll_can_read(client, seed, broker, fake_db):
    task_id = (await _post(client)).json()["task_id"]

    row = fake_db.tasks().items[task_id]

    assert row.status == TaskExecutionStatus.QUEUED
    assert row.project_id == "c1"
    assert row.args == {"project_id": "c1", "url": URL}
    assert row.task_name.endswith("fetch_url_task")


async def test_nothing_is_fetched_or_stored_by_the_request_itself(client, seed, broker, fake_db):
    before = len(fake_db.assets().items)

    await _post(client)

    assert len(fake_db.assets().items) == before


async def test_the_same_link_pasted_again_joins_the_run_in_flight(client, seed, broker):
    first = (await _post(client)).json()["task_id"]
    second = (await _post(client)).json()["task_id"]

    assert second == first
    assert len(broker) == 1, "the second paste must not reach the broker"


async def test_a_different_link_is_its_own_task(client, seed, broker):
    first = (await _post(client)).json()["task_id"]
    second = (await _post(client, url="https://news.test/lakes")).json()["task_id"]

    assert second != first and len(broker) == 2


async def test_an_unknown_notebook_is_a_404_before_anything_is_queued(client, seed, broker, fake_db):
    response = await _post(client, chat_id="nope")

    assert response.status_code == 404
    assert broker == []
    assert not [t for t in fake_db.tasks().items.values() if t.task_name.endswith("fetch_url_task")]


async def test_an_unreachable_broker_is_a_503_and_leaves_no_row(client, seed, monkeypatch, fake_db):
    import presentation.dependencies as dependencies

    def unreachable(*args, **kwargs):
        # What kombu raises when the broker is down; a RuntimeError is an
        # ordinary bug and must not be blamed on the broker.
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(fetch_url_task, "apply_async", unreachable)
    monkeypatch.setattr(dependencies, "mark_queued", lambda task_id: None)

    response = await _post(client)

    assert response.status_code == 503
    assert not [t for t in fake_db.tasks().items.values() if t.task_name.endswith("fetch_url_task")]
