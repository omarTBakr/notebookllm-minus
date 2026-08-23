"""The transcript, and the streamed answer."""

from enums import ChatRole
from models.db_schema import Message


async def test_history_is_empty_for_a_new_chat(client, seed):
    response = await client.get("/chat/chats/c1/messages")

    assert response.status_code == 200
    assert response.json()["messages"] == []


async def test_history_404s_for_an_unknown_chat(client, seed):
    assert (await client.get("/chat/chats/nope/messages")).status_code == 404


async def test_history_returns_stored_turns_in_order(client, seed, fake_db):
    for role, text in [(ChatRole.USER, "q"), (ChatRole.ASSISTANT, "a")]:
        await fake_db.messages().create_message(
            Message(message_id=f"m-{text}", chat_id="c1", role=role, content=text)
        )

    body = (await client.get("/chat/chats/c1/messages")).json()

    assert [m["content"] for m in body["messages"]] == ["q", "a"]


async def test_citations_are_re_resolved_against_current_asset_names(client, seed, fake_db):
    """A citation is frozen into the message when the answer is written. If a
    source is renamed afterwards the transcript must follow, or it cites a name
    that no longer exists anywhere."""
    await fake_db.messages().create_message(
        Message(
            message_id="m1", chat_id="c1", role=ChatRole.ASSISTANT, content="a",
            citations=[{"num": 1, "asset_id": "a1", "source": "note1.txt",
                        "chunk_order": 0, "score": 0.9}],
        )
    )
    fake_db.assets().items["a1"].name = "Groceries"

    body = (await client.get("/chat/chats/c1/messages")).json()

    assert body["messages"][0]["citations"][0]["source"] == "Groceries"


async def test_a_citation_for_a_deleted_asset_keeps_its_stored_name(client, seed, fake_db):
    await fake_db.messages().create_message(
        Message(
            message_id="m1", chat_id="c1", role=ChatRole.ASSISTANT, content="a",
            citations=[{"num": 1, "asset_id": "gone", "source": "deleted.txt",
                        "chunk_order": 0, "score": 0.9}],
        )
    )

    body = (await client.get("/chat/chats/c1/messages")).json()

    assert body["messages"][0]["citations"][0]["source"] == "deleted.txt"


async def test_asking_a_question_streams_an_answer(client, seed):
    response = await client.post("/chat/chats/c1/message", json={"text": "hello?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data:" in response.text


async def test_the_answer_is_persisted(client, seed, fake_db):
    await client.post("/chat/chats/c1/message", json={"text": "hello?"})

    roles = [m.role for m in fake_db.messages().items]
    assert ChatRole.ASSISTANT in roles


async def test_asking_an_unknown_chat_404s(client, seed):
    response = await client.post("/chat/chats/nope/message", json={"text": "hi"})

    assert response.status_code == 404


async def test_an_empty_question_is_rejected(client, seed):
    assert (await client.post("/chat/chats/c1/message", json={"text": ""})).status_code == 422
