"""The Mongo mixin: index specs, built without a database."""

import pytest

from enums import DatabaseCollection
from factories.db.mongo.base_model import BaseModel


class _FakeDb(dict):
    """`db[name]` is all BaseModel.__init__ does with it."""

    def __getitem__(self, name):
        return f"collection:{name}"


@pytest.fixture
def model():
    return BaseModel(_FakeDb(), DatabaseCollection.ASSETS)


def test_it_binds_the_named_collection(model):
    assert model.collection == "collection:assets"


def test_the_name_is_derived_from_the_keys_and_directions(model):
    spec = model.get_index([("project_id", 1), ("created_at", -1)])

    assert spec["name"] == "project_id_asc_created_at_desc_idx"


def test_the_keys_are_passed_through_untouched(model):
    keys = [("asset_id", 1)]

    assert model.get_index(keys)["key"] == keys


def test_an_explicit_name_wins(model):
    assert model.get_index([("a", 1)], name="chosen")["name"] == "chosen"


@pytest.mark.parametrize("unique", [True, False])
def test_uniqueness_is_carried_through(model, unique):
    assert model.get_index([("a", 1)], unique=unique)["unique"] is unique
