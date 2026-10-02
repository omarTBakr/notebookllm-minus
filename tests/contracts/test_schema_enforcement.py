"""Handing the model its schema, not just describing it in the prompt.

A prompt that says "return JSON like this, at most three items" is a request. A
provider that can constrain decoding to a JSON Schema turns it into a rule: the
answer cannot leave the shape, and a list with a `maxItems` cannot ramble. These
tests cover the plumbing -- who is handed the schema, what is in it, what a
provider does when an endpoint refuses it -- not whether any particular model
obeys, which only a live call can show.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from application.providers.chatting import LLMChattingFactory, OllamaChatProvider
from application.services.llm.structured_generation import (
    generate_structured,
    schema_spec,
)
from shared.exceptions import StructuredOutputError


class Card(BaseModel):
    front: str
    back: str


class Deck(BaseModel):
    model_config = {"json_schema_extra": {"example": {"cards": [{"front": "F", "back": "B"}]}}}
    cards: list[Card] = Field(...)


class Title(BaseModel):
    text: str


GOOD = '{"cards": [{"front": "a", "back": "b"}]}'


class Spy:
    """A client that records whether it was handed a schema."""

    def __init__(self, enforces: bool, reply: str = GOOD, delay: float = 0.0):
        self.ENFORCES_SCHEMA = enforces
        self.reply, self.delay = reply, delay
        self.kwargs: list[dict] = []

    async def generate_text(self, **kwargs):
        self.kwargs.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


# --- the schema that is sent --------------------------------------------------


def test_the_spec_is_the_models_own_schema_without_the_prompt_example():
    spec = schema_spec(Deck)

    assert spec["required"] == ["cards"]
    assert "example" not in spec


def test_max_items_caps_the_one_list_without_touching_the_validating_model():
    spec = schema_spec(Deck, max_items=3)

    assert spec["properties"]["cards"]["maxItems"] == 3
    # The model itself still accepts more: a provider that cannot constrain
    # produces an overlong answer the caller truncates, rather than one it rejects.
    assert len(Deck.model_validate({"cards": [{"front": "f", "back": "b"}] * 9}).cards) == 9


def test_max_items_is_ignored_for_a_schema_that_is_not_a_list_of_items():
    assert "maxItems" not in str(schema_spec(Title, max_items=3))


# --- who is handed it ---------------------------------------------------------


async def test_a_provider_that_enforces_is_handed_the_schema():
    client = Spy(enforces=True)

    await generate_structured(client, "make a deck", Deck, max_items=3)

    spec = client.kwargs[0]["json_schema"]
    assert spec["properties"]["cards"]["maxItems"] == 3


async def test_a_provider_that_does_not_keeps_the_call_it_always_had():
    client = Spy(enforces=False)

    await generate_structured(client, "make a deck", Deck, max_items=3)

    assert "json_schema" not in client.kwargs[0]


async def test_the_schema_is_still_in_the_prompt_for_the_models_that_ignore_the_constraint():
    client = Spy(enforces=True)

    await generate_structured(client, "make a deck", Deck)

    assert "cards" in client.kwargs[0]["prompt"]


# --- a call that takes too long -----------------------------------------------


async def test_a_slow_attempt_is_a_failed_attempt_not_a_hang():
    client = Spy(enforces=False, delay=5)

    with pytest.raises(StructuredOutputError, match="no answer within"):
        await generate_structured(client, "make a deck", Deck, retries=1, attempt_timeout=0.05)

    assert len(client.kwargs) == 2  # asked again, then gave up


async def test_a_slow_attempt_is_retried():
    class SlowThenFast(Spy):
        async def generate_text(self, **kwargs):
            self.kwargs.append(kwargs)
            if len(self.kwargs) == 1:
                await asyncio.sleep(5)
            return GOOD

    client = SlowThenFast(enforces=False)

    deck = await generate_structured(client, "make a deck", Deck, attempt_timeout=0.05)

    assert len(deck.cards) == 1 and len(client.kwargs) == 2


# --- the providers ------------------------------------------------------------


def _openai(settings, **update):
    return LLMChattingFactory(settings.model_copy(update={"OPENAI_API_KEY": "sk-test", **update})).create(
        provider="openai"
    )


class _Completions:
    """Stands in for client.chat.completions; refuses response_format on demand."""

    def __init__(self, reject=False):
        self.reject, self.calls = reject, []

    async def create(self, **kwargs):
        from openai import BadRequestError

        self.calls.append(kwargs)
        if self.reject and "response_format" in kwargs:
            import httpx

            response = httpx.Response(400, request=httpx.Request("POST", "http://x"))
            raise BadRequestError("response_format is not supported", response=response, body=None)
        message = SimpleNamespace(content=GOOD)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _install(provider, completions):
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))


async def test_openai_compatible_providers_send_a_json_schema_response_format(settings):
    provider = _openai(settings)
    completions = _Completions()
    _install(provider, completions)

    await provider.generate_text("hi", json_schema=schema_spec(Deck))

    sent = completions.calls[0]["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["schema"]["required"] == ["cards"]


async def test_no_schema_means_no_response_format(settings):
    provider = _openai(settings)
    completions = _Completions()
    _install(provider, completions)

    await provider.generate_text("hi")

    assert "response_format" not in completions.calls[0]


async def test_an_endpoint_that_rejects_response_format_is_asked_again_without_it(settings):
    provider = _openai(settings)
    completions = _Completions(reject=True)
    _install(provider, completions)

    assert await provider.generate_text("hi", json_schema=schema_spec(Deck)) == GOOD

    assert ["response_format" in call for call in completions.calls] == [True, False]


async def test_a_rejection_is_remembered_so_it_is_not_paid_for_twice(settings):
    provider = _openai(settings)
    completions = _Completions(reject=True)
    _install(provider, completions)

    await provider.generate_text("hi", json_schema=schema_spec(Deck))
    await provider.generate_text("hi", json_schema=schema_spec(Deck))

    assert ["response_format" in call for call in completions.calls] == [True, False, False]


def test_every_openai_compatible_vendor_enforces(settings):
    for provider, key in (
        ("openai", "OPENAI_API_KEY"),
        ("nvidia", "NVIDIA_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ):
        client = LLMChattingFactory(settings.model_copy(update={key: "k"})).create(provider=provider)
        assert client.ENFORCES_SCHEMA is True, provider


def test_providers_that_cannot_constrain_say_so(settings):
    for provider, key in (("anthropic", "ANTHROPIC_API_KEY"), ("cohere", "COHERE_API_KEY")):
        client = LLMChattingFactory(settings.model_copy(update={key: "k"})).create(provider=provider)
        assert client.ENFORCES_SCHEMA is False, provider


async def test_ollama_constrains_decoding_with_format():
    provider = OllamaChatProvider(model_id="m", base_url="http://localhost:11434")
    seen = {}

    class Chat:
        async def chat(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                message=SimpleNamespace(content=GOOD), done_reason="stop", prompt_eval_count=1, eval_count=1
            )

    provider.client = Chat()
    spec = schema_spec(Deck, max_items=3)

    await provider.generate_text("hi", json_schema=spec)
    assert seen["format"] == spec

    await provider.generate_text("hi")
    assert seen["format"] is None
