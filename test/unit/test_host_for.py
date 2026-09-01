"""Which Ollama a model id points at."""

import pytest

from exceptions import LLMProviderError, NotebookLLMError
from utils import CLOUD, LOCAL, NVIDIA, host_for


def test_local_resolves_to_the_local_host(settings):
    assert host_for(settings, LOCAL) == settings.ollama_base_url


def test_cloud_resolves_to_the_cloud_host(settings):
    assert host_for(settings, CLOUD) == settings.OLLAMA_CLOUD_BASE_URL


def test_an_unconfigured_cloud_raises_a_domain_error(settings):
    """It used to be a bare ValueError in ProviderCache, which the handler in
    main.py does not catch — so a misconfigured chat produced a blank 500
    instead of a message naming the missing setting."""
    naked = settings.model_copy(update={"OLLAMA_CLOUD_BASE_URL": None})

    with pytest.raises(LLMProviderError) as caught:
        host_for(naked, CLOUD)

    assert "OLLAMA_CLOUD_BASE_URL" in str(caught.value)


def test_that_error_maps_to_a_gateway_status(settings):
    """502: the fault is an upstream we depend on, not this application."""
    naked = settings.model_copy(update={"OLLAMA_CLOUD_BASE_URL": None})

    with pytest.raises(NotebookLLMError) as caught:
        host_for(naked, CLOUD)

    assert caught.value.status_code == 502


def test_a_vendor_source_has_no_ollama_host(settings):
    """This used to return the local Ollama URL for anything that was not
    "cloud". Harmless while every source *was* an Ollama host; now that
    "nvidia/..." is a real id, that fallback would hand an NVIDIA model a
    localhost URL and produce a connection error pointing at the wrong
    machine. Asking is a caller bug, so it says so."""
    with pytest.raises(LLMProviderError) as caught:
        host_for(settings, NVIDIA)

    assert NVIDIA in str(caught.value)


def test_an_unknown_source_also_raises(settings):
    """split_source only ever yields a known source, so reaching here at all
    means a caller invented one."""
    with pytest.raises(LLMProviderError):
        host_for(settings, "whatever")
