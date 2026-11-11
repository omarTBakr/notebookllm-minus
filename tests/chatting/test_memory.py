"""`/memory`: the command, the extraction, and finding a fact again."""

import json

import pytest

from application.services.memory.MemoryService import MEMORY_TOP_K, MemoryService
from presentation.routes.chat import memory as memory_route
from presentation.routes.chat.memory import is_memory_command
from shared.exceptions import StructuredOutputError
from tests.support.db import FakeVectorRepository
from tests.support.llm import FakeChatClient, FakeEmbeddingClient


class FakeFacts:
    """PersonalUserInfoRepository in memory: one row id per (user_id, key)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}

    async def upsert_fact(self, user_id, key, description):
        row = self.rows.setdefault((user_id, key), {"id": f"row-{len(self.rows) + 1}"})
        row["description"] = description
        return row["id"]


def facts_json(*pairs):
    return json.dumps({"facts": [{"key": key, "description": description} for key, description in pairs]})


def service(reply, vectors=None, facts=None):
    return MemoryService(
        chat_client=FakeChatClient(reply=reply),
        embedding_client=FakeEmbeddingClient(),
        vector_repo=vectors or FakeVectorRepository(),
        personal_info_repo=facts or FakeFacts(),
    )


# --- the command -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("/memory I am vegetarian", "I am vegetarian"),
        ("  /Memory   I am vegetarian  ", "I am vegetarian"),
        ("/memory", ""),
        ("what is in my memory?", None),
        ("tell me about /memory", None),
    ],
)
def test_only_a_message_that_starts_with_the_command_is_one(text, expected):
    assert is_memory_command(text) == expected


# --- extraction ------------------------------------------------------------------------


async def test_facts_are_stored_and_embedded_under_the_user():
    vectors, facts = FakeVectorRepository(), FakeFacts()
    memory = service(facts_json(("diet", "The user is vegetarian."), ("job", "The user is a nurse.")), vectors, facts)

    stored = await memory.extract_and_store("u1", "I am a vegetarian nurse", "en")

    assert [fact.key for fact in stored] == ["diet", "job"]
    assert facts.rows[("u1", "diet")]["description"] == "The user is vegetarian."

    points = vectors.points[MemoryService.collection_name("u1")]
    assert [point["text"] for point in points] == ["The user is vegetarian.", "The user is a nurse."]
    assert points[0]["metadata"] == {"user_id": "u1", "key": "diet"}
    # The vector is filed under the row's id, which is what lets a later save
    # of the same key replace its embedding.
    assert [point["id"] for point in points] == [facts.rows[("u1", "diet")]["id"], facts.rows[("u1", "job")]["id"]]


async def test_saving_a_key_again_reuses_its_row_and_vector_id():
    vectors, facts = FakeVectorRepository(), FakeFacts()

    await service(facts_json(("diet", "The user is vegetarian.")), vectors, facts).extract_and_store("u1", "x", "en")
    await service(facts_json(("diet", "The user is vegan.")), vectors, facts).extract_and_store("u1", "y", "en")

    assert len(facts.rows) == 1
    assert facts.rows[("u1", "diet")]["description"] == "The user is vegan."
    ids = {point["id"] for point in vectors.points[MemoryService.collection_name("u1")]}
    assert ids == {facts.rows[("u1", "diet")]["id"]}


async def test_a_message_with_nothing_to_remember_stores_nothing():
    vectors, facts = FakeVectorRepository(), FakeFacts()

    assert await service(facts_json(), vectors, facts).extract_and_store("u1", "hello", "en") == []
    assert facts.rows == {} and vectors.collections == {}


async def test_a_reply_without_the_facts_field_is_an_error_not_zero_facts():
    """`facts` is required: a model that echoes something else back must not
    read as a clean, silent "nothing to remember"."""
    with pytest.raises(StructuredOutputError):
        await service(json.dumps({"note": "no facts here"})).extract_and_store("u1", "x", "en")


async def test_two_users_never_share_a_collection():
    assert MemoryService.collection_name("u1") != MemoryService.collection_name("u2")
    assert MemoryService.collection_name("a b/c") == "user_memory_a_b_c"


# --- finding a fact again ---------------------------------------------------------------


async def test_search_on_a_user_with_no_memory_is_empty_not_an_error():
    assert await service("{}").search("nobody", "what do I eat?") == []


async def test_search_returns_the_stored_hits_as_memory_citations():
    hit = {"text": "The user is vegetarian.", "score": 0.91234, "metadata": {"key": "diet", "user_id": "u1"}}
    vectors = FakeVectorRepository(hits=[hit])
    await vectors.create_collection(MemoryService.collection_name("u1"), 8)
    memory = service("{}", vectors)

    hits = await memory.search("u1", "what do I eat?")

    assert hits == [hit]
    assert vectors.searched[-1]["limit"] == MEMORY_TOP_K
    assert MemoryService.to_citations(hits) == [
        {"num": 1, "key": "diet", "description": "The user is vegetarian.", "score": 0.9123}
    ]


# --- through the API ---------------------------------------------------------------------


@pytest.fixture
def queued(monkeypatch):
    """Record what the route publishes instead of reaching for a broker."""
    calls = []

    class Result:
        id = "memory-task-1"

    def apply_async(args=None, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(memory_route.extract_memory_task, "apply_async", apply_async)
    monkeypatch.setattr(memory_route, "mark_queued", lambda task_id: None)
    return calls


async def test_a_memory_message_is_queued_and_acknowledged_not_answered(client, seed, fake_db, queued):
    response = await client.post("/chat/chats/c1/message", json={"text": "/memory I am vegetarian"})

    assert response.status_code == 200
    assert "Memory updated" in response.text
    # (chat_id, user_id, text, lang), in that order. Swapped once: every fact
    # was filed under the chat's id and no answer ever found one.
    assert queued == [["c1", "u1", "I am vegetarian", "en"]]

    stored = [m for m in fake_db.messages().items if m.chat_id == "c1"]
    assert stored[-2].content == "/memory I am vegetarian"
    assert stored[-1].role.value == "assistant" and "Memory updated" in stored[-1].content


async def test_the_same_memory_message_twice_queues_one_extraction(client, seed, queued):
    await client.post("/chat/chats/c1/message", json={"text": "/memory I am vegetarian"})
    again = await client.post("/chat/chats/c1/message", json={"text": "/memory I am vegetarian"})

    assert len(queued) == 1
    assert "Already remembering" in again.text


async def test_the_memory_preview_with_an_empty_draft_is_empty(client, seed):
    response = await client.get("/chat/chats/c1/memory", params={"q": "  "})

    assert response.status_code == 200
    assert response.json() == {"memories": []}
