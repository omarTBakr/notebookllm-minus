"""Adding a source from a link, through the route and the ingestion it queues.

The fetch itself is covered in test/unit/test_url_sources.py; here it is
stubbed, and what is tested is everything after it: that a link ingests like
an upload, and that a video's chunks come back with the moment they quote.
"""

import json

import pytest

from application.services.ingest import FetchedSource, UrlSourceService
from shared.enums import AssetType
from shared.exceptions import LinkSourceError

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


@pytest.fixture
def add_link(client, app, monkeypatch):
    """POST a link with the fetch stubbed, and run the ingestion it queues."""
    from types import SimpleNamespace

    import application.tasks.tracking.status as task_status
    import application.tasks.workflows as workflows
    from test.fakes.ingest import drain_ingestion

    async def _add(chat_id, fetched):
        async def fetch(self, url):
            if isinstance(fetched, Exception):
                raise fetched
            return fetched

        monkeypatch.setattr(UrlSourceService, "fetch", fetch)
        monkeypatch.setattr(
            workflows, "ingestion_signature", lambda *a: SimpleNamespace(apply_async=lambda *a, **k: None)
        )
        monkeypatch.setattr(task_status, "mark_queued", lambda task_id: None)

        response = await client.post(f"/chat/chats/{chat_id}/sources/url", json={"url": "https://example.test/x"})

        if response.status_code < 400:
            await drain_ingestion(app)

        return response

    return _add


async def test_a_video_link_is_accepted_like_an_upload(add_link, seed, fake_db):
    response = await add_link("c1", VIDEO)

    assert response.status_code == 202, response.text
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


async def test_the_listing_carries_the_link(add_link, client, seed):
    await add_link("c1", ARTICLE)

    listed = (await client.get("/chat/chats/c1/assets")).json()["assets"]

    assert {a["source_url"] for a in listed} == {"", ARTICLE.source_url}


async def test_the_same_link_twice_is_refused(add_link, seed):
    assert (await add_link("c1", VIDEO)).status_code == 202

    response = await add_link("c1", VIDEO)

    assert response.status_code == 409


async def test_a_link_that_cannot_be_used_says_why(add_link, seed, fake_db):
    before = len(fake_db.assets().items)

    response = await add_link("c1", LinkSourceError("This video has no transcript available."))

    assert response.status_code == 400
    assert "no transcript" in response.text
    assert len(fake_db.assets().items) == before, "a refused link left an asset behind"
