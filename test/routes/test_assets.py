"""Sources: listing, selection, rename and content."""

import pytest


async def test_list_assets(client, seed):
    response = await client.get("/chat/chats/c1/assets")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["assets"][0]["name"] == "note1.txt"
    assert body["assets"][0]["selected"] is True


async def test_excluded_assets_are_reported_unselected(client, seed, fake_db):
    fake_db.chats().items["c1"].excluded_assets = ["a1"]

    body = (await client.get("/chat/chats/c1/assets")).json()

    assert body["assets"][0]["selected"] is False
    assert body["selected_count"] == 0


async def test_select_sources_persists_the_exclusions(client, seed, fake_db):
    response = await client.patch("/chat/chats/c1/sources",
                                  json={"excluded_assets": ["a1"]})

    assert response.status_code == 200
    assert fake_db.chats().items["c1"].excluded_assets == ["a1"]


async def test_rename_asset(client, seed, fake_db):
    response = await client.patch("/chat/chats/c1/assets/a1", json={"name": "Groceries"})

    assert response.status_code == 200
    assert response.json()["name"] == "Groceries"
    assert fake_db.assets().items["a1"].name == "Groceries"


async def test_rename_asset_404s_when_missing(client, seed):
    response = await client.patch("/chat/chats/c1/assets/nope", json={"name": "x"})

    assert response.status_code == 404


@pytest.mark.parametrize("name", ["", "   "])
async def test_rename_asset_rejects_blank(client, seed, name):
    response = await client.patch("/chat/chats/c1/assets/a1", json={"name": name})

    assert response.status_code == 422


async def test_asset_content_is_served_as_text(client, seed):
    response = await client.get("/chat/chats/c1/assets/a1/content")

    assert response.status_code == 200
    assert response.text == "the note body"
    assert response.headers["content-type"].startswith("text/plain")


async def test_asset_content_is_served_as_pdf_for_a_pdf(client, seed, fake_db):
    from enums import AssetType

    fake_db.assets().items["a1"].asset_type = AssetType.PDF

    response = await client.get("/chat/chats/c1/assets/a1/content")

    assert response.headers["content-type"] == "application/pdf"


async def test_asset_content_404s_when_missing(client, seed):
    assert (await client.get("/chat/chats/c1/assets/nope/content")).status_code == 404


async def test_user_assets_across_chats(client, seed):
    """No UI caller — covered here because nothing else would notice it break."""
    response = await client.get("/chat/users/u1/assets")

    assert response.status_code == 200
    assert [a["name"] for a in response.json()["assets"]] == ["note1.txt"]


# --- the content route's access check and caching -----------------------------


async def test_content_404s_for_an_asset_in_another_notebook(client, seed, fake_db):
    """chat_id used to be validated and then thrown away.

    Naming any valid chat alongside any asset id returned that asset's bytes,
    so a notebook's documents were readable from a notebook that did not own
    them. Reported as missing rather than forbidden, so the reply does not
    confirm the asset exists somewhere else.
    """
    from enums import AssetType
    from models.db_schema import Asset, Chat, Project

    fake_db.chats().items["c2"] = Chat(chat_id="c2", session_id="s1", user_id="u1",
                                       title="Someone else's notebook")
    fake_db.projects().items["c2"] = Project(project_id="c2", name="Someone else's")
    fake_db.assets().items["secret"] = Asset(
        asset_id="secret", asset_type=AssetType.TEXT, project_id="c2",
        name="private.txt", file_bytes=b"confidential",
    )

    response = await client.get("/chat/chats/c1/assets/secret/content")

    assert response.status_code == 404
    assert b"confidential" not in response.content


async def test_content_carries_an_etag_and_revalidates(client, seed, fake_db):
    """A 25 MB PDF is re-requested every time a citation is opened."""
    fake_db.assets().items["a1"].content_hash = "abc123"

    first = await client.get("/chat/chats/c1/assets/a1/content")
    assert first.headers["etag"] == '"abc123"'

    second = await client.get(
        "/chat/chats/c1/assets/a1/content",
        headers={"If-None-Match": '"abc123"'},
    )
    assert second.status_code == 304
    assert second.content == b""


async def test_a_stale_etag_still_gets_the_bytes(client, seed, fake_db):
    fake_db.assets().items["a1"].content_hash = "abc123"

    response = await client.get(
        "/chat/chats/c1/assets/a1/content",
        headers={"If-None-Match": '"an-older-version"'},
    )

    assert response.status_code == 200
    assert response.content == b"the note body"


# --- downloading a source -----------------------------------------------------


async def test_default_content_is_served_inline_with_the_asset_id(client, seed):
    """The unchanged behaviour: previews and citations must not regress."""
    response = await client.get("/chat/chats/c1/assets/a1/content")

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline")
    assert 'filename="a1"' in disposition
    assert "note1.txt" not in disposition


async def test_download_flag_serves_as_an_attachment_with_the_real_name(client, seed):
    response = await client.get("/chat/chats/c1/assets/a1/content?download=1")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "filename*=UTF-8''note1.txt" in disposition
    assert response.text == "the note body"


async def test_download_encodes_a_non_ascii_filename(client, seed, fake_db):
    """This corpus has Arabic filenames — a bare filename="..." cannot carry
    them correctly in every browser, which is exactly why this is RFC 5987
    (filename*=) rather than the plain form."""
    fake_db.assets().items["a1"].name = "دليل.txt"

    response = await client.get("/chat/chats/c1/assets/a1/content?download=1")

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "filename*=UTF-8''%D8%AF%D9%84%D9%8A%D9%84.txt" in disposition


async def test_download_still_enforces_the_notebook_ownership_check(client, seed, fake_db):
    from enums import AssetType
    from models.db_schema import Asset, Chat, Project

    fake_db.chats().items["c2"] = Chat(chat_id="c2", session_id="s1", user_id="u1",
                                       title="Someone else's notebook")
    fake_db.projects().items["c2"] = Project(project_id="c2", name="Someone else's")
    fake_db.assets().items["secret"] = Asset(
        asset_id="secret", asset_type=AssetType.TEXT, project_id="c2",
        name="private.txt", file_bytes=b"confidential",
    )

    response = await client.get("/chat/chats/c1/assets/secret/content?download=1")

    assert response.status_code == 404
    assert b"confidential" not in response.content


# --- locating a chunk: page, and a highlight if one exists --------------------


def _chunk(fake_db, asset_id, order, metadata):
    from bson.objectid import ObjectId
    from models.db_schema import DataChunk

    fake_db.chunks().items.append(DataChunk(
        project_id=ObjectId(), asset_id=asset_id, chunk_order=order,
        chunk_content="the cited passage", chunk_metadata=metadata,
    ))


async def test_locate_returns_the_page_and_highlight(client, seed, fake_db):
    _chunk(fake_db, "a1", 3, {
        "page": 10, "page_label": "11",
        "highlight": {"v": 1, "w": 600.0, "h": 800.0, "o": "tl", "r": [[10.0, 20.0, 50.0, 35.0]]},
    })

    response = await client.get("/chat/chats/c1/assets/a1/chunks/3/locate")

    assert response.status_code == 200
    body = response.json()
    assert body["page_number"] == 11
    assert body["page_label"] == "11"
    assert body["highlight"]["r"] == [[10.0, 20.0, 50.0, 35.0]]
    assert body["text"] == "the cited passage"


async def test_locate_returns_null_highlight_for_a_legacy_chunk(client, seed, fake_db):
    """Ingested before PDF_LOADER=pymupdf captured word boxes — page still
    resolves, and the raw text is still there as a fallback, but there is
    nothing to draw."""
    _chunk(fake_db, "a1", 0, {"page": 0, "page_label": "1"})

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    body = response.json()
    assert body["page_number"] == 1
    assert body["highlight"] is None
    assert body["text"] == "the cited passage"


async def test_locate_404s_for_an_unknown_chunk(client, seed, fake_db):
    response = await client.get("/chat/chats/c1/assets/a1/chunks/99/locate")

    assert response.status_code == 404


async def test_locate_enforces_the_notebook_ownership_check(client, seed, fake_db):
    from enums import AssetType
    from models.db_schema import Asset, Chat, Project

    fake_db.chats().items["c2"] = Chat(chat_id="c2", session_id="s1", user_id="u1",
                                       title="Someone else's notebook")
    fake_db.projects().items["c2"] = Project(project_id="c2", name="Someone else's")
    fake_db.assets().items["secret"] = Asset(
        asset_id="secret", asset_type=AssetType.TEXT, project_id="c2", name="private.txt",
    )
    _chunk(fake_db, "secret", 0, {"page": 0})

    response = await client.get("/chat/chats/c1/assets/secret/chunks/0/locate")

    assert response.status_code == 404


async def test_locate_on_a_text_asset_has_no_page(client, seed, fake_db):
    """A .txt note never had a page to begin with."""
    _chunk(fake_db, "a1", 0, {"source": "note1.txt"})

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    body = response.json()
    assert body["page_number"] is None
    assert body["highlight"] is None


# --- sanitised inline text, and text_range for text/markdown citations -------


async def test_inline_text_is_sanitised_but_download_is_raw(client, seed, fake_db):
    """Presentation forms and a NUL, the same corpus this project is built
    around — the inline preview shows what was actually chunked/embedded;
    download must still return the exact original bytes."""
    raw = "﻿\x00ﻻ hello"  # BOM-ish bidi mark + NUL + a presentation form
    fake_db.assets().items["a1"].file_bytes = raw.encode("utf-8")

    inline = await client.get("/chat/chats/c1/assets/a1/content")
    downloaded = await client.get("/chat/chats/c1/assets/a1/content?download=1")

    assert "\x00" not in inline.text
    assert "ﻻ" not in inline.text  # folded to its NFKC form
    assert "لا" in inline.text
    assert downloaded.content == raw.encode("utf-8")  # untouched


async def test_locate_returns_a_text_range_for_a_text_asset(client, seed, fake_db):
    _chunk(fake_db, "a1", 0, {"start_index": 10})
    fake_db.assets().items["a1"].asset_type = __import__("enums").AssetType.TEXT

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    body = response.json()
    # "the cited passage" from _chunk's fixed chunk_content, len 17.
    assert body["text_range"] == [10, 27]


async def test_locate_text_range_is_absent_without_a_start_index(client, seed, fake_db):
    _chunk(fake_db, "a1", 0, {})

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    assert response.json()["text_range"] is None


async def test_locate_text_range_ignores_an_unresolved_rebase(client, seed, fake_db):
    """enforce_size marks a chunk it could not rebase with -1, not a real
    offset — must not be reported as though it were one."""
    _chunk(fake_db, "a1", 0, {"start_index": -1})

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    assert response.json()["text_range"] is None


async def test_locate_has_no_text_range_for_a_pdf_asset(client, seed, fake_db):
    """start_index exists on PDF chunks too (for computing `highlight`), but
    it indexes into the *page* text, not the whole asset — a text_range there
    would be meaningless and must not be reported."""
    from enums import AssetType

    fake_db.assets().items["a1"].asset_type = AssetType.PDF
    _chunk(fake_db, "a1", 0, {"page": 0, "start_index": 5})

    response = await client.get("/chat/chats/c1/assets/a1/chunks/0/locate")

    assert response.json()["text_range"] is None
