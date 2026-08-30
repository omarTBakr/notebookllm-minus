"""get_chunks_by_orders — the lookup a citation uses to find its page.

Exercised against the fake, which stands in for both backends. That the real
ones implement it at all is covered by test_backend_contract.py, which asserts
no concrete repository is left abstract.
"""

from bson.objectid import ObjectId

from models.db_schema import DataChunk


def chunk(asset_id, order, page=None):
    return DataChunk(
        project_id=ObjectId(),
        asset_id=asset_id,
        chunk_order=order,
        chunk_content=f"chunk {order}",
        chunk_metadata={} if page is None else {"page": page},
    )


async def test_returns_a_dict_keyed_by_chunk_order(fake_db):
    """A dict, not a list.

    The caller only ever looks values up, and a dict removes any question of
    whether the backend preserved the requested order.
    """
    repo = fake_db.chunks()
    repo.items.extend([chunk("a1", 0), chunk("a1", 1), chunk("a1", 2)])

    found = await repo.get_chunks_by_orders("a1", [0, 2])

    assert sorted(found) == [0, 2]
    assert found[2].chunk_content == "chunk 2"


async def test_empty_orders_returns_empty(fake_db):
    fake_db.chunks().items.append(chunk("a1", 0))

    assert await fake_db.chunks().get_chunks_by_orders("a1", []) == {}


async def test_unknown_orders_are_simply_absent(fake_db):
    """Asking for a chunk that was deleted is not an error."""
    repo = fake_db.chunks()
    repo.items.append(chunk("a1", 0))

    assert await repo.get_chunks_by_orders("a1", [0, 99]) == {0: repo.items[0]}


async def test_scoped_to_one_asset(fake_db):
    """Two documents in one notebook both number their chunks from 0."""
    repo = fake_db.chunks()
    repo.items.extend([chunk("a1", 0, page=5), chunk("a2", 0, page=9)])

    found = await repo.get_chunks_by_orders("a2", [0])

    assert found[0].chunk_metadata["page"] == 9


async def test_chunk_order_zero_is_found(fake_db):
    repo = fake_db.chunks()
    repo.items.append(chunk("a1", 0, page=0))

    assert 0 in await repo.get_chunks_by_orders("a1", [0])
