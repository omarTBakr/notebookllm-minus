"""Langfuse tracing and prompt versioning, against a recording fake client."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from application.prompts.template_parser import (
    TemplateParser,
    iter_local_prompts,
    langfuse_name,
)
from shared.utils import tracing
from tests.support.llm import FakeChatClient, FakeStreamingChatClient


class FakeObservation:
    def __init__(self, log, name, **attrs):
        self.log, self.name, self.attrs = log, name, dict(attrs)
        self.ended = False

    def update(self, **attrs):
        self.attrs.update(attrs)
        return self

    def end(self, **_):
        self.ended = True


class FakeLangfuse:
    """Records every observation and serves prompts from a dict."""

    def __init__(self, prompts=None):
        self.observations: list[FakeObservation] = []
        self.prompts = prompts or {}
        self.prompt_requests: list[tuple] = []

    def start_observation(self, *, name, as_type="span", **attrs):
        obs = FakeObservation(self, name, as_type=as_type, **attrs)
        self.observations.append(obs)
        return obs

    def start_as_current_observation(self, *, name, as_type="span", **attrs):
        client = self

        @contextmanager
        def cm():
            obs = client.start_observation(name=name, as_type=as_type, **attrs)
            yield obs
            obs.ended = True

        return cm()

    def get_prompt(self, name, *, label=None, fallback=None, **_):
        self.prompt_requests.append((name, label))
        if name in self.prompts:
            return SimpleNamespace(prompt=self.prompts[name], version=7, is_fallback=False)
        return SimpleNamespace(prompt=fallback, version=None, is_fallback=True)

    def create_event(self, **attrs):
        self.observations.append(FakeObservation(self, attrs.pop("name"), as_type="event", **attrs))

    def get_current_trace_id(self):
        return "trace-1"

    def get_current_observation_id(self):
        return "span-9"

    def flush(self):
        pass


@pytest.fixture
def langfuse(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_client", fake)
    monkeypatch.setattr(tracing, "_client_resolved", True)
    return fake


# --- off by default -----------------------------------------------------------


def test_tracing_is_off_without_keys():
    assert tracing.get_langfuse() is None
    with tracing.observation("anything") as obs:
        obs.update(output="ignored")
    assert obs is tracing.NOOP
    assert tracing.current_trace_headers() == {}


def test_tracing_stays_off_with_enabled_but_no_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    assert tracing.get_langfuse() is None


async def test_generate_text_works_untraced():
    assert await FakeChatClient(reply="hi").generate_text("q") == "hi"


# --- generations --------------------------------------------------------------


async def test_generate_text_records_a_generation_with_usage(langfuse):
    client = FakeChatClient(reply="the answer", model_id="m1")

    original = client._generate_text

    async def with_usage(messages, max_tokens, temperature):
        client._log_usage(11, 3)
        return await original(messages, max_tokens, temperature)

    client._generate_text = with_usage

    await client.generate_text("question?")

    (gen,) = langfuse.observations
    assert gen.attrs["as_type"] == "generation"
    assert gen.attrs["model"] == "m1"
    assert gen.attrs["output"] == "the answer"
    assert gen.attrs["usage_details"] == {"input": 11, "output": 3}
    assert gen.attrs["input"][-1]["content"] == "question?"
    assert gen.ended


async def test_stream_text_records_only_the_answer_text(langfuse):
    client = FakeStreamingChatClient(reply="one two")

    pieces = [p async for p in client.stream_text("q")]

    assert [p["kind"] for p in pieces][0] == "thinking"
    (gen,) = langfuse.observations
    assert gen.attrs["output"] == "one two "
    assert gen.attrs["metadata"]["thinking_chars"] == 3
    assert "completion_start_time" in gen.attrs
    assert gen.ended


async def test_failed_generation_is_ended_as_error(langfuse):
    from tests.support.llm import FailingChatClient

    with pytest.raises(RuntimeError):
        await FailingChatClient(RuntimeError("boom")).generate_text("q")

    (gen,) = langfuse.observations
    assert gen.attrs["level"] == "ERROR"
    assert gen.ended


async def test_linked_prompt_is_attached_to_the_generation(langfuse):
    ref = object()
    with tracing.linked_prompt(ref):
        await FakeChatClient().generate_text("q")
    await FakeChatClient().generate_text("q")

    first, second = langfuse.observations
    assert first.attrs["prompt"] is ref
    assert "prompt" not in second.attrs


def test_observation_marks_errors_and_reraises(langfuse):
    with pytest.raises(ValueError):
        with tracing.observation("step"):
            raise ValueError("nope")

    (obs,) = langfuse.observations
    assert obs.attrs["level"] == "ERROR"


def test_no_headers_outside_a_trace(langfuse):
    """Queued with no active span: nothing to propagate, and no SDK call
    (which would log a "No active span" warning per task)."""
    called = []
    langfuse.get_current_trace_id = lambda: called.append(1) or "trace-1"

    assert tracing.current_trace_headers() == {}
    assert called == []


def test_trace_headers_round_trip(langfuse, monkeypatch):
    from opentelemetry import trace as otel_trace

    active = otel_trace.NonRecordingSpan(
        otel_trace.SpanContext(trace_id=1, span_id=2, is_remote=False, trace_flags=otel_trace.TraceFlags(1))
    )
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: active)
    headers = tracing.current_trace_headers()
    assert headers == {
        tracing.TRACE_ID_HEADER: "trace-1",
        tracing.PARENT_SPAN_HEADER: "span-9",
    }
    assert tracing.trace_context_from(headers) == {
        "trace_id": "trace-1",
        "parent_span_id": "span-9",
    }
    assert tracing.trace_context_from({}) is None


# --- prompt versioning ----------------------------------------------------------


def test_prompts_come_from_local_files_when_tracing_is_off():
    text, ref = TemplateParser("en").get_with_ref("rag", "footer_prompt", {"question": "Q?"})
    assert "Q?" in text
    assert ref is None


def test_langfuse_version_wins_and_is_returned_as_ref(langfuse):
    langfuse.prompts[langfuse_name("en", "rag", "footer_prompt")] = "Remote: {question}"

    text, ref = TemplateParser("en").get_with_ref("rag", "footer_prompt", {"question": "Q?"})

    assert text == "Remote: Q?"
    assert ref.version == 7
    assert langfuse.prompt_requests == [("en/rag.footer_prompt", "production")]


def test_missing_remote_prompt_falls_back_to_local(langfuse):
    text, ref = TemplateParser("ar").get_with_ref("rag", "footer_prompt", {"question": "س"})
    assert "س" in text
    assert ref is None


def test_broken_remote_placeholder_falls_back_to_local(langfuse):
    langfuse.prompts["en/rag.footer_prompt"] = "Remote: {not_a_var}"

    text, ref = TemplateParser("en").get_with_ref("rag", "footer_prompt", {"question": "Q?"})

    assert "Remote" not in text and "Q?" in text
    assert ref is None


def test_prompts_switch_off_independently(langfuse, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PROMPTS_ENABLED", "false")
    langfuse.prompts["en/rag.footer_prompt"] = "Remote: {question}"

    text, _ = TemplateParser("en").get_with_ref("rag", "footer_prompt", {"question": "Q?"})

    assert "Remote" not in text
    assert langfuse.prompt_requests == []


def test_every_locale_prompt_is_seedable():
    seen = {(lang, group, key) for lang, group, key, _ in iter_local_prompts()}
    assert ("en", "rag", "system_prompt") in seen
    assert ("ar", "studio", "quiz_prompt") in seen
    # Locales must stay in step: every key exists in both languages.
    by_lang = {}
    for lang, group, key in seen:
        by_lang.setdefault(lang, set()).add((group, key))
    assert by_lang["en"] == by_lang["ar"]
