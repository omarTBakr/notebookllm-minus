"""Pure helpers on NLPService: naming and point keys."""

import pytest

from application.services import NLPService


@pytest.mark.parametrize("project_id, expected", [
    ("abc", "project_abc"),
    ("a-b_c", "project_a-b_c"),
    # A chat id is a uuid, which is already safe.
    ("40f5478b-e933-49ec-991c-a42097634993",
     "project_40f5478b-e933-49ec-991c-a42097634993"),
    # Anything Qdrant would refuse in a collection name is folded to _.
    ("a/b", "project_a_b"),
    ("a b", "project_a_b"),
    ("drop table;", "project_drop_table_"),
])
def test_collection_name_is_sanitised(project_id, expected):
    assert NLPService.collection_name(project_id) == expected


def test_collection_names_are_stable():
    assert NLPService.collection_name("x") == NLPService.collection_name("x")


class _Chunk:
    def __init__(self, asset_id=None, chunk_order=None, id="oid"):
        self.asset_id = asset_id
        self.chunk_order = chunk_order
        self.id = id


def test_point_key_pairs_the_asset_and_the_order():
    """Stable ids mean re-indexing overwrites in place instead of duplicating."""
    assert NLPService._point_key(_Chunk("a1", 3)) == "a1:3"


def test_point_key_falls_back_to_the_document_id():
    assert NLPService._point_key(_Chunk(id="507f1f77bcf86cd799439011")) == \
           "507f1f77bcf86cd799439011"
