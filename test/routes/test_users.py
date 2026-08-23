"""Profiles: create, list, fetch, rename."""

import pytest

from models.db_schema import User


@pytest.fixture
def seeded(fake_db):
    fake_db.users().items["u1"] = User(user_id="u1", label="Omar")
    return fake_db


async def test_create_user_returns_an_id(client):
    response = await client.post("/chat/users", json={"label": "Omar"})

    assert response.status_code in (200, 201)
    assert response.json()["user_id"]


async def test_create_user_persists_the_label(client, fake_db):
    await client.post("/chat/users", json={"label": "Omar"})

    stored = list(fake_db.users().items.values())
    assert [u.label for u in stored] == ["Omar"]


async def test_list_users_returns_every_profile(client, seeded):
    response = await client.get("/chat/users")

    assert response.status_code == 200
    assert [u["user_id"] for u in response.json()["users"]] == ["u1"]


async def test_get_user_returns_the_profile(client, seeded):
    response = await client.get("/chat/users/u1")

    assert response.status_code == 200
    assert response.json()["label"] == "Omar"


async def test_get_user_404s_for_an_unknown_id(client, seeded):
    """A wiped database leaves the browser holding a stale id; that is a 404,
    not a 500."""
    response = await client.get("/chat/users/nope")

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_rename_user(client, seeded, fake_db):
    response = await client.patch("/chat/users/u1", json={"label": "Renamed"})

    assert response.status_code == 200
    assert fake_db.users().items["u1"].label == "Renamed"


async def test_rename_user_404s_for_an_unknown_id(client, seeded):
    response = await client.patch("/chat/users/nope", json={"label": "x"})

    assert response.status_code == 404


@pytest.mark.parametrize("label", ["", "   "])
async def test_rename_rejects_a_blank_label(client, seeded, label):
    """min_length plus a visible-text validator, so both are 422."""
    response = await client.patch("/chat/users/u1", json={"label": label})

    assert response.status_code == 422
