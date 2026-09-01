"""Where a model lives, encoded in the model id itself.

There can be two Ollama hosts — the local one and a second reached over the
network (OLLAMA_CLOUD_BASE_URL). Both are Ollama, so a tag alone no longer
identifies a model: the same tag can be pulled on both, and they are different
models to us. Ids are therefore qualified — "local/llama3.1:8b",
"cloud/gemma4:latest".

A hosted vendor joins the same scheme: "nvidia/meta/llama-3.2-11b-vision-instruct"
is NVIDIA's, and the prefix is what tells ProviderCache to build an NVIDIA
client rather than an Ollama one. NVIDIA's own ids contain a slash (the
publisher), which costs nothing here — only the *first* segment is read as a
source, and the rest is handed back whole.

This lives in utils rather than beside ModelController because both the
controllers and the provider factories need it, and controllers already import
factories — putting it in either would close the loop.
"""

from enums import LLMChattingProvider

LOCAL = "local"
CLOUD = "cloud"
NVIDIA = "nvidia"

# Every prefix an id may carry. The first two are Ollama hosts; NVIDIA is a
# hosted vendor, which is why `backend_for` exists below — the prefix now picks
# the *provider*, not just which machine to ask.
SOURCES = (LOCAL, CLOUD, NVIDIA)

# The subset that is Ollama, and therefore has a base URL to point a client at.
OLLAMA_SOURCES = (LOCAL, CLOUD)


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


def backend_for(source: str) -> str:
    """The provider backend an id's *source* names.

    The two Ollama sources differ only in which host answers, so both are
    "ollama"; a vendor prefix is the backend itself. This is what lets one
    chat run on a local model while the next runs on NVIDIA, without either
    touching GENERATION_BACKEND in .env.
    """
    return LLMChattingProvider.OLLAMA.value if source in OLLAMA_SOURCES else source


def source_of(backend: str) -> str:
    """Which source a configured *backend* corresponds to — inverse of backend_for.

    "ollama" is the local host: a bare tag in .env has never meant the cloud
    one. Any other backend names itself, because a vendor is its own source.
    """
    backend = str(getattr(backend, "value", backend)).strip().lower()

    return LOCAL if backend == LLMChattingProvider.OLLAMA.value else backend


def default_chat_model(settings) -> str:
    """The qualified id of the model GENERATION_MODEL_ID names.

    .env names a model the way its vendor does — "gemma4:e4b",
    "nvidia/nemotron-3-embed-1b" — and says separately which backend serves it.
    Qualifying the two together here is what keeps every caller agreeing on one
    spelling.

    It matters more than it looks. An NVIDIA tag already begins with a
    publisher, so a raw .env value like "nvidia/nemotron-3-embed-1b" reads to
    split_source as source "nvidia" plus tag "nemotron-3-embed-1b" — one
    segment short, and 404 from the vendor. Going through qualify() first
    makes the leading segment the source it actually is.
    """
    return qualify(source_of(settings.GENERATION_BACKEND), settings.GENERATION_MODEL_ID)


def default_embedding_model(settings) -> str:
    """The qualified id of the model EMBEDDING_MODEL_ID names."""
    return qualify(source_of(settings.EMBEDDING_BACKEND), settings.EMBEDDING_MODEL_ID)


def host_for(settings, source: str) -> str:
    """The Ollama base URL that *source* names.

    Raises rather than falling back to the local host: a chat set to a cloud
    model would otherwise ask localhost for a model it never pulled, and the
    resulting "model not found" says nothing about the real problem. Asking
    for a non-Ollama source's host is a caller bug, and says so.
    """
    # Imported here: exceptions is a leaf, but utils is imported by almost
    # everything and a module-level import would widen its import graph.
    from exceptions import LLMProviderError

    if source not in OLLAMA_SOURCES:
        raise LLMProviderError(
            f"{source!r} is not an Ollama source, so it has no Ollama host "
            f"(expected one of {OLLAMA_SOURCES})"
        )

    if source != CLOUD:
        return settings.ollama_base_url

    host = settings.OLLAMA_CLOUD_BASE_URL

    if not host:
        raise LLMProviderError(
            "A cloud model was named but OLLAMA_CLOUD_BASE_URL is not set"
        )

    return host
