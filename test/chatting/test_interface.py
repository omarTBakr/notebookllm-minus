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
    history = [
        LLMChattingInterface.construct_message(ChatRole.USER, "earlier"),
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "a reply"),
    ]

    built = client._build_messages("now", history)

    assert [m["content"] for m in built] == ["earlier", "a reply", "now"]


def test_an_unanswered_question_is_joined_to_the_next_one():
    """Two user turns in a row is what a failed or abandoned stream leaves
    behind — the question was stored, the answer never was. It used to go out
    as-is, which NVIDIA and Anthropic both reject, so one failed answer broke
    every later message in that chat."""
    client = FakeChatClient()
    history = [LLMChattingInterface.construct_message(ChatRole.USER, "earlier")]

    built = client._build_messages("now", history)

    assert [m["role"] for m in built] == [ChatRole.USER.value]
    assert built[0]["content"] == "earlier\n\nnow"


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


# --- strict alternation -------------------------------------------------------
#
# "Conversation roles must alternate user/assistant/user/assistant/..." — a 400
# from NVIDIA, and the same requirement Anthropic has. Ollama accepts anything,
# which is why this went unnoticed for so long.


def _turns(built):
    return [(m["role"], m["content"]) for m in built]


def test_a_history_opening_on_a_reply_drops_it():
    """History is the last CHAT_HISTORY_LIMIT messages, so the window can begin
    mid-exchange — leading with an answer to a question the model cannot see."""
    client = FakeChatClient()
    history = [
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "answer to something older"),
        LLMChattingInterface.construct_message(ChatRole.USER, "q1"),
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "a1"),
    ]

    built = client._build_messages("now", history)

    assert _turns(built) == [
        (ChatRole.USER.value, "q1"),
        (ChatRole.ASSISTANT.value, "a1"),
        (ChatRole.USER.value, "now"),
    ]


def test_the_system_turn_stays_at_the_front():
    client = FakeChatClient()
    history = [
        LLMChattingInterface.construct_message(ChatRole.SYSTEM, "be terse"),
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "stray"),
    ]

    built = client._build_messages("now", history)

    assert _turns(built) == [
        (ChatRole.SYSTEM.value, "be terse"),
        (ChatRole.USER.value, "now"),
    ]


def test_consecutive_assistant_turns_are_joined():
    client = FakeChatClient()
    history = [
        LLMChattingInterface.construct_message(ChatRole.USER, "q1"),
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "part one"),
        LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "part two"),
    ]

    built = client._build_messages("now", history)

    assert _turns(built) == [
        (ChatRole.USER.value, "q1"),
        (ChatRole.ASSISTANT.value, "part one\n\npart two"),
        (ChatRole.USER.value, "now"),
    ]


def test_everything_built_alternates(  ):
    """The invariant itself, over the shapes this application actually makes."""
    client = FakeChatClient()

    histories = [
        [],
        [LLMChattingInterface.construct_message(ChatRole.USER, "q")],
        [LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "a")],
        [
            LLMChattingInterface.construct_message(ChatRole.SYSTEM, "s"),
            LLMChattingInterface.construct_message(ChatRole.ASSISTANT, "a"),
            LLMChattingInterface.construct_message(ChatRole.USER, "q1"),
            LLMChattingInterface.construct_message(ChatRole.USER, "q2"),
        ],
    ]

    for history in histories:
        roles = [m["role"] for m in client._build_messages("now", history)
                 if m["role"] != ChatRole.SYSTEM.value]

        assert roles[0] == ChatRole.USER.value
        assert roles[-1] == ChatRole.USER.value
        assert all(a != b for a, b in zip(roles, roles[1:])), roles


# --- streaming over the OpenAI wire format ------------------------------------
#
# OpenAIChatProvider used to inherit the interface's fallback — generate the
# whole answer, yield it once — so the UI sat empty for the length of a reply
# and a reasoning model's scratchpad was never shown, a finished answer having
# none left to show. NVIDIA's reasoning NIMs fill `reasoning_content` beside
# `content` in the same stream.


class _Delta:
    def __init__(self, content=None, reasoning_content=None):
        self.content = content
        self.reasoning_content = reasoning_content


class _Chunk:
    def __init__(self, content=None, reasoning=None, usage=None):
        self.choices = (
            [] if content is None and reasoning is None and usage
            else [type("Choice", (), {"delta": _Delta(content, reasoning)})()]
        )
        self.usage = usage


class _Stream:
    """What the SDK returns for stream=True: awaitable, then async-iterable."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def _openai_client(chunks):
    from factories.llmchatting import OpenAIChatProvider

    client = OpenAIChatProvider(api_key="sk-test", model_id="m", base_url="http://x.invalid/v1")
    stream = _Stream(chunks)

    def create(**kwargs):
        stream.kwargs = kwargs
        return stream

    client.client.chat.completions.create = create

    return client, stream


async def _collect(client, **kwargs):
    return [piece async for piece in client.stream_text("q", **kwargs)]


async def test_content_deltas_stream_as_content():
    client, _ = _openai_client([_Chunk(content="Hel"), _Chunk(content="lo")])

    pieces = await _collect(client)

    assert pieces == [
        {"kind": "content", "text": "Hel"},
        {"kind": "content", "text": "lo"},
    ]


async def test_reasoning_deltas_stream_as_thinking():
    """The scratchpad arrives on its own field, so nothing has to parse tags
    out of the answer text."""
    client, _ = _openai_client([
        _Chunk(reasoning="let me think"),
        _Chunk(reasoning=" a moment"),
        _Chunk(content="391"),
    ])

    pieces = await _collect(client)

    assert [p["kind"] for p in pieces] == ["thinking", "thinking", "content"]
    assert "".join(p["text"] for p in pieces if p["kind"] == "content") == "391"


async def test_empty_deltas_are_skipped():
    """The first frame carries only the role and the last only a finish
    reason; forwarding those would put empty strings in the answer."""
    client, _ = _openai_client([_Chunk(content=None), _Chunk(content="x"), _Chunk(content="")])

    assert await _collect(client) == [{"kind": "content", "text": "x"}]


async def test_the_request_asks_for_a_stream_and_its_usage():
    client, stream = _openai_client([_Chunk(content="x")])

    await _collect(client, max_tokens=64, temperature=0.2)

    assert stream.kwargs["stream"] is True
    assert stream.kwargs["stream_options"] == {"include_usage": True}
    assert stream.kwargs["max_completion_tokens"] == 64


async def test_a_usage_only_frame_does_not_become_a_piece():
    """It arrives after the last choice, with an empty choices list."""
    usage = type("Usage", (), {"prompt_tokens": 11, "completion_tokens": 3})()
    client, _ = _openai_client([_Chunk(content="x"), _Chunk(usage=usage)])

    assert await _collect(client) == [{"kind": "content", "text": "x"}]


async def test_a_mid_stream_failure_is_a_provider_error():
    """Raised once the caller is already consuming — it still has to be the
    type a non-streamed failure produces, or the route's error frame would
    differ by transport."""
    class _Boom(_Stream):
        async def __aiter__(self):
            yield _Chunk(content="partial")
            raise RuntimeError("connection reset")

    from factories.llmchatting import OpenAIChatProvider

    client = OpenAIChatProvider(api_key="sk-test", model_id="m", base_url="http://x.invalid/v1")
    client.client.chat.completions.create = lambda **kw: _Boom([])

    with pytest.raises(LLMProviderError, match="streaming failed"):
        await _collect(client)
