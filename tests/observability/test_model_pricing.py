"""Token cost per model, and its trip to Langfuse."""

from types import SimpleNamespace

import pytest

from shared.utils import tracing
from shared.utils.model_pricing import backend_of, cost_details, price_for, price_table
from tests.observability.test_tracing import FakeLangfuse
from tests.support.llm import FakeChatClient


def test_backend_comes_from_the_provider_class():
    assert backend_of("OllamaChatProvider") == "ollama"
    assert backend_of("OpenRouterChatProvider") == "openrouter"
    assert backend_of("NvidiaChatProvider") == "nvidia"


def test_local_models_cost_nothing_by_default():
    assert cost_details("ollama", "llama3.1:8b", 942, 54) == {"input": 0.0, "output": 0.0, "total": 0.0}


def test_unpriced_models_are_left_to_langfuse():
    assert cost_details("anthropic", "claude-sonnet-4-5", 1000, 100) is None


def test_cost_is_tokens_times_price_per_million():
    prices = {"nvidia/meta/*": {"input": 2.0, "output": 10.0}}
    cost = cost_details("nvidia", "meta/llama-3.1-8b-instruct", 1_000, 500, prices)
    assert cost["input"] == pytest.approx(0.002)
    assert cost["output"] == pytest.approx(0.005)
    assert cost["total"] == pytest.approx(0.007)


def test_exact_key_beats_pattern_and_longer_pattern_beats_shorter():
    table = price_table(
        {
            "openrouter/*": {"input": 1, "output": 1},
            "openrouter/qwen/*": {"input": 2, "output": 2},
            "openrouter/qwen/qwen3-8b": {"input": 3, "output": 3},
        }
    )
    assert price_for("openrouter", "qwen/qwen3-8b", table)["input"] == 3
    assert price_for("openrouter", "qwen/other", table)["input"] == 2
    assert price_for("openrouter", "meta/x", table)["input"] == 1


def test_overrides_can_price_local_models():
    cost = cost_details("ollama", "llama3.1:8b", 1_000_000, 0, {"ollama/llama3.1:*": {"input": 0.5, "output": 1}})
    assert cost["total"] == pytest.approx(0.5)


def test_no_tokens_means_no_cost():
    assert cost_details("ollama", "x", None, None) is None


# --- into Langfuse -------------------------------------------------------------------


@pytest.fixture
def langfuse(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_client", fake)
    monkeypatch.setattr(tracing, "_client_resolved", True)
    return fake


class PricedClient(FakeChatClient):
    """Reports usage the way a real provider does."""

    async def _generate_text(self, messages, max_tokens, temperature):
        self._log_usage(1_000, 200)
        return "ok"


async def test_a_priced_model_sends_cost_details(langfuse, monkeypatch):
    monkeypatch.setenv("MODEL_PRICES", '{"pricedclient/*": {"input": 3.0, "output": 15.0}}')

    await PricedClient(model_id="m").generate_text("q")

    (gen,) = langfuse.observations
    assert gen.attrs["usage_details"] == {"input": 1000, "output": 200}
    assert gen.attrs["cost_details"]["input"] == pytest.approx(0.003)
    assert gen.attrs["cost_details"]["output"] == pytest.approx(0.003)
    assert gen.attrs["cost_details"]["total"] == pytest.approx(0.006)


async def test_an_unpriced_model_sends_only_tokens(langfuse):
    await PricedClient(model_id="m").generate_text("q")

    (gen,) = langfuse.observations
    assert gen.attrs["usage_details"] == {"input": 1000, "output": 200}
    assert "cost_details" not in gen.attrs


async def test_ollama_streaming_reports_its_tokens(langfuse):
    from application.providers.chatting import OllamaChatProvider

    provider = OllamaChatProvider.__new__(OllamaChatProvider)
    provider.model_id, provider.thinking, provider.base_url = "llama3.1:8b", None, "http://ollama"
    provider.default_max_tokens, provider.default_temperature = 64, 0.1
    provider.logger = __import__("logging").getLogger("test")
    provider._options = lambda max_tokens, temperature: {}

    def part(text, done=False, **counts):
        return SimpleNamespace(message=SimpleNamespace(content=text, thinking=None), done=done, **counts)

    async def fake_stream(messages, options, think):
        async def gen():
            yield part("Hello ")
            yield part("there")
            yield part("", done=True, prompt_eval_count=120, eval_count=7)

        return gen()

    provider._open_stream = fake_stream

    pieces = [p async for p in provider.stream_text("hi")]

    assert "".join(p["text"] for p in pieces) == "Hello there"
    (gen,) = langfuse.observations
    assert gen.attrs["usage_details"] == {"input": 120, "output": 7}
    assert gen.attrs["cost_details"]["total"] == 0.0  # local model
