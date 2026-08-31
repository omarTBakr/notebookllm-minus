"""NvidiaEmbeddingProvider: the three ways NVIDIA's endpoint differs from OpenAI's.

The client is stubbed out — what is under test is the batching, the input_type
translation and the factory wiring, none of which needs the network. The live
endpoint was checked by hand; these are the parts that must not regress.
"""

import pytest

from enums import EmbeddingInputType, TruncateMode
from exceptions import EmbeddingError, UnsupportedProviderError
from factories.llmembedding import LLMEmbeddingFactory, NvidiaEmbeddingProvider


class _Vector:
    def __init__(self, index: int, text: str, width: int):
        self.index = index
        # Keyed on the text, so a vector can be traced back to the input it
        # came from — which is what the ordering tests actually assert.
        self.embedding = [float(len(text))] * (width - 1) + [float(hash(text) % 1000)]


class _FakeEmbeddings:
    """Records every request, and answers one traceable vector per input."""

    def __init__(self, width: int = 2048, reverse: bool = False):
        self.width = width
        self.reverse = reverse
        self.requests: list[dict] = []

    def vector_for(self, text: str) -> list[float]:
        return _Vector(0, text, self.width).embedding

    async def create(self, **kwargs):
        self.requests.append(kwargs)

        vectors = [_Vector(i, text, self.width) for i, text in enumerate(kwargs["input"])]

        # The API promises an index on each item, not an order, so half the
        # tests hand them back shuffled.
        if self.reverse:
            vectors.reverse()

        return type("Response", (), {"data": vectors})()


def _provider(settings, overrides: dict | None = None, **fake):
    """The provider exactly as the factory builds it from .env, stubbed client."""
    client = LLMEmbeddingFactory(
        settings.model_copy(
            update={
                "NVIDIA_API_KEY": "nvapi-test",
                "EMBEDDING_MODEL_SIZE": 2048,
                **(overrides or {}),
            }
        )
    ).create(provider="nvidia")

    client.client.embeddings = _FakeEmbeddings(**fake)

    return client


# --- the factory --------------------------------------------------------------


def test_the_factory_builds_nvidia_at_the_configured_endpoint(settings):
    client = LLMEmbeddingFactory(
        settings.model_copy(update={"NVIDIA_API_KEY": "nvapi-test"})
    ).create(provider="nvidia")

    assert isinstance(client, NvidiaEmbeddingProvider)
    assert str(client.client.base_url).rstrip("/") == settings.NVIDIA_API_BASE_URL


def test_the_factory_passes_the_configured_cap_and_truncation(settings):
    """Both are NVIDIA's numbers, so both come from .env rather than the class."""
    client = LLMEmbeddingFactory(
        settings.model_copy(
            update={
                "NVIDIA_API_KEY": "nvapi-test",
                "NVIDIA_EMBEDDING_MAX_BATCH": 32,
                "NVIDIA_EMBEDDING_TRUNCATE": TruncateMode.START,
            }
        )
    ).create(provider="nvidia")

    assert client.max_batch == 32
    assert client.truncate == "START"      # NVIDIA's spelling, not the enum's


def test_the_factory_rejects_nvidia_with_no_key(settings):
    with pytest.raises(UnsupportedProviderError):
        LLMEmbeddingFactory(settings.model_copy(update={"NVIDIA_API_KEY": ""})).create(
            provider="nvidia"
        )


# --- input_type ---------------------------------------------------------------


@pytest.mark.parametrize("given, sent", [
    (EmbeddingInputType.DOCUMENT, "passage"),
    (EmbeddingInputType.QUERY, "query"),
])
async def test_input_type_is_translated_for_nvidia(settings, given, sent):
    """Optional on the wire and silently degrading when omitted, so it is
    always sent — and in NVIDIA's spelling, not Cohere's or Google's."""
    client = _provider(settings)

    await client.embed(["text"], input_type=given)

    assert client.client.embeddings.requests[0]["extra_body"]["input_type"] == sent


async def test_the_truncation_mode_is_sent_in_nvidias_spelling(settings):
    client = _provider(settings, {"NVIDIA_EMBEDDING_TRUNCATE": TruncateMode.NONE})

    await client.embed(["text"])

    assert client.client.embeddings.requests[0]["extra_body"]["truncate"] == "NONE"


async def test_dimensions_are_never_sent(settings):
    """The width is fixed; asking for it can only produce a vendor 400 where
    the interface's own width check gives the better message."""
    client = _provider(settings)

    await client.embed(["text"])

    assert "dimensions" not in client.client.embeddings.requests[0]


# --- the 256-input cap --------------------------------------------------------


@pytest.mark.parametrize("count, requests", [(1, 1), (256, 1), (257, 2), (600, 3)])
async def test_a_batch_is_split_at_the_cap(settings, count, requests):
    client = _provider(settings)

    vectors = await client.embed([f"chunk {i}" for i in range(count)])

    assert len(vectors) == count
    assert len(client.client.embeddings.requests) == requests
    assert all(
        len(request["input"]) <= client.max_batch
        for request in client.client.embeddings.requests
    )


async def test_the_cap_is_whatever_env_says(settings):
    """NVIDIA's limit, not ours — raising NVIDIA_EMBEDDING_MAX_BATCH must be
    all it takes to send bigger requests."""
    client = _provider(settings, {"NVIDIA_EMBEDDING_MAX_BATCH": 2})

    await client.embed(["a", "b", "c", "d", "e"])

    assert [len(r["input"]) for r in client.client.embeddings.requests] == [2, 2, 1]


async def test_split_batches_keep_the_input_order(settings):
    """Vectors are zipped against chunk ids by position downstream, so a split
    that reorders anything silently attaches every vector to the wrong chunk."""
    client = _provider(settings)
    texts = [f"chunk {i}" for i in range(300)]

    vectors = await client.embed(texts)

    sent = [text for request in client.client.embeddings.requests for text in request["input"]]

    assert sent == texts
    # ...and the vectors come back attached to the right ones, across the seam
    # between one request and the next.
    assert vectors == [client.client.embeddings.vector_for(text) for text in texts]


async def test_a_response_out_of_order_is_sorted_by_index(settings):
    client = _provider(settings, reverse=True)

    vectors = await client.embed(["a", "bb", "ccc"])

    assert vectors == [client.client.embeddings.vector_for(t) for t in ("a", "bb", "ccc")]


# --- the width check still applies --------------------------------------------


async def test_a_wrong_embedding_model_size_names_the_setting(settings):
    client = _provider(settings, width=768)

    with pytest.raises(EmbeddingError, match="EMBEDDING_MODEL_SIZE"):
        await client.embed(["text"])
