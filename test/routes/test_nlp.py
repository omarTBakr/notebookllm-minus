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


async def test_search_falls_back_to_the_default_for_a_non_chat_project(
    client, fake_db, fake_providers, seed
):
    """/process and /data create projects that never had a chat.

    Those must keep working on the .env default rather than 404-ing because no
    chat row exists for the id.
    """
    from models.db_schema import Project

    fake_db.projects().items["p-no-chat"] = Project(
        project_id="p-no-chat", name="not a notebook"
    )
    fake_providers.asked_for.clear()

    response = await client.post(
        "/nlp/index/search/p-no-chat", json={"text": "hello", "limit": 3}
    )

    # It gets far enough to look for a vector index, which is the point: the
    # missing chat must not be what stops it.
    assert "no vector index" in response.json()["detail"]

    # The fallback reuses the client the lifespan already built rather than
    # asking the cache again, so the assertion is that nothing asked for a
    # *named* model — there is no chat to name one.
    named = [r for r in fake_providers.asked_for if r[0] == "embedding" and r[1]]
    assert not named
