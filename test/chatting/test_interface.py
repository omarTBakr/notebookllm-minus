"""The concrete helpers on LLMChattingInterface, exercised through a fake."""

import pytest

from enums import ChatRole
from exceptions import LLMProviderError, UnsupportedProviderError
from factories.llmchatting import LLMChattingInterface
from factories.llmchatting.LLMChattingFactory import LLMChattingFactory, _thinking_flag
from test.fakes.llm import FailingChatClient, FakeChatClient, FakeStreamingChatClient


# --- message construction -----------------------------------------------------


def test_construct_message_accepts_an_enum_and_a_string():
    assert LLMChattingInterface.construct_message(ChatRole.USER, "hi") == \
           LLMChattingInterface.construct_message("user", "hi")


def test_construct_message_rejects_an_unknown_role():
    with pytest.raises(ValueError):
        LLMChattingInterface.construct_message("wizard", "hi")


def test_split_system_separates_the_system_turn():
    messages = [
        LLMChattingInterface.construct_message(ChatRole.SYSTEM, "be terse"),
        LLMChattingInterface.construct_message(ChatRole.USER, "hello"),
    ]

    system, rest = LLMChattingInterface._split_system(messages)

    assert system == "be terse"
    assert [m["role"] for m in rest] == [ChatRole.USER.value]


def test_split_system_joins_several_system_turns():
    messages = [
        LLMChattingInterface.construct_message(ChatRole.SYSTEM, "one"),
        LLMChattingInterface.construct_message(ChatRole.SYSTEM, "two"),
    ]

    system, rest = LLMChattingInterface._split_system(messages)

    assert system == "one\n\ntwo"
    assert rest == []


def test_split_system_returns_none_when_there_is_no_system_turn():
    messages = [LLMChattingInterface.construct_message(ChatRole.USER, "hello")]

    assert LLMChattingInterface._split_system(messages)[0] is None


def test_build_messages_appends_the_prompt_to_the_history():
    client = FakeChatClient()
    history = [LLMChattingInterface.construct_message(ChatRole.USER, "earlier")]

    built = client._build_messages("now", history)

    assert [m["content"] for m in built] == ["earlier", "now"]


# --- generate and stream ------------------------------------------------------


async def test_generate_text_returns_the_reply():
    assert await FakeChatClient(reply="hello").generate_text("q") == "hello"


async def test_generate_text_passes_the_overrides_through():
    client = FakeChatClient()

    await client.generate_text("q", max_tokens=99, temperature=1.5)

    assert client.calls[0]["max_tokens"] == 99
    assert client.calls[0]["temperature"] == 1.5


async def test_generate_text_defaults_come_from_the_constructor():
    client = FakeChatClient(default_max_tokens=7, default_temperature=0.9)

    await client.generate_text("q")

    assert client.calls[0]["max_tokens"] == 7
    assert client.calls[0]["temperature"] == 0.9


async def test_stream_text_falls_back_to_one_chunk():
    """A provider that implements only _generate_text still streams."""
    chunks = [c async for c in FakeChatClient(reply="whole answer").stream_text("q")]

    assert [c["kind"] for c in chunks] == ["content"]
    assert "".join(c["text"] for c in chunks) == "whole answer"


async def test_stream_text_yields_thinking_then_content():
    chunks = [c async for c in FakeStreamingChatClient(reply="a b").stream_text("q")]

    assert chunks[0]["kind"] == "thinking"
    assert "".join(c["text"] for c in chunks if c["kind"] == "content").strip() == "a b"


async def test_a_provider_failure_propagates():
    client = FailingChatClient(LLMProviderError("upstream is down"))

    with pytest.raises(LLMProviderError):
        await client.generate_text("q")


# --- the factory --------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("True", True), ("false", False),
    ("low", "low"), ("medium", "medium"), ("high", "high"),
])
def test_thinking_flag(value, expected):
    assert _thinking_flag(value) == expected


def test_factory_rejects_an_unknown_backend(settings):
    with pytest.raises(UnsupportedProviderError):
        LLMChattingFactory(settings).create(provider="nosuchvendor")


def test_factory_rejects_a_backend_with_no_key(settings):
    """Everything except Ollama needs a key, and a blank one cannot be built."""
    with pytest.raises(UnsupportedProviderError):
        LLMChattingFactory(settings.model_copy(update={"ANTHROPIC_API_KEY": ""})).create(
            provider="anthropic"
        )


def test_factory_builds_ollama_without_a_key(settings):
    client = LLMChattingFactory(settings).create(provider="ollama")

    assert client.base_url == settings.OLLAMA_BASE_URL
