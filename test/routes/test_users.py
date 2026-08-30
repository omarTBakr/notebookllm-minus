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


# --- deleting a user ----------------------------------------------------------
#
# Nothing in either store cascades on its own, so the route walks the whole
# tree. These check that every level actually goes, and that nothing belonging
# to anyone else goes with it.


def a_text_file(name="doc.txt", body=b"the quick brown fox " * 40):
    return {"file": (name, body, "text/plain")}


async def test_deleting_a_user_removes_the_user(client, seed, fake_db):
    response = await client.delete("/chat/users/u1")

    assert response.status_code == 200, response.text
    assert "u1" not in fake_db.users().items


async def test_deleting_a_user_removes_their_sessions_and_chats(client, seed, fake_db):
    await client.delete("/chat/users/u1")

    assert "s1" not in fake_db.sessions().items
    assert "c1" not in fake_db.chats().items


async def test_deleting_a_user_removes_their_documents_and_chunks(client, seed, fake_db):
    """The whole point: a user's uploads and everything derived from them must
    not outlive the user."""
    await client.post("/chat/chats/c1/documents", files=a_text_file("keep-me.txt"))
    assert fake_db.chunks().items, "nothing was ingested, so this proves nothing"

    response = await client.delete("/chat/users/u1")

    assert response.status_code == 200, response.text
    assert fake_db.assets().items == {}
    assert fake_db.chunks().items == []
    assert fake_db.projects().items == {}


async def test_deleting_a_user_removes_their_vectors(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("indexed.txt"))
    assert any(rows for rows in fake_db.vectors().points.values())

    await client.delete("/chat/users/u1")

    surviving = [row for rows in fake_db.vectors().points.values() for row in rows]
    assert not surviving, "vectors outlived the user they belonged to"


async def test_deleting_a_user_removes_their_messages(client, seed, fake_db):
    from models.db_schema import Message
    from enums import ChatRole

    fake_db.messages().items.append(
        Message(message_id="m1", chat_id="c1", role=ChatRole.USER, content="hello")
    )

    await client.delete("/chat/users/u1")

    assert fake_db.messages().items == []


async def test_the_response_reports_what_went(client, seed, fake_db):
    await client.post("/chat/chats/c1/documents", files=a_text_file("counted.txt"))

    body = (await client.delete("/chat/users/u1")).json()

    assert body["user_id"] == "u1"
    deleted = body["deleted"]
    assert deleted["chats"] == 1
    assert deleted["sessions"] == 1
    # The seed leaves one asset in this notebook, and the upload adds another.
    assert deleted["assets"] == 2
    assert deleted["chunks"] > 0


async def test_deleting_a_user_leaves_another_users_data_alone(client, seed, fake_db):
    """The delete is scoped by ownership, not by "everything that looks similar"."""
    from models.db_schema import Chat, Project, Session, User

    fake_db.users().items["u2"] = User(user_id="u2", label="Someone else")
    fake_db.sessions().items["s2"] = Session(session_id="s2", user_id="u2")
    fake_db.chats().items["c2"] = Chat(
        chat_id="c2", session_id="s2", user_id="u2", title="Their notebook"
    )
    fake_db.projects().items["c2"] = Project(project_id="c2", name="Their notebook")
    await client.post("/chat/chats/c2/documents", files=a_text_file("theirs.txt"))

    await client.delete("/chat/users/u1")

    assert "u2" in fake_db.users().items
    assert "s2" in fake_db.sessions().items
    assert "c2" in fake_db.chats().items
    assert [a for a in fake_db.assets().items.values() if a.project_id == "c2"]
    assert [c for c in fake_db.chunks().items if str(c.project_id) == str(
        fake_db.projects().items["c2"].id
    )]


async def test_deleting_a_user_with_an_empty_notebook_works(client, seed, fake_db):
    """A notebook nobody uploaded to has no project row. That is normal, not an
    error, and must not stop the rest of the delete."""
    fake_db.projects().items.clear()

    response = await client.delete("/chat/users/u1")

    assert response.status_code == 200, response.text
    assert "u1" not in fake_db.users().items


async def test_deleting_an_unknown_user_404s(client, seed):
    response = await client.delete("/chat/users/nobody")

    assert response.status_code == 404
