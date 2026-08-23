"""Attaching a document: upload, chunk, index."""

import pytest


def a_text_file(name="note1.txt", body=b"the quick brown fox " * 40):
    return {"file": (name, body, "text/plain")}


async def test_uploading_a_text_file_creates_an_asset(client, seed, fake_db):
    response = await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt"))

    assert response.status_code in (200, 201), response.text
    names = {a.name for a in fake_db.assets().items.values()}
    assert "new.txt" in names


async def test_the_bytes_are_stored_on_the_asset(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt", b"hello"))

    asset = next(a for a in fake_db.assets().items.values() if a.name == "new.txt")
    assert asset.file_bytes == b"hello"


async def test_the_document_is_chunked(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt"))

    assert fake_db.chunks().items, "no chunks were persisted"


async def test_every_chunk_records_the_source_name(client, seed, fake_db):
    """The name is what a citation shows, so it must be the asset's, not the
    temporary path the loader saw."""
    await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt"))

    sources = {c.chunk_metadata.get("source") for c in fake_db.chunks().items}
    assert sources == {"new.txt"}


async def test_the_chunks_are_indexed(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt"))

    points = fake_db.vectors().points
    assert any(rows for rows in points.values()), "nothing reached the vector store"


async def test_the_chat_is_marked_as_having_documents(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("new.txt"))

    assert fake_db.chats().items["c1"].has_documents is True


async def test_an_unknown_chat_404s(client, seed):
    response = await client.post("/chat/chats/nope/documents", files=a_text_file())

    assert response.status_code == 404


async def test_a_forbidden_content_type_is_rejected(client, seed):
    response = await client.post(
        "/chat/chats/c1/documents",
        files={"file": ("evil.png", b"\x89PNG", "image/png")},
    )

    assert response.status_code == 400


async def test_an_empty_file_is_rejected(client, seed):
    response = await client.post(
        "/chat/chats/c1/documents", files={"file": ("empty.txt", b"", "text/plain")}
    )

    assert response.status_code == 400
