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
