"""The /nlp routes, and which embedding model they choose.

A chat_id *is* a project_id here, and a chat may name an embedding model other
than the one in .env — the UI's model picker writes it, and the chat's vectors
are then stored at that model's width. These routes used to build their
controller from ``app.embedding_client`` regardless, which embedded the query
with the default model and searched a collection built with another. On
pgvector that surfaces as ``different vector dimensions 4096 and 768``.
"""

import pytest


@pytest.fixture
def chat_with_own_model(fake_db, seed):
    """Give the seeded chat an embedding model that is not the .env default."""
    chat = fake_db.chats().items["c1"]
    chat.embedding_model = "local/nomic-embed-text:latest"
    chat.embedding_dimensions = 768
    return seed


async def test_search_uses_the_chats_embedding_model(
    client, fake_providers, chat_with_own_model
):
    """The query must be embedded with the model the chat's vectors came from."""
    fake_providers.asked_for.clear()

    await client.post("/nlp/index/search/c1", json={"text": "ما معنى اسم لبنان؟", "limit": 3})

    embedding_requests = [r for r in fake_providers.asked_for if r[0] == "embedding"]
    assert embedding_requests, "the route never asked for an embedding client"
    assert embedding_requests[-1] == (
        "embedding",
        "local/nomic-embed-text:latest",
        768,
    )


async def test_a_project_with_no_notebook_is_unreachable(client, fake_db, seed):
    """/process and /data used to create projects that never had a chat, and
    search fell back to the .env default for them. With accounts, a project's
    owner is the owner of the chat with the same id, so a project with no
    chat has no owner and nobody may search it."""
    from data.models import Project

    fake_db.projects().items["p-no-chat"] = Project(
        project_id="p-no-chat", name="not a notebook"
    )

    response = await client.post(
        "/nlp/index/search/p-no-chat", json={"text": "hello", "limit": 3}
    )

    assert response.status_code == 404
