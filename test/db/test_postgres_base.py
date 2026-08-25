"""The Postgres mixin: turning asyncpg rows into models, without a server."""

import json

import pytest

from factories.db.postgres.base_repository import PostgresBaseRepository
from models.db_schema import User


@pytest.fixture
def repo():
    return PostgresBaseRepository.__new__(PostgresBaseRepository)


def test_a_missing_row_becomes_none(repo):
    assert repo._record_to_model(None, User) is None


def test_a_row_becomes_a_model(repo):
    row = {"user_id": "u1", "label": "Omar"}

    user = repo._record_to_model(row, User)

    assert isinstance(user, User)
    assert user.user_id == "u1"


def test_json_columns_are_decoded(repo):
    """asyncpg hands JSONB back as a string unless a codec is registered, so
    a list column would otherwise fail model validation."""
    from models.db_schema import Chat

    row = {
        "chat_id": "c1", "session_id": "s1", "user_id": "u1",
        "excluded_assets": json.dumps(["a1", "a2"]),
    }

    chat = repo._record_to_model(row, Chat)

    assert chat.excluded_assets == ["a1", "a2"]


def test_a_string_that_merely_looks_like_json_is_left_alone(repo):
    """A label of "{" must not blow up the decode attempt."""
    user = repo._record_to_model({"user_id": "u1", "label": "{"}, User)

    assert user.label == "{"


def test_the_id_column_becomes_an_objectid(repo):
    """The schemas are shared with Mongo, so `_id` is typed as a real
    ObjectId. Postgres stores the 24-hex string, and pydantic refuses a str
    for that field — every read failed validation until this conversion."""
    from bson.objectid import ObjectId

    oid = ObjectId()

    user = repo._record_to_model({"id": str(oid), "user_id": "u1"}, User)

    assert user.id == oid


def test_an_id_that_is_not_an_objectid_is_left_for_pydantic_to_reject(repo):
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError):
        repo._record_to_model({"id": "not-an-oid", "user_id": "u1"}, User)


def test_every_objectid_field_is_coerced_not_just_the_primary_key(repo):
    """DataChunk has two: `_id` and project_id. A hardcoded `_id`-only
    conversion left the second one failing validation on every read."""
    from bson.objectid import ObjectId
    from models.db_schema import DataChunk

    project_oid = ObjectId()

    chunk = repo._record_to_model(
        {
            "id": str(ObjectId()),
            "project_id": str(project_oid),
            "asset_id": "a1",
            "chunk_order": 0,
            "chunk_content": "text",
        },
        DataChunk,
    )

    assert chunk.project_id == project_oid


def test_the_objectid_field_list_is_derived_from_the_model(repo):
    from models.db_schema import DataChunk, User

    assert set(repo._objectid_fields(User)) == {"_id"}
    assert set(repo._objectid_fields(DataChunk)) == {"_id", "project_id"}


def test_a_list_of_objectids_is_coerced_element_by_element(repo):
    """Project.chunks_ids and assets_ids are `list[ObjectId]`, stored as JSONB
    arrays of hex strings. Coercing only scalars left every project that had
    ever ingested a document failing validation on read."""
    from bson.objectid import ObjectId
    from models.db_schema import Project

    chunk_oids = [ObjectId(), ObjectId()]
    asset_oid = ObjectId()

    project = repo._record_to_model(
        {
            "id": str(ObjectId()),
            "project_id": "p1",
            "name": "doc.pdf",
            "chunks_ids": [str(o) for o in chunk_oids],
            "assets_ids": [str(asset_oid)],
        },
        Project,
    )

    assert project.chunks_ids == chunk_oids
    assert project.assets_ids == [asset_oid]


def test_an_orm_row_becomes_a_model(repo):
    """The repositories hand back mapped instances, not dicts.

    Timestamps are spelled out because they are *server* defaults: a row that
    has been through the database always has them, a freshly constructed
    instance does not.
    """
    from datetime import datetime, timezone

    from bson.objectid import ObjectId

    from factories.db.postgres.base_repository import UserRow

    now = datetime.now(timezone.utc)
    oid = ObjectId()
    row = UserRow(
        id=str(oid), user_id="u1", label="Omar", created_at=now, updated_at=now
    )

    user = repo._record_to_model(row, User)

    assert isinstance(user, User)
    assert (user.id, user.user_id, user.label) == (oid, "u1", "Omar")
