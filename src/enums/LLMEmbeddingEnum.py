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
    OLLAMA = "ollama"  # local models; no API key, needs OLLAMA_HOST/PORT


class EmbeddingInputType(str, Enum):
    """Whether the text being embedded is a stored document or a live query.

    Not decoration — asymmetric models embed the two differently, and getting
    it wrong quietly degrades retrieval: Cohere wants
    input_type=search_document/search_query, Google wants
    task_type=RETRIEVAL_DOCUMENT/RETRIEVAL_QUERY, OpenAI ignores both.
    """

    DOCUMENT = "document"
    QUERY    = "query"
