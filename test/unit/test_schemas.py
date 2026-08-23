"""Request-body validation, no app required."""

import pytest
from pydantic import ValidationError

from routes.schemas import (
    ChatSettingsRequest,
    CreateChatRequest,
    MessageRequest,
    RenameAssetRequest,
    RenameChatRequest,
    RenameUserRequest,
)
from routes.schemas.nlp_request import PushRequest, SearchRequest
from routes.schemas.process_request import ProcessRequest


@pytest.mark.parametrize("model, field", [
    (RenameUserRequest, "label"),
    (RenameChatRequest, "title"),
    (RenameAssetRequest, "name"),
])
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_rename_requests_reject_blank(model, field, value):
    with pytest.raises(ValidationError):
        model(**{field: value})


@pytest.mark.parametrize("model, field", [
    (RenameUserRequest, "label"),
    (RenameChatRequest, "title"),
    (RenameAssetRequest, "name"),
])
def test_rename_requests_strip_surrounding_space(model, field):
    assert getattr(model(**{field: "  Name  "}), field) == "Name"


def test_chat_language_must_be_supported():
    with pytest.raises(ValidationError):
        CreateChatRequest(title="t", lang="klingon")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_supported_languages_are_accepted(lang):
    assert CreateChatRequest(title="t", lang=lang).lang == lang


def test_overlap_may_not_reach_the_chunk_size():
    """An overlap at or above the chunk size makes the splitter loop."""
    with pytest.raises(ValidationError):
        ProcessRequest(chunk_size=100, overlap_size=100)


def test_overlap_below_the_chunk_size_is_fine():
    assert ProcessRequest(chunk_size=100, overlap_size=99).overlap_size == 99


def test_settings_request_applies_the_same_overlap_rule():
    with pytest.raises(ValidationError):
        ChatSettingsRequest(chunk_size=200, overlap_size=500)


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_message_top_k_is_bounded(top_k):
    with pytest.raises(ValidationError):
        MessageRequest(text="q", top_k=top_k)


def test_message_text_is_required():
    with pytest.raises(ValidationError):
        MessageRequest(text="")


@pytest.mark.parametrize("limit", [0, 101])
def test_search_limit_is_bounded(limit):
    with pytest.raises(ValidationError):
        SearchRequest(text="q", limit=limit)


@pytest.mark.parametrize("batch", [0, 513])
def test_push_batch_size_is_bounded(batch):
    with pytest.raises(ValidationError):
        PushRequest(batch_size=batch)
