from enum import Enum


class LLMEmbeddingProvider(str, Enum):
    """Embedding backends this application knows how to build.

    Anthropic is absent on purpose: it ships no embeddings API and points
    users at a partner instead. Listing it here would only produce a factory
    entry that always fails.
    """

    OPENAI = "openai"
    GOOGLE = "google"
    COHERE = "cohere"
    # NVIDIA NIM — the OpenAI embeddings API at integrate.api.nvidia.com,
    # asymmetric (it wants input_type) and capped at 256 inputs per request.
    NVIDIA = "nvidia"
    OLLAMA = "ollama"  # local models; no API key, needs OLLAMA_HOST/PORT


class EmbeddingInputType(str, Enum):
    """Whether the text being embedded is a stored document or a live query.

    Not decoration — asymmetric models embed the two differently, and getting
    it wrong quietly degrades retrieval: Cohere wants
    input_type=search_document/search_query, Google wants
    task_type=RETRIEVAL_DOCUMENT/RETRIEVAL_QUERY, NVIDIA wants
    input_type=passage/query, OpenAI ignores all of it.
    """

    DOCUMENT = "document"
    QUERY    = "query"


class TruncateMode(str, Enum):
    """What an embedding provider should do with input longer than its context.

    NONE fails the request, START and END drop the overflowing end of the text.
    Named in this project's own lower-case vocabulary like every other .env
    choice; the wire spelling is a provider's business (see
    EMBEDDING_TRUNCATE_TO_NVIDIA). Only the NVIDIA provider reads it today —
    Ollama and the other hosted SDKs expose no equivalent.
    """

    NONE  = "none"
    START = "start"
    END   = "end"
