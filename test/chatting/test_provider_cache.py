"""One client per qualified model id, pointed at the host the id names."""

import pytest

from factories.provider_cache import ProviderCache
from factories.llmchatting import LLMChattingInterface


@pytest.fixture
def cache(settings):
    return ProviderCache(settings)


def test_a_local_id_builds_a_client_on_the_local_host(cache, settings):
    client = cache.chatting("local/llama3.1:8b")

    assert client.base_url == settings.OLLAMA_BASE_URL
    assert client.model_id == "llama3.1:8b"


def test_a_cloud_id_builds_a_client_on_the_cloud_host(cache, settings):
    client = cache.chatting("cloud/gemma4:31b")

    assert client.base_url == settings.OLLAMA_CLOUD_BASE_URL
    assert client.model_id == "gemma4:31b"


def test_a_bare_tag_is_treated_as_local(cache, settings):
    """Chats saved before there were two hosts store an unqualified tag."""
    client = cache.chatting("gemma4:e4b")

    assert client.base_url == settings.OLLAMA_BASE_URL
    assert client.model_id == "gemma4:e4b"


def test_the_same_id_returns_the_same_client(cache):
    assert cache.chatting("local/x:1b") is cache.chatting("local/x:1b")


def test_the_same_tag_on_two_hosts_is_two_clients(cache):
    """Otherwise whichever was built first would answer for both."""
    local = cache.chatting("local/gemma4:latest")
    cloud = cache.chatting("cloud/gemma4:latest")

    assert local is not cloud
    assert local.base_url != cloud.base_url


def test_no_model_id_falls_back_to_the_configured_default(cache, settings):
    assert cache.chatting().model_id == settings.GENERATION_MODEL_ID


def test_embedding_is_keyed_on_model_and_width(cache):
    eight = cache.embedding("local/e:1b", 8)
    sixteen = cache.embedding("local/e:1b", 16)

    assert eight is not sixteen
    assert eight.embedding_size == 8
    assert sixteen.embedding_size == 16


def test_embedding_routes_to_the_cloud_host_too(cache, settings):
    assert cache.embedding("cloud/e:1b", 8).base_url == settings.OLLAMA_CLOUD_BASE_URL


def test_clients_implement_the_interface(cache):
    assert isinstance(cache.chatting("local/x:1b"), LLMChattingInterface)


async def test_aclose_all_closes_and_empties(cache):
    cache.chatting("local/a:1b")
    cache.embedding("local/b:1b", 8)

    await cache.aclose_all()

    assert cache._chatting == {} and cache._embedding == {}
