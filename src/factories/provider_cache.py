"""Builds and reuses provider clients per model.

The lifespan builds one chat client and one embedding client from ``.env``.
That is the right default, but it ties every conversation to the same two
models. This cache lets a chat name its own model and still share a connection
pool with every other chat using it, instead of constructing a client per
request and leaking pools.

Keyed on the qualified model id ("local/llama3.1:8b"), so switching a chat back
to a model already in use costs a dict lookup — and the same tag on two
different Ollama hosts stays two different clients.
"""

from utils import Settings, get_logger, host_for, split_source

from .llmchatting import LLMChattingFactory, LLMChattingInterface
from .llmembedding import LLMEmbeddingFactory, LLMEmbeddingInterface

logger = get_logger(__name__)


class ProviderCache:
    """One client per (kind, model), created on first use."""

    def __init__(self, settings: Settings) -> None:

        self.settings = settings

        self._chatting: dict[str, LLMChattingInterface] = {}
        self._embedding: dict[tuple[str, int], LLMEmbeddingInterface] = {}

    # --- chatting -------------------------------------------------------------

    def chatting(self, model_id: str | None = None) -> LLMChattingInterface:
        """The chat client for *model_id*, defaulting to GENERATION_MODEL_ID."""

        resolved = model_id or self.settings.GENERATION_MODEL_ID

        cached = self._chatting.get(resolved)
        if cached is not None:
            return cached

        # A shallow copy of Settings with the model and its host swapped: the
        # factory reads every other knob (backend, key, temperature) from it,
        # so this overrides two fields without duplicating that logic — and
        # without the provider classes learning there are two hosts at all.
        source, tag = split_source(resolved)

        settings = self.settings.model_copy(
            update={
                "GENERATION_MODEL_ID": tag,
                "OLLAMA_BASE_URL": host_for(self.settings, source),
            }
        )

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
        resolved = model_id or self.settings.EMBEDDING_MODEL_ID
        size = dimensions or self.settings.EMBEDDING_MODEL_SIZE

        cached = self._embedding.get((resolved, size))
        if cached is not None:
            return cached

        source, tag = split_source(resolved)

        settings = self.settings.model_copy(
            update={
                "EMBEDDING_MODEL_ID": tag,
                "EMBEDDING_MODEL_SIZE": size,
                "OLLAMA_BASE_URL": host_for(self.settings, source),
            }
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
