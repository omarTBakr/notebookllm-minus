"""Resolving a search hit back to a page of the original document.

The vector payload carries only (asset_id, chunk_order); the page lives on the
chunk row. resolve_pages bridges the two, and is the single place a 0-based
page becomes a 1-based page number.
"""

from bson.objectid import ObjectId

from models.db_schema import DataChunk
from routes.chat._pages import keys_from_hits, resolve_pages


def chunk(asset_id="a1", order=0, metadata=None):
    return DataChunk(
        project_id=ObjectId(),
        asset_id=asset_id,
        chunk_order=order,
        chunk_content="text",
        chunk_metadata=metadata if metadata is not None else {},
    )


def hit(asset_id="a1", order=0):
    return {"score": 0.5, "metadata": {"asset_id": asset_id, "chunk_order": order}}


# --- keys_from_hits -----------------------------------------------------------


def test_keys_skip_hits_with_no_asset():
    assert keys_from_hits([{"metadata": {"chunk_order": 2}}]) == []


def test_chunk_order_zero_is_kept():
    """0 is falsy and is also the first chunk of every document."""
    assert keys_from_hits([hit(order=0)]) == [("a1", 0)]


# --- resolve_pages ------------------------------------------------------------


async def test_resolves_a_zero_based_page_to_a_one_based_number(fake_db):
    fake_db.chunks().items.append(chunk(order=3, metadata={"page": 10, "page_label": "11"}))

    pages = await resolve_pages(fake_db, [("a1", 3)])

    assert pages == {("a1", 3): {"page_number": 11, "page_label": "11"}}


async def test_page_label_falls_back_to_the_page_number(fake_db):
    """Only pypdf emits page_label.

    pymupdf and pdfplumber set `page` alone, so reading the label
    unconditionally would show nothing for two of the three loaders.
    """
    fake_db.chunks().items.append(chunk(order=1, metadata={"page": 4}))

    pages = await resolve_pages(fake_db, [("a1", 1)])

    assert pages[("a1", 1)] == {"page_number": 5, "page_label": "5"}


async def test_a_chunk_with_no_page_is_absent_rather_than_none(fake_db):
    """A .txt note. The citation simply does not become a link."""
    fake_db.chunks().items.append(chunk(order=0, metadata={"source": "note1.txt"}))

    assert await resolve_pages(fake_db, [("a1", 0)]) == {}


async def test_a_missing_chunk_does_not_raise(fake_db):
    """Cited, then the document was deleted and re-uploaded."""
    assert await resolve_pages(fake_db, [("gone", 7)]) == {}


async def test_empty_keys_issue_no_query(fake_db):
    class Exploding:
        def chunks(self):
            raise AssertionError("resolve_pages queried on an empty key list")

    assert await resolve_pages(Exploding(), []) == {}


async def test_one_query_per_asset(fake_db):
    """Citations from the same source share a lookup.

    An answer usually cites one document several times, so grouping is what
    keeps this to a single round trip on the path that delays the first frame.
    """
    calls = []
    repo = fake_db.chunks()
    original = repo.get_chunks_by_orders

    async def counted(asset_id, chunk_orders):
        calls.append(asset_id)
        return await original(asset_id, chunk_orders)

    repo.get_chunks_by_orders = counted

    for order in (0, 1, 2):
        repo.items.append(chunk(asset_id="a1", order=order, metadata={"page": order}))
    repo.items.append(chunk(asset_id="a2", order=0, metadata={"page": 9}))

    pages = await resolve_pages(fake_db, [("a1", 0), ("a1", 1), ("a1", 2), ("a2", 0)])

    assert sorted(calls) == ["a1", "a2"]
    assert len(pages) == 4


async def test_a_broken_page_value_is_skipped(fake_db):
    """Metadata comes from a PDF's own dictionary; it is not to be trusted."""
    fake_db.chunks().items.append(chunk(order=0, metadata={"page": "not a number"}))

    assert await resolve_pages(fake_db, [("a1", 0)]) == {}


async def test_a_database_failure_costs_the_links_not_the_answer(fake_db):
    """An unlinked citation is still a usable citation."""
    async def boom(asset_id, chunk_orders):
        raise RuntimeError("database is down")

    fake_db.chunks().get_chunks_by_orders = boom

    assert await resolve_pages(fake_db, [("a1", 0)]) == {}
