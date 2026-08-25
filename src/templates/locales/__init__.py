"""Prompt locales. One subdirectory per member of the Language enum."""

from enums import Language

# Derived rather than written out, so adding a Language member is the only edit
# needed. Kept as a tuple of plain strings because every caller tests
# membership against a string off a request or a .env value.
SUPPORTED_LANGS = tuple(lang.value for lang in Language)

__all__ = ["SUPPORTED_LANGS", "Language"]
