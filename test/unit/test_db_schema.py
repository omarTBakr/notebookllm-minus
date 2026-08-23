"""The stored models: required fields, bounds and validators."""

import pytest
from bson.objectid import ObjectId
from pydantic import ValidationError

from enums import AssetType
from models.db_schema import Asset, Chat, DataChunk, Project, Session, User


@pytest.mark.parametrize("model, kwargs, field", [
    (Project, {"project_id": "p"}, "name"),
    (Asset, {"asset_id": "a", "asset_type": AssetType.TEXT, "project_id": "p"}, "name"),
    (Chat, {"chat_id": "c", "session_id": "s", "user_id": "u"}, "title"),
])
@pytest.mark.parametrize("blank", ["   ", "\t"])
def test_names_must_contain_visible_text(model, kwargs, field, blank):
    with pytest.raises(ValidationError):
        model(**kwargs, **{field: blank})


def test_names_are_stripped():
    assert Project(project_id="p", name="  Report  ").name == "Report"


@pytest.mark.parametrize("field, bad", [
    ("temperature", 2.5), ("temperature", -0.1),
    ("max_tokens", 63), ("max_tokens", 32769),
    ("chunk_size", 99), ("chunk_size", 8001),
    ("overlap_size", -1), ("overlap_size", 2001),
    ("embedding_dimensions", 0),
])
def test_chat_numeric_bounds(field, bad):
    with pytest.raises(ValidationError):
        Chat(chat_id="c", session_id="s", user_id="u", **{field: bad})


def test_chat_defaults_are_unset_meaning_inherit_from_env():
    chat = Chat(chat_id="c", session_id="s", user_id="u")

    assert chat.generation_model is None
    assert chat.embedding_model is None
    assert chat.excluded_assets == []
    assert chat.has_documents is False


def test_a_mongo_document_round_trips_through_the_alias():
    """Repositories hand raw documents straight to the model, so `_id` has to
    populate `id` — and `id=` has to keep working for freshly built ones."""
    oid = ObjectId()
    from_mongo = User(**{"_id": oid, "user_id": "u", "label": "Omar"})
    from_code = User(id=oid, user_id="u", label="Omar")

    assert from_mongo.id == from_code.id == oid


def test_chunk_order_may_not_be_negative():
    with pytest.raises(ValidationError):
        DataChunk(project_id=ObjectId(), asset_id="a", chunk_order=-1,
                  chunk_content="x")


def test_chunk_requires_a_project_object_id():
    with pytest.raises(ValidationError):
        DataChunk(project_id="not-an-objectid", asset_id="a", chunk_order=0,
                  chunk_content="x")


def test_asset_bytes_have_a_ceiling():
    with pytest.raises(ValidationError):
        Asset(asset_id="a", asset_type=AssetType.TEXT, project_id="p", name="n",
              file_bytes=b"x" * (10_485_760 + 1))


def test_timestamps_are_timezone_aware():
    """Naive datetimes compare badly against Mongo's, which are UTC."""
    session = Session(session_id="s", user_id="u")

    assert session.created_at.tzinfo is not None
