"""Fixed lookup tables translating this project's embedding enums into vendor vocabularies.

Each provider speaks its own wire format for a concept this project already has
an enum for (EmbeddingInputType, TruncateMode, LLMEmbeddingProvider). These
tables used to live as private dicts on whichever class happened to consume
them first; they live beside the enums they key off instead.
"""

from .embedding import EmbeddingInputType, LLMEmbeddingProvider, TruncateMode

# Cohere requires input_type on every call — there is no neutral default.
EMBEDDING_INPUT_TYPE_TO_COHERE: dict[EmbeddingInputType, str] = {
    EmbeddingInputType.DOCUMENT: "search_document",
    EmbeddingInputType.QUERY: "search_query",
}

# Gemini's models are asymmetric: the task_type materially changes the
# vector, so a query embedded as a document retrieves worse.
EMBEDDING_INPUT_TYPE_TO_GOOGLE: dict[EmbeddingInputType, str] = {
    EmbeddingInputType.DOCUMENT: "RETRIEVAL_DOCUMENT",
    EmbeddingInputType.QUERY: "RETRIEVAL_QUERY",
}

# NVIDIA's embedding NIMs are asymmetric too, and spell the same distinction
# a third way. Optional on the wire — the call succeeds without it and the
# vectors are quietly worse, which is exactly the kind of silence worth
# spending a lookup table to avoid.
EMBEDDING_INPUT_TYPE_TO_NVIDIA: dict[EmbeddingInputType, str] = {
    EmbeddingInputType.DOCUMENT: "passage",
    EmbeddingInputType.QUERY: "query",
}

# NVIDIA spells the truncation modes in upper case; this project spells every
# .env choice in lower case (see TruncateMode). One table, one place to look.
EMBEDDING_TRUNCATE_TO_NVIDIA: dict[TruncateMode, str] = {
    TruncateMode.NONE: "NONE",
    TruncateMode.START: "START",
    TruncateMode.END: "END",
}

# Ollama is absent on purpose: it runs locally and authenticates by host.
EMBEDDING_PROVIDER_API_KEY_FIELDS: dict[LLMEmbeddingProvider, str] = {
    LLMEmbeddingProvider.OPENAI: "OPENAI_API_KEY",
    LLMEmbeddingProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMEmbeddingProvider.COHERE: "COHERE_API_KEY",
    LLMEmbeddingProvider.NVIDIA: "NVIDIA_API_KEY",
    LLMEmbeddingProvider.OPENROUTER: "OPENROUTER_API_KEY",
}

# The embedding half of CHAT_PROVIDER_SETTING_KWARGS, same reasoning. NVIDIA
# carries two more knobs than an endpoint: the per-request input cap and what
# to do with an over-long text, both of which are its limits rather than
# facts about embedding, and both of which move when NVIDIA moves them.
EMBEDDING_PROVIDER_SETTING_KWARGS: dict[LLMEmbeddingProvider, dict[str, str]] = {
    LLMEmbeddingProvider.OPENAI: {"base_url": "OPENAI_API_BASE_URL"},
    LLMEmbeddingProvider.NVIDIA: {
        "base_url": "NVIDIA_API_BASE_URL",
        "max_batch": "NVIDIA_EMBEDDING_MAX_BATCH",
        "truncate": "NVIDIA_EMBEDDING_TRUNCATE",
    },
    LLMEmbeddingProvider.OPENROUTER: {"base_url": "OPENROUTER_API_BASE_URL"},
}
