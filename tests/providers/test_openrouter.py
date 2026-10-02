"""OpenRouter: wired like NVIDIA, described by its own listing.

Unlike NVIDIA, OpenRouter's ``/models`` says what each model reads and writes
and which parameters it takes, so nothing is probed for chat — these tests
check that the listing is read correctly, and that the provider speaks
OpenRouter's dialect rather than NIM's.
"""

import httpx
import pytest

from application.providers.chatting import LLMChattingFactory
from application.providers.embedding import LLMEmbeddingFactory
from application.services import ModelService
from application.services.llm import for_source
from application.services.llm.OpenRouterModelService import OpenRouterModelService
from shared.exceptions import UnsupportedProviderError
from shared.utils import OPENROUTER, default_chat_model, qualify, split_source


def _endpoint(client) -> str:
    return str(client.client.base_url).rstrip("/")


# --- ids ----------------------------------------------------------------------


def test_the_publisher_inside_an_openrouter_id_is_not_read_as_a_source():
    """`anthropic` is itself a source prefix. Only the first segment counts, so
    an OpenRouter id for an Anthropic model stays OpenRouter's."""
    assert split_source("openrouter/anthropic/claude-sonnet-4") == ("openrouter", "anthropic/claude-sonnet-4")
    assert qualify(OPENROUTER, "openai/gpt-4o") == "openrouter/openai/gpt-4o"


def test_a_configured_openrouter_backend_qualifies_its_model(settings):
    configured = settings.model_copy(
        update={"GENERATION_BACKEND": "openrouter", "GENERATION_MODEL_ID": "anthropic/claude-sonnet-4"}
    )

    assert default_chat_model(configured) == "openrouter/anthropic/claude-sonnet-4"


# --- settings and factories ---------------------------------------------------


def test_the_shipped_openrouter_endpoint_is_openrouters(settings):
    assert settings.OPENROUTER_API_BASE_URL == "https://openrouter.ai/api/v1"


def test_a_blank_openrouter_endpoint_falls_back_to_the_default(monkeypatch):
    from shared.utils import get_settings

    monkeypatch.setenv("OPENROUTER_API_BASE_URL", "")
    get_settings.cache_clear()

    assert get_settings().OPENROUTER_API_BASE_URL == "https://openrouter.ai/api/v1"


def test_factory_builds_openrouter_at_the_configured_endpoint(settings):
    client = LLMChattingFactory(settings.model_copy(update={"OPENROUTER_API_KEY": "sk-or-test"})).create(
        provider="openrouter"
    )

    assert type(client).__name__ == "OpenRouterChatProvider"
    assert client.api_key == "sk-or-test"
    assert _endpoint(client) == settings.OPENROUTER_API_BASE_URL
    assert client._VENDOR == "OpenRouter"


def test_factory_rejects_openrouter_with_no_key(settings):
    with pytest.raises(UnsupportedProviderError):
        LLMChattingFactory(settings.model_copy(update={"OPENROUTER_API_KEY": ""})).create(provider="openrouter")


def test_openrouters_endpoint_does_not_leak_into_openai(settings):
    client = LLMChattingFactory(
        settings.model_copy(update={"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_BASE_URL": "http://gw.internal/v1"})
    ).create(provider="openai")

    assert "gw.internal" not in _endpoint(client)


def test_openrouter_takes_max_tokens_not_max_completion_tokens(settings):
    client = LLMChattingFactory(settings.model_copy(update={"OPENROUTER_API_KEY": "sk-or-test"})).create(
        provider="openrouter"
    )

    assert client._MAX_TOKENS_FIELD == "max_tokens"


@pytest.mark.parametrize("thinking, expected", [(None, {}), (False, False), (True, True)])
def test_the_think_toggle_is_openrouters_reasoning_field(settings, thinking, expected):
    """NIM's chat_template_kwargs means nothing to OpenRouter."""
    client = LLMChattingFactory(
        settings.model_copy(update={"OPENROUTER_API_KEY": "sk-or-test", "CHAT_THINKING_OVERRIDE": thinking})
    ).create(provider="openrouter")

    body = client._extra_body()

    if thinking is None:
        assert body == {}
    else:
        assert body == {"extra_body": {"reasoning": {"enabled": expected}}}


def test_factory_builds_the_openrouter_embedder(settings):
    client = LLMEmbeddingFactory(settings.model_copy(update={"OPENROUTER_API_KEY": "sk-or-test"})).create(
        provider="openrouter"
    )

    assert type(client).__name__ == "OpenRouterEmbeddingProvider"
    assert _endpoint(client) == settings.OPENROUTER_API_BASE_URL


# --- discovery ----------------------------------------------------------------

_MODELS = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-4",
            "name": "Claude Sonnet 4",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "supported_parameters": ["tools", "reasoning", "max_tokens"],
        },
        {
            "id": "meta-llama/llama-3.3-70b-instruct:free",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["max_tokens"],
        },
        {
            # Answers a chat request with a picture this app has nowhere to put.
            "id": "google/imagen-x",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
        },
        {
            "id": "meta-llama/llama-guard-4-12b",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {"name": "no id at all"},
    ]
}

_EMBEDDERS = {"data": [{"id": "openai/text-embedding-3-small"}]}


class _Reply:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class _Http:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, headers=None):
        self.calls.append(("GET", url, headers))
        # Exact path: "/models" is a suffix of "/embeddings/models" too.
        return self.routes.get(url.removeprefix("https://or.invalid/api/v1"), _Reply({}, 404))

    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, json))
        return self.routes["POST"](json)


@pytest.fixture
def openrouter(monkeypatch, settings):
    def build(routes):
        http = _Http(routes)
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: http)
        monkeypatch.setattr(OpenRouterModelService, "_host_url", lambda self, source: "https://or.invalid/api/v1")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test")
        ModelService.forget_probes()
        return OpenRouterModelService(), http

    return build


async def test_the_listing_is_read_not_probed(openrouter):
    service, http = openrouter({"/models": _Reply(_MODELS), "/embeddings/models": _Reply(_EMBEDDERS)})

    models = {m["tag"]: m for m in await service.list_models()}

    assert set(models) == {
        "anthropic/claude-sonnet-4",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openai/text-embedding-3-small",
    }
    # No completion call was ever made to decide any of it.
    assert not [c for c in http.calls if c[0] == "POST"]


async def test_capabilities_come_from_the_listing(openrouter):
    service, _ = openrouter({"/models": _Reply(_MODELS), "/embeddings/models": _Reply(_EMBEDDERS)})

    models = {m["tag"]: m for m in await service.list_models()}

    assert models["anthropic/claude-sonnet-4"]["capabilities"] == ["completion", "vision", "tools", "thinking"]
    assert models["meta-llama/llama-3.3-70b-instruct:free"]["capabilities"] == ["completion"]
    assert models["openai/text-embedding-3-small"]["capabilities"] == ["embedding"]


async def test_ids_are_qualified_and_the_publisher_is_the_family(openrouter):
    service, _ = openrouter({"/models": _Reply(_MODELS), "/embeddings/models": _Reply(_EMBEDDERS)})

    models = {m["tag"]: m for m in await service.list_models()}
    sonnet = models["anthropic/claude-sonnet-4"]

    assert sonnet["id"] == "openrouter/anthropic/claude-sonnet-4"
    assert sonnet["source"] == OPENROUTER
    assert sonnet["family"] == "anthropic"
    assert sonnet["context_length"] == 200000


async def test_image_only_and_safety_models_are_left_out(openrouter):
    service, _ = openrouter({"/models": _Reply(_MODELS), "/embeddings/models": _Reply({"data": []})})

    tags = {m["tag"] for m in await service.list_models()}

    assert "google/imagen-x" not in tags
    assert "meta-llama/llama-guard-4-12b" not in tags


async def test_a_missing_embedding_listing_does_not_hide_the_chat_models(openrouter):
    service, _ = openrouter({"/models": _Reply(_MODELS)})  # /embeddings/models -> 404

    tags = {m["tag"] for m in await service.list_models()}

    assert "anthropic/claude-sonnet-4" in tags
    assert "openai/text-embedding-3-small" not in tags


async def test_an_unreachable_catalogue_is_reported_with_the_fix(openrouter):
    from shared.exceptions import LLMProviderError

    service, _ = openrouter({"/models": _Reply({}, 401)})

    with pytest.raises(LLMProviderError, match="OPENROUTER_API_KEY"):
        await service.list_models()


async def test_the_catalogue_splits_chat_from_embedding(openrouter, monkeypatch):
    service, _ = openrouter(
        {
            "/models": _Reply(_MODELS),
            "/embeddings/models": _Reply(_EMBEDDERS),
            "POST": lambda body: _Reply({"data": [{"embedding": [0.0] * 1536}]}),
        }
    )
    monkeypatch.setattr(ModelService, "_hosts", lambda self, sources=None: [service])
    # Keys in a developer's src/.env would add hosted models, and start a live
    # probe against them.
    monkeypatch.setattr(service.settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(service.settings, "GOOGLE_API_KEY", None)

    catalogue = await ModelService().catalogue()

    assert {m["id"] for m in catalogue["chat"]} == {
        "openrouter/anthropic/claude-sonnet-4",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    }
    assert [(m["id"], m["dimensions"]) for m in catalogue["embedding"]] == [
        ("openrouter/openai/text-embedding-3-small", 1536)
    ]


def test_openrouter_is_only_a_host_when_there_is_a_key(monkeypatch, settings):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    assert OPENROUTER not in [h.source for h in ModelService()._hosts()]

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-test")
    assert OPENROUTER in [h.source for h in ModelService()._hosts()]


def test_for_source_returns_the_openrouter_service():
    assert isinstance(for_source(OPENROUTER), OpenRouterModelService)
