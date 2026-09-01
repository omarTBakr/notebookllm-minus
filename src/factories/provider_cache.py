"""Builds and reuses provider clients per model.

The lifespan builds one chat client and one embedding client from ``.env``.
That is the right default, but it ties every conversation to the same two
models. This cache lets a chat name its own model and still share a connection
pool with every other chat using it, instead of constructing a client per
request and leaking pools.

Keyed on the qualified model id ("local/llama3.1:8b"), so switching a chat back
to a model already in use costs a dict lookup — and the same tag on two
different Ollama hosts stays two different clients.

The prefix also picks the *provider*: "nvidia/..." builds an NVIDIA client
even when GENERATION_BACKEND says ollama. That is what lets one notebook run
on a local model and the next on a hosted one, which a single global backend
setting could not express.
"""

from utils import (
    OLLAMA_SOURCES,
    Settings,
    backend_for,
    default_chat_model,
    default_embedding_model,
    get_logger,
    host_for,
    split_source,
)

from .llmchatting import LLMChattingFactory, LLMChattingInterface
from .llmembedding import LLMEmbeddingFactory, LLMEmbeddingInterface

logger = get_logger(__name__)


class ProviderCache:
    """One client per (kind, model), created on first use."""

    def __init__(self, settings: Settings) -> None:

        self.settings = settings

        self._chatting: dict[str, LLMChattingInterface] = {}
        self._embedding: dict[tuple[str, int], LLMEmbeddingInterface] = {}

    def _for_source(self, source: str, backend_field: str, **overrides) -> Settings:
        """A copy of Settings describing *source* rather than .env's default.

        The backend comes from the id's prefix, and an Ollama host is resolved
        only for the sources that have one — asking host_for about a vendor
        prefix is a caller bug it will raise on.
        """
        update = {backend_field: backend_for(source), **overrides}

        if source in OLLAMA_SOURCES:
            update["OLLAMA_BASE_URL_OVERRIDE"] = host_for(self.settings, source)

        return self.settings.model_copy(update=update)

    # --- chatting -------------------------------------------------------------

    def chatting(self, model_id: str | None = None) -> LLMChattingInterface:
        """The chat client for *model_id*, defaulting to GENERATION_MODEL_ID."""

        # Qualified, never raw: an NVIDIA tag begins with its publisher, so
        # split_source would read "nvidia/nemotron-3-embed-1b" as source
        # nvidia plus tag nemotron-3-embed-1b and ask the vendor for a
        # model id that is one segment short of existing.
        resolved = model_id or default_chat_model(self.settings)

        cached = self._chatting.get(resolved)
        if cached is not None:
            return cached

        # A shallow copy of Settings with the model, its backend and its host
        # swapped: the factory reads every other knob (keys, temperature) from
        # it, so this overrides only what the id decides — and without the
        # provider classes learning that a chat can name a host or a vendor.
        source, tag = split_source(resolved)

        settings = self._for_source(source, "GENERATION_BACKEND", GENERATION_MODEL_ID=tag)

        client = LLMChattingFactory(settings).create()

        self._chatting[resolved] = client

        logger.info("Opened chat client for model %r", resolved)

        return client

    # --- embedding ------------------------------------------------------------

    def embedding(
        self, model_id: str | None = None, dimensions: int | None = None
    ) -> LLMEmbeddingInterface:
        """The embedding client for *model_id* at *dimensions*.

        Both are part of the key: the same model asked for a different width
        produces vectors that belong in a different collection, so they must
        not share a client whose ``embedding_size`` validates the result.
        """
        resolved = model_id or default_embedding_model(self.settings)
        size = dimensions or self.settings.EMBEDDING_MODEL_SIZE

        cached = self._embedding.get((resolved, size))
        if cached is not None:
            return cached

        source, tag = split_source(resolved)

        settings = self._for_source(
            source,
            "EMBEDDING_BACKEND",
            EMBEDDING_MODEL_ID=tag,
            EMBEDDING_MODEL_SIZE=size,
        )

        client = LLMEmbeddingFactory(settings).create()

        self._embedding[(resolved, size)] = client

        logger.info("Opened embedding client for model %r (%d dims)", resolved, size)

        return client

    # --- shutdown -------------------------------------------------------------

    async def aclose_all(self) -> None:
        """Close every pool this cache opened, isolating each failure."""

        for label, client in [
            *((f"chat:{k}", v) for k, v in self._chatting.items()),
            *((f"embed:{k[0]}", v) for k, v in self._embedding.items()),
        ]:
            try:
                await client.aclose()
            except Exception as exc:
                logger.warning("Could not close %s cleanly: %s", label, exc)

        self._chatting.clear()
        self._embedding.clear()
