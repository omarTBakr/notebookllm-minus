"""Grouping mind-map topics into branches, without a model."""

import pytest
from pydantic import ValidationError

from controllers.MindMapController import group_into_branches
from models.db_schema import MindMapNodeSet, MindMapOutline


def _items(n):
    return [{"topic": f"t{i}", "detail": "d", "chunk_order": i, "asset_id": "a"} for i in range(1, n + 1)]


def test_each_topic_gets_the_branch_that_named_it():
    outline = MindMapOutline(branches=[{"title": "A", "members": [1, 3]}, {"title": "B", "members": [2]}])

    grouped = group_into_branches(_items(3), outline, other="Other")

    assert [(i["topic"], i["branch"]) for i in grouped] == [("t1", "A"), ("t3", "A"), ("t2", "B")]


def test_unclaimed_topics_go_to_other_and_nothing_is_dropped():
    outline = MindMapOutline(branches=[{"title": "A", "members": [2]}])

    grouped = group_into_branches(_items(3), outline, other="Other")

    assert len(grouped) == 3
    assert [i["branch"] for i in grouped] == ["A", "Other", "Other"]


def test_a_topic_named_twice_stays_in_its_first_branch_and_bad_numbers_are_ignored():
    outline = MindMapOutline(branches=[{"title": "A", "members": [1, 0, 99]}, {"title": "B", "members": [1, 2]}])

    grouped = group_into_branches(_items(2), outline, other="Other")

    assert [(i["topic"], i["branch"]) for i in grouped] == [("t1", "A"), ("t2", "B")]


def test_grouping_does_not_mutate_the_input():
    items = _items(1)

    group_into_branches(items, MindMapOutline(branches=[{"title": "A", "members": [1]}]), other="Other")

    assert "branch" not in items[0]


def test_branch_titles_must_be_distinct():
    with pytest.raises(ValidationError):
        MindMapOutline(branches=[{"title": "Same", "members": [1]}, {"title": " same ", "members": [2]}])


def test_the_node_list_is_required_so_an_echoed_schema_is_retried():
    with pytest.raises(ValidationError):
        MindMapNodeSet.model_validate({})
