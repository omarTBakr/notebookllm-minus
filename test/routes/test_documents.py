"""Attaching a document: upload, chunk, index."""

from types import SimpleNamespace

import pytest


def a_text_file(name="note1.txt", body=b"the quick brown fox " * 40):
    return {"file": (name, body, "text/plain")}


async def test_uploading_a_text_file_creates_an_asset(ingest, client, seed, fake_db):
    response = await ingest("c1", a_text_file("new.txt"))

    assert response.status_code in (200, 201, 202), response.text
    names = {a.name for a in fake_db.assets().items.values()}
    assert "new.txt" in names


async def test_the_bytes_are_stored_on_the_asset(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("new.txt", b"hello"))

    asset = next(a for a in fake_db.assets().items.values() if a.name == "new.txt")
    assert asset.file_bytes == b"hello"


async def test_the_document_is_chunked(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("new.txt"))

    assert fake_db.chunks().items, "no chunks were persisted"


async def test_every_chunk_records_the_source_name(ingest, client, seed, fake_db):
    """The name is what a citation shows, so it must be the asset's, not the
    temporary path the loader saw."""
    await ingest("c1", a_text_file("new.txt"))

    sources = {c.chunk_metadata.get("source") for c in fake_db.chunks().items}
    assert sources == {"new.txt"}


async def test_the_chunks_are_indexed(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("new.txt"))

    points = fake_db.vectors().points
    assert any(rows for rows in points.values()), "nothing reached the vector store"


async def test_the_chat_is_marked_as_having_documents(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("new.txt"))

    assert fake_db.chats().items["c1"].has_documents is True


async def test_an_unknown_chat_404s(ingest, client, seed):
    response = await ingest("nope", a_text_file())

    assert response.status_code == 404


async def test_a_forbidden_content_type_is_rejected(ingest, client, seed):
    response = await ingest("c1", {"file": ("evil.png", b"\x89PNG", "image/png")})

    assert response.status_code == 400


async def test_an_empty_file_is_rejected(ingest, client, seed):
    response = await ingest("c1", {"file": ("empty.txt", b"", "text/plain")}
    )

    assert response.status_code == 400


# --- duplicates ---------------------------------------------------------------
#
# A document is identified by its bytes, not its filename: asset_id is a fresh
# uuid on every upload, so nothing else stops the same file being ingested,
# chunked and embedded twice over.


async def test_the_same_document_twice_is_refused(ingest, client, seed):
    files = a_text_file("report.txt", b"the same bytes exactly")

    first = await ingest("c1", files)
    second = await ingest("c1", files)

    assert first.status_code in (200, 201, 202), first.text
    assert second.status_code == 409, second.text
    # The message names the document the user already has, not a bare code.
    assert "report.txt" in second.json()["detail"]


async def test_a_refused_duplicate_changes_nothing(ingest, client, seed, fake_db):
    """The second upload must be inert — no second asset, no extra chunks, no
    extra vectors. A duplicate that half-wrote itself would be worse than one
    that was accepted."""
    files = a_text_file("report.txt", b"the same bytes exactly")
    await ingest("c1", files)

    before = (
        len(fake_db.assets().items),
        len(fake_db.chunks().items),
        sum(len(rows) for rows in fake_db.vectors().points.values()),
    )

    refused = await ingest("c1", files)

    after = (
        len(fake_db.assets().items),
        len(fake_db.chunks().items),
        sum(len(rows) for rows in fake_db.vectors().points.values()),
    )

    assert refused.status_code == 409
    assert before == after, f"the duplicate wrote something: {before} -> {after}"


async def test_the_same_document_in_another_notebook_is_allowed(ingest, client, seed, fake_db):
    """Scoped per notebook: two notebooks may each hold the same file."""
    from models.db_schema import Chat, Project

    fake_db.chats().items["c2"] = Chat(
        chat_id="c2", session_id="s1", user_id="u1", title="Another notebook"
    )
    fake_db.projects().items["c2"] = Project(project_id="c2", name="Another notebook")

    files = a_text_file("shared.txt", b"one document, two notebooks")

    first = await ingest("c1", files)
    second = await ingest("c2", files)

    assert first.status_code in (200, 201, 202), first.text
    assert second.status_code in (200, 201, 202), second.text

    projects = {a.project_id for a in fake_db.assets().items.values() if a.name == "shared.txt"}
    assert projects == {"c1", "c2"}


async def test_a_different_document_with_the_same_name_is_allowed(ingest, client, seed):
    """The name is not the identity — re-saving a file under a used name is a
    normal thing to do."""
    await ingest("c1", a_text_file("notes.txt", b"first version"))
    response = await ingest("c1", a_text_file("notes.txt", b"a later, different version")
    )

    assert response.status_code in (200, 201, 202), response.text


# --- deleting a source --------------------------------------------------------


async def test_deleting_a_source_removes_its_asset(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("gone.txt"))
    asset_id = next(a.asset_id for a in fake_db.assets().items.values() if a.name == "gone.txt")

    response = await client.delete(f"/chat/chats/c1/assets/{asset_id}")

    assert response.status_code == 200, response.text
    assert asset_id not in fake_db.assets().items


async def test_deleting_a_source_removes_its_chunks_and_vectors(ingest, client, seed, fake_db):
    """The whole point: a source that is gone from the list while its chunks
    still answer questions is worse than one that was never deleted."""
    await ingest("c1", a_text_file("gone.txt"))
    asset_id = next(a.asset_id for a in fake_db.assets().items.values() if a.name == "gone.txt")

    assert any(c.asset_id == asset_id for c in fake_db.chunks().items)

    response = await client.delete(f"/chat/chats/c1/assets/{asset_id}")

    assert response.status_code == 200, response.text
    assert not [c for c in fake_db.chunks().items if c.asset_id == asset_id]

    surviving = [
        row
        for rows in fake_db.vectors().points.values()
        for row in rows
        if (row["metadata"] or {}).get("asset_id") == asset_id
    ]
    assert not surviving, "vectors outlived the source they came from"


async def test_deleting_a_source_leaves_the_other_sources_alone(ingest, client, seed, fake_db):
    await ingest("c1", a_text_file("keep.txt", b"keep me around"))
    await ingest("c1", a_text_file("drop.txt", b"drop me instead"))

    drop_id = next(a.asset_id for a in fake_db.assets().items.values() if a.name == "drop.txt")
    keep_id = next(a.asset_id for a in fake_db.assets().items.values() if a.name == "keep.txt")

    await client.delete(f"/chat/chats/c1/assets/{drop_id}")

    assert keep_id in fake_db.assets().items
    assert [c for c in fake_db.chunks().items if c.asset_id == keep_id]


async def test_deleting_the_last_source_ungrounds_the_chat(ingest, client, seed, fake_db):
    """has_documents is what the composer reads to decide whether an answer can
    cite anything, so emptying a notebook has to clear it."""
    await ingest("c1", a_text_file("only.txt"))
    assert fake_db.chats().items["c1"].has_documents is True

    # The seed leaves an asset in this notebook too; the flag turns off on the
    # last one out, not the first.
    for asset_id in [a.asset_id for a in list(fake_db.assets().items.values())]:
        response = await client.delete(f"/chat/chats/c1/assets/{asset_id}")
        assert response.status_code == 200, response.text

    assert fake_db.chats().items["c1"].has_documents is False


async def test_deleting_frees_the_name_for_re_upload(ingest, client, seed, fake_db):
    """Deleting really removes the row, so the same bytes are no longer a
    duplicate and can be uploaded again."""
    files = a_text_file("again.txt", b"upload, delete, upload")
    await ingest("c1", files)
    asset_id = next(a.asset_id for a in fake_db.assets().items.values() if a.name == "again.txt")

    await client.delete(f"/chat/chats/c1/assets/{asset_id}")
    response = await ingest("c1", files)

    assert response.status_code in (200, 201, 202), response.text


async def test_deleting_an_unknown_asset_404s(client, seed):
    response = await client.delete("/chat/chats/c1/assets/no-such-asset")

    assert response.status_code == 404


# --- markdown --------------------------------------------------------------


async def test_uploading_a_markdown_file_creates_an_asset(ingest, client, seed, fake_db):
    body = b"# Guide\n\n" + b"word " * 60
    response = await ingest("c1", {"file": ("guide.md", body, "text/markdown")})

    assert response.status_code == 202
    # chunks_created is no longer in the response — the chunking has not
    # happened yet when it returns. The chunks themselves are the assertion.
    assert fake_db.chunks().items, "no chunks were persisted"


async def test_a_markdown_document_is_chunked_on_its_headings(ingest, client, seed, fake_db):
    # Comfortably past CHAT_CHUNK_SIZE (1000) so it actually has to split
    # rather than fitting both sections in one chunk.
    body = ("# One\n\n" + "word " * 300 + "\n\n# Two\n\n" + "word " * 300).encode()

    await ingest("c1", {"file": ("two-sections.md", body, "text/markdown")},
    )

    contents = [c.chunk_content for c in fake_db.chunks().items]
    assert any(text.startswith("# One") for text in contents)
    assert any(text.startswith("# Two") for text in contents)


# --- when the broker is down --------------------------------------------------


async def test_a_broker_outage_is_a_503_not_a_500(client, seed, monkeypatch):
    """The other two enqueue routes have always mapped a broker outage to 503.
    This one did not, so RabbitMQ being down surfaced as an opaque
    "Internal server error" on every upload."""
    from kombu.exceptions import OperationalError as BrokerOperationalError

    import routes.chat.assets as assets_route

    def refuse(*args, **kwargs):
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(
        assets_route, "ingestion_chain", lambda *a, **k: SimpleNamespace(apply_async=refuse)
    )

    response = await client.post(
        "/chat/chats/c1/documents", files=a_text_file("unlucky.txt")
    )

    assert response.status_code == 503
    assert "unlucky.txt" in response.json()["detail"]


async def test_a_failed_queue_does_not_leave_the_document_unuploadable(
    client, seed, fake_db, monkeypatch
):
    """The asset row is committed before the chain is queued. Left behind, it
    would never be ingested *and* the content-hash dedupe would answer 409 to
    every retry — so the document could not be added at all without deleting
    it by hand first."""
    from kombu.exceptions import OperationalError as BrokerOperationalError

    import routes.chat.assets as assets_route

    def refuse(*args, **kwargs):
        raise BrokerOperationalError("broker unavailable")

    monkeypatch.setattr(
        assets_route, "ingestion_chain", lambda *a, **k: SimpleNamespace(apply_async=refuse)
    )

    files = a_text_file("retry-me.txt", b"queued while the broker was down")
    await client.post("/chat/chats/c1/documents", files=files)

    # Rolled back, so the retry below is a first upload rather than a duplicate.
    assert not [a for a in fake_db.assets().items.values() if a.name == "retry-me.txt"]

    monkeypatch.undo()
    retry = await client.post("/chat/chats/c1/documents", files=files)

    assert retry.status_code != 409, "the failed upload blocked its own retry"
