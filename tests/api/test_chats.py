"""Notebooks: create, list, fetch, rename."""

import pytest


async def test_get_chat_returns_its_settings(client, seed):
    response = await client.get("/chat/chats/c1")

    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == "c1"
    assert body["title"] == "A notebook"


async def test_get_chat_404s_when_missing(client, seed):
    assert (await client.get("/chat/chats/nope")).status_code == 404


async def test_list_user_chats(client, seed):
    response = await client.get("/chat/users/u1/chats")

    assert response.status_code == 200
    assert [c["chat_id"] for c in response.json()["chats"]] == ["c1"]


async def test_list_user_chats_404s_for_an_unknown_user(client, seed):
    """The user is validated before the listing, so an unknown id is a 404
    rather than an empty list that looks like "you have no notebooks"."""
    assert (await client.get("/chat/users/nobody/chats")).status_code == 404


async def test_create_chat_for_a_user(client, seed, fake_db):
    response = await client.post("/chat/users/u1/chats", json={"title": "Second"})

    assert response.status_code in (200, 201)
    titles = {c.title for c in fake_db.chats().items.values()}
    assert "Second" in titles


async def test_rename_chat(client, seed, fake_db):
    response = await client.patch("/chat/chats/c1", json={"title": "Renamed"})

    assert response.status_code == 200
    assert fake_db.chats().items["c1"].title == "Renamed"


async def test_rename_chat_404s_when_missing(client, seed):
    assert (await client.patch("/chat/chats/nope", json={"title": "x"})).status_code == 404


@pytest.mark.parametrize("title", ["", "   "])
async def test_rename_chat_rejects_blank(client, seed, title):
    assert (await client.patch("/chat/chats/c1", json={"title": title})).status_code == 422


async def test_list_session_chats(client, seed):
    """No UI calls this, which is exactly why it needs a test."""
    response = await client.get("/chat/sessions/s1/chats")

    assert response.status_code == 200
    assert [c["chat_id"] for c in response.json()["chats"]] == ["c1"]
