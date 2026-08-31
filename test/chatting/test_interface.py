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

    assert client.base_url == settings.ollama_base_url


# --- nvidia, the OpenAI-compatible one ----------------------------------------
#
# NVIDIA NIM is the OpenAI provider class pointed at another endpoint, so what
# is worth testing is the wiring: the right key, the right URL, and no leakage
# between the two vendors that now share one code path.


def _endpoint(client) -> str:
    return str(client.client.base_url).rstrip("/")


def test_factory_builds_nvidia_at_the_configured_endpoint(settings):
    """The URL comes from Settings, not from the provider class."""
    client = LLMChattingFactory(
        settings.model_copy(update={"NVIDIA_API_KEY": "nvapi-test"})
    ).create(provider="nvidia")

    assert type(client).__name__ == "NvidiaChatProvider"
    assert client.api_key == "nvapi-test"
    assert _endpoint(client) == settings.NVIDIA_API_BASE_URL


def test_the_shipped_nvidia_endpoint_is_nvidias(settings):
    """The one place the URL is written down. A .env may override it; with
    nothing set, the app must still reach NVIDIA rather than api.openai.com."""
    assert settings.NVIDIA_API_BASE_URL == "https://integrate.api.nvidia.com/v1"


def test_a_blank_nvidia_endpoint_falls_back_to_the_default(monkeypatch):
    """`NVIDIA_API_BASE_URL = ""` reads as unset — an OpenAI client with an
    nvapi key and no endpoint would dial OpenAI and fail with an
    authentication error that says nothing about the real mistake."""
    from utils import get_settings

    monkeypatch.setenv("NVIDIA_API_BASE_URL", "")
    get_settings.cache_clear()

    assert get_settings().NVIDIA_API_BASE_URL == "https://integrate.api.nvidia.com/v1"


def test_nvidia_base_url_can_be_overridden(settings):
    """A self-hosted NIM, or a gateway in front of one."""
    client = LLMChattingFactory(
        settings.model_copy(
            update={
                "NVIDIA_API_KEY": "nvapi-test",
                "NVIDIA_API_BASE_URL": "http://nim.internal:8000/v1",
            }
        )
    ).create(provider="nvidia")

    assert _endpoint(client) == "http://nim.internal:8000/v1"


def test_factory_rejects_nvidia_with_no_key(settings):
    with pytest.raises(UnsupportedProviderError):
        LLMChattingFactory(settings.model_copy(update={"NVIDIA_API_KEY": ""})).create(
            provider="nvidia"
        )


def test_nvidias_endpoint_does_not_leak_into_openai(settings):
    """The two share a class and a base-url table; they must not share a URL."""
    client = LLMChattingFactory(
        settings.model_copy(
            update={
                "OPENAI_API_KEY": "sk-test",
                "NVIDIA_API_BASE_URL": "http://nim.internal:8000/v1",
            }
        )
    ).create(provider="openai")

    assert "nvidia" not in _endpoint(client)
    assert "nim.internal" not in _endpoint(client)


def test_nvidia_errors_name_nvidia_not_openai(settings):
    """`OpenAI generation failed` for a call to NVIDIA sends debugging the
    wrong way; the vendor label is what makes the message honest."""
    client = LLMChattingFactory(
        settings.model_copy(update={"NVIDIA_API_KEY": "nvapi-test"})
    ).create(provider="nvidia")

    assert client._VENDOR == "NVIDIA"
