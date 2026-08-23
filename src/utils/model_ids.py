"""Which Ollama a model lives on, encoded in the model id itself.

There can be two Ollama hosts — the local one and a second reached over the
network (OLLAMA_CLOUD_BASE_URL). Both are Ollama, so a tag alone no longer
identifies a model: the same tag can be pulled on both, and they are different
models to us. Ids are therefore qualified — "local/llama3.1:8b",
"cloud/gemma4:latest".

This lives in utils rather than beside ModelController because both the
controllers and the provider factories need it, and controllers already import
factories — putting it in either would close the loop.
"""

LOCAL = "local"
CLOUD = "cloud"
SOURCES = (LOCAL, CLOUD)


def split_source(model_id: str) -> tuple[str, str]:
    """Split a qualified model id into (source, bare tag).

    Ollama tags can themselves contain a slash — "dimavz/whisper-tiny:latest"
    is one model, not a namespaced anything — so only a *known* prefix counts
    as a source. An id with no recognised prefix is local, which is exactly
    what every chat stored before there was a second host.
    """
    prefix, _, rest = model_id.partition("/")

    if prefix in SOURCES and rest:
        return prefix, rest

    return LOCAL, model_id


def qualify(source: str, tag: str) -> str:
    """The inverse of split_source."""
    return f"{source}/{tag}"


def host_for(settings, source: str) -> str:
    """The Ollama base URL that *source* names.

    Raises rather than falling back to the local host: a chat set to a cloud
    model would otherwise ask localhost for a model it never pulled, and the
    resulting "model not found" says nothing about the real problem.
    """
    # Imported here: exceptions is a leaf, but utils is imported by almost
    # everything and a module-level import would widen its import graph.
    from exceptions import LLMProviderError

    if source != CLOUD:
        return settings.OLLAMA_BASE_URL

    host = settings.OLLAMA_CLOUD_BASE_URL

    if not host:
        raise LLMProviderError(
            "A cloud model was named but OLLAMA_CLOUD_BASE_URL is not set"
        )

    return host
