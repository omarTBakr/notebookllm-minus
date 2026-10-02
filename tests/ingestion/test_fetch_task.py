"""What the fetch task does with a link: attach it, and report what went wrong.

The route only queues the task (tests/api/test_link_sources.py). Here the task
body runs in-process against the fakes, then the ingestion it queued is drained,
so the assertions are the ones users depend on: a video's chunks know their
moment, an article is highlighted like a note, a sheet is read like a CSV -- and
a link that cannot be used says why on its row, which is where the UI reads it.
"""

import json

import pytest

from application.services.ingest import FetchedSource, UrlSourceService
from shared.enums import AssetType, TaskExecutionStatus
from shared.exceptions import DuplicateAssetError, LinkSourceError

SEGMENTS = [
    {"start": float(i * 5), "end": float(i * 5 + 5), "text": f"sentence number {i} about the river delta"}
    for i in range(40)
]

VIDEO = FetchedSource(
    name="Rivers, explained",
    content=json.dumps({"video_id": "dQw4w9WgXcQ", "title": "Rivers, explained", "segments": SEGMENTS}).encode(),
    content_type="application/json",
    asset_type=AssetType.YOUTUBE,
    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    content_hash="youtube-dQw4w9WgXcQ",
)

ARTICLE = FetchedSource(
    name="How rivers carve canyons.md",
    content=("# How rivers carve canyons\n\n" + "A river cuts down through rock. " * 60).encode(),
    content_type="text/markdown",
    asset_type=AssetType.MARKDOWN,
    source_url="https://news.test/rivers",
    content_hash="url-news.test/rivers",
)

SHEET = FetchedSource(
    name="Google Sheet abc_123.csv",
    content=b"Name,Department\nAda,Research\nLinus,Engineering\n",
    content_type="text/csv",
    asset_type=AssetType.CSV,
    source_url="https://docs.google.com/spreadsheets/d/abc_123/edit#gid=42",
    content_hash="google-sheets-abc_123-42",
)


@pytest.fixture
def add_link(client, app, monkeypatch):
    """POST a link, then run the fetch task it queued and the ingestion after it."""
    from types import SimpleNamespace

    import application.tasks.workflows as workflows
    import presentation.dependencies as dependencies
    from application.tasks.jobs.ingest.fetch import fetch_url_task
    from tests.support.ingest import drain_ingestion

    async def _add(chat_id, fetched):
        async def fetch(self, url):
            if isinstance(fetched, Exception):
                raise fetched
            return fetched

        monkeypatch.setattr(UrlSourceService, "fetch", fetch)
        monkeypatch.setattr(fetch_url_task, "apply_async", lambda *a, **k: None)
        monkeypatch.setattr(dependencies, "mark_queued", lambda task_id: None)
        monkeypatch.setattr(
            workflows, "ingestion_signature", lambda *a: SimpleNamespace(apply_async=lambda *a, **k: None)
        )

        response = await client.post(f"/chat/chats/{chat_id}/sources/url", json={"url": "https://example.test/x"})
        assert response.status_code == 202, response.text

        await drain_ingestion(app)

        return response

    return _add


def _fetch_rows(fake_db):
    return [t for t in fake_db.tasks().items.values() if t.task_name.endswith("fetch_url_task")]


async def test_a_video_link_is_attached_like_an_upload(add_link, seed, fake_db):
    await add_link("c1", VIDEO)

    asset = next(a for a in fake_db.assets().items.values() if a.name == "Rivers, explained")
    assert asset.asset_type == AssetType.YOUTUBE
    assert asset.source_url == VIDEO.source_url


async def test_every_chunk_of_a_video_knows_its_moment(add_link, seed, fake_db):
    await add_link("c1", VIDEO)

    asset = next(a for a in fake_db.assets().items.values() if a.name == "Rivers, explained")
    chunks = [c for c in fake_db.chunks().items if c.asset_id == asset.asset_id]

    assert chunks
    for chunk in chunks:
        start, end = chunk.chunk_metadata["time_range"]
        assert 0 <= start < end <= 200


async def test_locating_a_video_chunk_returns_its_time_range(add_link, client, seed, fake_db):
    await add_link("c1", VIDEO)

    asset = next(a for a in fake_db.assets().items.values() if a.name == "Rivers, explained")
    chunk = next(c for c in fake_db.chunks().items if c.asset_id == asset.asset_id)

    response = await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/chunks/{chunk.chunk_order}/locate")

    assert response.status_code == 200
    assert response.json()["time_range"] == chunk.chunk_metadata["time_range"]


async def test_a_video_serves_its_transcript_for_the_player(add_link, client, seed, fake_db):
    await add_link("c1", VIDEO)

    asset = next(a for a in fake_db.assets().items.values() if a.name == "Rivers, explained")
    response = await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/content")

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["segments"] == SEGMENTS


async def test_an_article_is_highlighted_like_a_markdown_note(add_link, client, seed, fake_db):
    await add_link("c1", ARTICLE)

    asset = next(a for a in fake_db.assets().items.values() if a.source_url == ARTICLE.source_url)
    chunk = next(c for c in fake_db.chunks().items if c.asset_id == asset.asset_id)

    located = (await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/chunks/{chunk.chunk_order}/locate")).json()

    assert located["text_range"] is not None
    assert located["time_range"] is None


async def test_a_google_sheet_is_ingested_like_a_csv_upload(add_link, client, seed, fake_db):
    await add_link("c1", SHEET)

    asset = next(a for a in fake_db.assets().items.values() if a.source_url == SHEET.source_url)
    assert asset.asset_type == AssetType.CSV
    assert any("Ada" in chunk.chunk_content for chunk in fake_db.chunks().items if chunk.asset_id == asset.asset_id)

    listed = (await client.get("/chat/chats/c1/assets")).json()["assets"]
    assert next(a for a in listed if a["asset_id"] == asset.asset_id)["source_url"] == SHEET.source_url


async def test_the_listing_carries_the_link(add_link, client, seed):
    await add_link("c1", ARTICLE)

    listed = (await client.get("/chat/chats/c1/assets")).json()["assets"]

    assert {a["source_url"] for a in listed} == {"", ARTICLE.source_url}


async def test_the_fetch_names_the_ingestion_it_queued(add_link, client, seed, fake_db):
    """What lets the progress bar carry on from the fetch to the indexing."""
    response = await add_link("c1", ARTICLE)
    task_id = response.json()["task_id"]

    body = (await client.get("/chat/chats/c1/indexing", params={"task_id": task_id})).json()

    assert body["status"] == "SUCCESS"
    assert body["next_task_id"]
    assert body["next_task_id"] in fake_db.tasks().items, "it must name a task the poll can find"


async def test_the_same_link_twice_fails_the_second_fetch_on_its_row(add_link, seed, fake_db):
    await add_link("c1", VIDEO)
    assets_after_first = len(fake_db.assets().items)

    with pytest.raises(DuplicateAssetError):
        await add_link("c1", VIDEO)

    assert len(fake_db.assets().items) == assets_after_first, "a duplicate must not be stored twice"

    failed = [t for t in _fetch_rows(fake_db) if t.status == TaskExecutionStatus.FAILURE]
    assert len(failed) == 1
    assert failed[0].error_type == "DuplicateAssetError"
    assert failed[0].error


async def test_a_link_that_cannot_be_used_says_why_on_its_row(add_link, seed, fake_db):
    before = len(fake_db.assets().items)

    with pytest.raises(LinkSourceError):
        await add_link("c1", LinkSourceError("This video has no transcript available."))

    (row,) = _fetch_rows(fake_db)
    assert row.status == TaskExecutionStatus.FAILURE
    assert "no transcript" in row.error, "this is the text the sources panel shows"
    assert len(fake_db.assets().items) == before, "a refused link left an asset behind"
