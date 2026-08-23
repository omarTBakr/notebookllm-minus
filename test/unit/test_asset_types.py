"""MIME type to AssetType."""

import pytest

from enums import AssetType


@pytest.mark.parametrize("content_type, expected", [
    ("text/plain", AssetType.TEXT),
    ("application/pdf", AssetType.PDF),
])
def test_known_types_map(content_type, expected):
    assert AssetType.from_content_type(content_type) is expected


@pytest.mark.parametrize("content_type", [None, "", "application/x-made-up"])
def test_anything_else_is_other(content_type):
    assert AssetType.from_content_type(content_type) is AssetType.OTHER


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN BUG: the lookup compares the raw header, so a media type "
           "carrying a parameter misses. DataController.validate_file has the "
           "same flaw and would reject the upload outright. Both should split "
           "on ';' before matching. Remove this marker with the fix.",
)
def test_a_charset_suffix_does_not_defeat_the_lookup():
    """`text/plain; charset=utf-8` is a legal, common upload header."""
    assert AssetType.from_content_type("text/plain; charset=utf-8") is AssetType.TEXT
