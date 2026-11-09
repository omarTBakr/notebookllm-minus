"""Accounts: signup, login, sessions, logout, password change, claiming a profile."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.account.AuthService import FAILED_LOGINS, hash_token
from data.models import Chat, Session, User
from shared.utils import get_settings

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _forget_failed_logins():
    FAILED_LOGINS._failures.clear()
    yield
    FAILED_LOGINS._failures.clear()


def cookie_header(response) -> str:
    return next(v for k, v in response.headers.multi_items() if k == "set-cookie")


async def signup(client, email="ada@example.com", password=PASSWORD, **extra):
    return await client.post("/auth/signup", json={"email": email, "password": password, **extra})


# --- signup ------------------------------------------------------------------------


async def test_signup_creates_an_account_and_signs_in(anon_client, fake_db):
    response = await signup(anon_client, email="Ada@Example.com", display_name="Ada")

    assert response.status_code == 201
    body = response.json()["user"]
    assert body["email"] == "ada@example.com"  # stored lowercased
    assert body["label"] == "Ada"
    assert "password_hash" not in body

    header = cookie_header(response).lower()
    assert "httponly" in header and "samesite=lax" in header

    me = await anon_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["user_id"] == body["user_id"]

    stored = fake_db.users().items[body["user_id"]]
    assert stored.password_hash and stored.password_hash != PASSWORD
    assert stored.password_hash.startswith("$argon2")


async def test_duplicate_email_is_refused_case_insensitively(anon_client):
    await signup(anon_client, email="ada@example.com")
    response = await signup(anon_client, email="ADA@example.com")

    assert response.status_code == 409


@pytest.mark.parametrize(
    "email, password",
    [("not-an-email", PASSWORD), ("ada@example.com", "short"), ("a b@example.com", PASSWORD)],
)
async def test_bad_signup_input_is_a_400(anon_client, email, password):
    assert (await signup(anon_client, email=email, password=password)).status_code == 400


# --- login -------------------------------------------------------------------------


async def test_login_signs_in(anon_client, app):
    await signup(anon_client)
    anon_client.cookies.clear()

    response = await anon_client.post("/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})

    assert response.status_code == 200
    assert (await anon_client.get("/auth/me")).status_code == 200


async def test_wrong_password_and_unknown_email_look_the_same(anon_client):
    await signup(anon_client)
    anon_client.cookies.clear()

    wrong = await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": "nope-nope"})
    unknown = await anon_client.post("/auth/login", json={"email": "who@example.com", "password": "nope-nope"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_repeated_failures_lock_the_email(anon_client, monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_MAX_FAILURES", "3")
    get_settings.cache_clear()
    await signup(anon_client)
    anon_client.cookies.clear()

    for _ in range(3):
        await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": "nope-nope"})

    locked = await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert locked.status_code == 429


async def test_a_legacy_profile_cannot_be_signed_into(anon_client, fake_db):
    fake_db.users().items["old"] = User(user_id="old", label="Old profile")
    response = await anon_client.post("/auth/login", json={"email": "old@example.com", "password": PASSWORD})
    assert response.status_code == 401


# --- sessions ----------------------------------------------------------------------


async def test_no_cookie_is_401_on_protected_routes(anon_client, seed):
    assert (await anon_client.get("/chat/chats/c1")).status_code == 401
    assert (await anon_client.get("/auth/me")).status_code == 401


async def test_an_expired_session_is_refused_and_removed(anon_client, fake_db):
    await signup(anon_client)
    token_hash, session = next(iter(fake_db.auth_sessions().items.items()))
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert (await anon_client.get("/auth/me")).status_code == 401
    assert token_hash not in fake_db.auth_sessions().items


async def test_an_active_session_slides_forward(anon_client, fake_db):
    await signup(anon_client)
    session = next(iter(fake_db.auth_sessions().items.values()))
    session.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=2)
    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    await anon_client.get("/auth/me")

    assert session.expires_at > datetime.now(timezone.utc) + timedelta(days=1)


async def test_only_the_token_hash_is_stored(anon_client, fake_db):
    response = await signup(anon_client)
    token = response.cookies.get(get_settings().AUTH_COOKIE_NAME)

    assert token not in fake_db.auth_sessions().items
    assert hash_token(token) in fake_db.auth_sessions().items


async def test_logout_ends_the_session(anon_client, fake_db):
    response = await signup(anon_client)
    token = response.cookies.get(get_settings().AUTH_COOKIE_NAME)

    out = await anon_client.post("/auth/logout")

    assert out.status_code == 200
    assert "max-age=0" in cookie_header(out).lower()
    assert hash_token(token) not in fake_db.auth_sessions().items
    anon_client.cookies.set(get_settings().AUTH_COOKIE_NAME, token)  # replaying it
    assert (await anon_client.get("/auth/me")).status_code == 401


async def test_logout_all_ends_every_session(anon_client, fake_db):
    await signup(anon_client)
    await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert len(fake_db.auth_sessions().items) == 2

    await anon_client.post("/auth/logout-all")

    assert fake_db.auth_sessions().items == {}


async def test_change_password_keeps_this_session_and_ends_the_others(anon_client, fake_db):
    await signup(anon_client)
    first_cookie = anon_client.cookies.get(get_settings().AUTH_COOKIE_NAME)
    await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})

    wrong = await anon_client.post(
        "/auth/password", json={"current_password": "nope-nope", "new_password": "a brand new one"}
    )
    assert wrong.status_code == 401

    ok = await anon_client.post(
        "/auth/password", json={"current_password": PASSWORD, "new_password": "a brand new one"}
    )
    assert ok.status_code == 200
    assert len(fake_db.auth_sessions().items) == 1
    assert hash_token(first_cookie) not in fake_db.auth_sessions().items

    anon_client.cookies.clear()
    old = await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    new = await anon_client.post("/auth/login", json={"email": "ada@example.com", "password": "a brand new one"})
    assert old.status_code == 401 and new.status_code == 200


async def test_deleting_the_account_ends_its_sessions(anon_client, fake_db):
    user = (await signup(anon_client)).json()["user"]

    response = await anon_client.delete(f"/chat/users/{user['user_id']}")

    assert response.status_code == 200
    assert fake_db.auth_sessions().items == {}
    assert (await anon_client.get("/auth/me")).status_code == 401


# --- claiming a profile from before accounts --------------------------------------------


def legacy_profile(fake_db, user_id="legacy"):
    fake_db.users().items[user_id] = User(user_id=user_id, label="Old")
    fake_db.sessions().items["ls"] = Session(session_id="ls", user_id=user_id)
    fake_db.chats().items["lc"] = Chat(chat_id="lc", session_id="ls", user_id=user_id, title="Old notes")


async def test_signup_can_claim_a_legacy_profile_and_keep_its_notebooks(anon_client, fake_db):
    legacy_profile(fake_db)

    response = await signup(anon_client, claim_user_id="legacy")

    assert response.json()["user"]["user_id"] == "legacy"
    chats = (await anon_client.get("/chat/users/legacy/chats")).json()
    assert [c["chat_id"] for c in chats["chats"]] == ["lc"]


async def test_a_profile_with_an_account_cannot_be_claimed(anon_client, fake_db):
    legacy_profile(fake_db)
    await signup(anon_client, email="first@example.com", claim_user_id="legacy")
    anon_client.cookies.clear()

    second = await signup(anon_client, email="second@example.com", claim_user_id="legacy")

    assert second.status_code == 201
    assert second.json()["user"]["user_id"] != "legacy"
    assert (await anon_client.get("/chat/chats/lc")).status_code == 404


# --- pages and hardening ---------------------------------------------------------------


async def test_the_app_page_redirects_to_login_when_signed_out(anon_client):
    response = await anon_client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_the_login_page_redirects_to_the_app_when_signed_in(client, seed):
    response = await client.get("/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_the_login_page_renders(anon_client):
    response = await anon_client.get("/login")
    assert response.status_code == 200
    assert "auth.js" in response.text


async def test_a_cross_site_post_is_refused(anon_client):
    response = await anon_client.post(
        "/auth/login",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


async def test_a_same_site_post_is_allowed(anon_client):
    await signup(anon_client)
    response = await anon_client.post(
        "/auth/login",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={"Origin": "http://test"},
    )
    assert response.status_code == 200


async def test_api_docs_are_hidden(anon_client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await anon_client.get(path)).status_code == 404
