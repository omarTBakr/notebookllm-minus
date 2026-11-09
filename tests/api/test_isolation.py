"""No user can reach another user's data, through any route.

Driven by the app's own route table rather than a hand-written list, so a route added
later without the login layer, or one whose ids are not owner-checked, fails
here without anyone having to remember to add a test for it.
"""

import re

import pytest

from data.models import TaskExecution
from tests.conftest import sign_in

# Reachable without a session, on purpose.
PUBLIC = {
    ("POST", "/auth/signup"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/base/health"),
    ("GET", "/"),
    ("GET", "/login"),
}

OWNED = {"user_id", "session_id", "chat_id", "project_id", "task_id"}

# User A's ids (the seed user u1), filled into every path.
A_IDS = {
    "user_id": "u1",
    "session_id": "s1",
    "chat_id": "c1",
    "project_id": "c1",
    "task_id": "t1",
    "asset_id": "a1",
    "chunk_order": "0",
    "kind": "quiz",
}

_PARAM = re.compile(r"{([a-z_]+)(?::[a-z]+)?}")


def api_routes():
    """Every (method, path) the app serves, from its OpenAPI schema.

    The schema rather than ``app.routes``: since FastAPI 0.140 an included
    router stays a lazy wrapper there, and walking it means private API.
    ``app.openapi()`` works with the /openapi.json endpoint switched off.
    """
    from main import create_app

    schema = create_app(lifespan_enabled=False).openapi()
    for path, operations in schema["paths"].items():
        for method in operations:
            if method in {"get", "post", "put", "patch", "delete"}:
                yield method.upper(), path


def fill(path: str) -> str:
    return _PARAM.sub(lambda m: A_IDS.get(m.group(1), "x"), path)


def params_of(path: str) -> set[str]:
    return set(_PARAM.findall(path))


ALL = list(api_routes())
PROTECTED = [r for r in ALL if r not in PUBLIC]
OWNED_ROUTES = [r for r in PROTECTED if params_of(r[1]) & OWNED]


def test_the_route_walk_found_the_api():
    """Guards the parametrised tests below against silently checking nothing."""
    assert len(PROTECTED) > 30
    assert len(OWNED_ROUTES) > 25
    assert ("POST", "/chat/chats/{chat_id}/message") in OWNED_ROUTES


@pytest.mark.parametrize("method, path", PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
async def test_every_protected_route_needs_a_session(anon_client, seed, method, path):
    response = await anon_client.request(method, fill(path), json={})

    assert response.status_code == 401, f"{method} {path} answered {response.status_code} without a session"


@pytest.fixture
def two_users(fake_db, make_seed, client):
    """A (u1) owns c1, s1, a1 and task t1; the client is signed in as B (u2)."""
    make_seed()
    make_seed(user_id="u2", session_id="s2", chat_id="c2", asset_id="a2", title="B's notebook")
    fake_db.tasks().items["t1"] = TaskExecution(
        task_id="t1", task_name="notebookllm.process_data_task", project_id="c1"
    )
    sign_in(fake_db, client, "u2")
    return fake_db


def snapshot(db) -> dict:
    """What A has, to prove a refused request changed nothing."""
    return {
        "user": db.users().items["u1"].model_dump(),
        "chat": db.chats().items["c1"].model_dump(),
        "session": db.sessions().items["s1"].model_dump(),
        "assets": sorted(k for k, a in db.assets().items.items() if a.project_id == "c1"),
        "messages": len([m for m in db.messages().items if m.chat_id == "c1"]),
        "tasks": sorted(db.tasks().items),
    }


@pytest.mark.parametrize("method, path", OWNED_ROUTES, ids=[f"{m} {p}" for m, p in OWNED_ROUTES])
async def test_another_users_ids_are_not_found(client, two_users, method, path):
    before = snapshot(two_users)

    response = await client.request(method, fill(path), json={"text": "hi", "label": "x", "title": "x"})

    assert response.status_code == 404, f"{method} {path} let user B reach A's data ({response.status_code})"
    assert snapshot(two_users) == before


async def test_b_sees_only_their_own_lists(client, two_users):
    chats = (await client.get("/chat/users/u2/chats")).json()
    assert [c["chat_id"] for c in chats["chats"]] == ["c2"]

    users = (await client.get("/chat/users")).json()
    assert [u["user_id"] for u in users["users"]] == ["u2"]


async def test_a_task_id_in_the_query_string_is_checked_too(client, two_users):
    """/indexing takes task_id as a query parameter, which the path-based
    check cannot see; SourceService.indexing_task checks it instead."""
    response = await client.get("/chat/chats/c2/indexing", params={"task_id": "t1"})

    assert response.status_code == 200
    assert response.json() == {"active": False}


async def test_an_asset_cannot_be_renamed_through_another_notebook(client, two_users):
    """An existing gap: rename only checked that the chat existed."""
    response = await client.patch("/chat/chats/c2/assets/a1", json={"name": "pwned"})

    assert response.status_code == 404
    assert two_users.assets().items["a1"].name != "pwned"
