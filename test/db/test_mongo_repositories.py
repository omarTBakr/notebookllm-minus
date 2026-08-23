"""Mongo repositories against a fake collection.

These pin the three shapes the repositories repeat — create, patch and upsert
— including which typed error each failure becomes, because the HTTP status a
route returns is derived from the exception class.
"""

import pytest

from exceptions import AssetNotFoundError, ChatNotFoundError, DbError, UserNotFoundError
from factories.db.mongo.asset_repository import MongoAssetRepository
from factories.db.mongo.chat_repository import MongoChatRepository
from factories.db.mongo.user_repository import MongoUserRepository
from models.db_schema import Asset, Chat, User
from enums import AssetType
from test.fakes.mongo import FakeMongoDb, driver_failure


@pytest.fixture
def db():
    return FakeMongoDb()


@pytest.fixture
def users(db):
    return MongoUserRepository(db)


@pytest.fixture
def chats(db):
    return MongoChatRepository(db)


@pytest.fixture
def assets(db):
    return MongoAssetRepository(db)


def an_asset(**kw):
    fields = {"asset_id": "a1", "asset_type": AssetType.TEXT, "project_id": "p1",
              "name": "note1.txt"}
    return Asset(**{**fields, **kw})


# --- create -------------------------------------------------------------------


async def test_create_writes_the_document(users):
    await users.create_user(User(user_id="u1", label="Omar"))

    [doc] = users.collection.docs
    assert doc["user_id"] == "u1" and doc["label"] == "Omar"


async def test_create_uses_the_alias_so_the_id_is_stored_as_underscore_id(users):
    await users.create_user(User(user_id="u1"))

    assert "_id" in users.collection.docs[0]
    assert "id" not in users.collection.docs[0]


async def test_a_driver_failure_on_create_becomes_a_db_error(users):
    users.collection.fail_with = driver_failure()

    with pytest.raises(DbError):
        await users.create_user(User(user_id="u1"))


async def test_db_error_is_a_503(users):
    users.collection.fail_with = driver_failure()

    with pytest.raises(DbError) as caught:
        await users.create_user(User(user_id="u1"))

    assert caught.value.status_code == 503


# --- read ---------------------------------------------------------------------


async def test_get_returns_the_model(users):
    await users.create_user(User(user_id="u1", label="Omar"))

    assert (await users.get_user("u1")).label == "Omar"


async def test_get_raises_the_specific_not_found(users):
    with pytest.raises(UserNotFoundError):
        await users.get_user("nope")


async def test_not_found_is_a_404(users):
    with pytest.raises(UserNotFoundError) as caught:
        await users.get_user("nope")

    assert caught.value.status_code == 404


# --- patch --------------------------------------------------------------------


async def test_rename_sets_the_field_and_touches_updated_at(users):
    await users.create_user(User(user_id="u1", label="Omar"))

    await users.rename("u1", "Renamed")

    _, _, update, _ = [c for c in users.collection.calls
                       if c[0] == "find_one_and_update"][0]
    assert update["$set"]["label"] == "Renamed"
    assert "updated_at" in update["$set"]


async def test_rename_raises_when_the_document_is_absent(users):
    with pytest.raises(UserNotFoundError):
        await users.rename("nope", "x")


async def test_asset_rename_uses_its_own_not_found(assets):
    with pytest.raises(AssetNotFoundError):
        await assets.rename("nope", "x")


async def test_chat_rename_uses_its_own_not_found(chats):
    with pytest.raises(ChatNotFoundError):
        await chats.rename("nope", "x")


async def test_a_driver_failure_on_patch_becomes_a_db_error(users):
    await users.create_user(User(user_id="u1"))
    users.collection.fail_with = driver_failure()

    with pytest.raises(DbError):
        await users.rename("u1", "x")


async def test_set_settings_writes_only_what_it_was_given(chats):
    await chats.create_chat(Chat(chat_id="c1", session_id="s1", user_id="u1"))

    await chats.set_settings("c1", {"temperature": 0.7})

    update = [c for c in chats.collection.calls if c[0] == "find_one_and_update"][0][2]
    assert update["$set"]["temperature"] == 0.7


async def test_set_has_documents(chats):
    await chats.create_chat(Chat(chat_id="c1", session_id="s1", user_id="u1"))

    await chats.set_has_documents("c1", True)

    assert (await chats.get_chat("c1")).has_documents is True


# --- upsert -------------------------------------------------------------------


async def test_update_asset_inserts_when_absent(assets):
    await assets.update_asset(an_asset())

    assert len(assets.collection.docs) == 1


async def test_update_asset_overwrites_when_present(assets):
    await assets.update_asset(an_asset())
    await assets.update_asset(an_asset(name="renamed.txt"))

    assert len(assets.collection.docs) == 1
    assert (await assets.get_asset("a1")).name == "renamed.txt"


async def test_upsert_does_not_reset_created_at(assets):
    """created_at goes in $setOnInsert; overwriting must not move it."""
    await assets.update_asset(an_asset())

    update = [c for c in assets.collection.calls if c[0] == "find_one_and_update"][-1][2]
    assert "created_at" in update.get("$setOnInsert", {})
    assert "created_at" not in update["$set"]
