"""Fixed lookup tables translating this project's enums into external vocabularies.

Each provider or backend speaks its own wire format for a concept this project
already has an enum for (ChatRole, EmbeddingInputType, DistanceMethod, ...).
These tables used to live as private dicts on whichever class happened to
consume them first; they live together here instead, next to the enums they
key off.
"""

from langchain_text_splitters import Language  # ty: ignore[unresolved-import]

from .LLMChattingEnum import ChatRole, LLMChattingProvider
from .LLMEmbeddingEnum import EmbeddingInputType, LLMEmbeddingProvider, TruncateMode
from .db import DistanceMethod

# Gemini calls the assistant "model"; the user role keeps its name.
CHAT_ROLE_TO_GOOGLE: dict[str, str] = {
    ChatRole.USER.value: "user",
    ChatRole.ASSISTANT.value: "model",
}

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

# pgvector operator and index opclass per distance metric.
#   cosine: <=> / vector_cosine_ops
#   dot:    <#> / vector_ip_ops      (inner product; pgvector negates it)
#   euclid: <-> / vector_l2_ops
DISTANCE_METHOD_TO_PGVECTOR: dict[DistanceMethod, tuple[str, str]] = {
    DistanceMethod.COSINE: ("<=>", "vector_cosine_ops"),
    DistanceMethod.DOT: ("<#>", "vector_ip_ops"),
    DistanceMethod.EUCLID: ("<->", "vector_l2_ops"),
}

# provider -> the Settings attribute holding its key. Ollama is absent on
# purpose: it runs locally and authenticates by host, not by key.
CHAT_PROVIDER_API_KEY_FIELDS: dict[LLMChattingProvider, str] = {
    LLMChattingProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    LLMChattingProvider.OPENAI: "OPENAI_API_KEY",
    LLMChattingProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMChattingProvider.COHERE: "COHERE_API_KEY",
    LLMChattingProvider.NVIDIA: "NVIDIA_API_KEY",
}

# provider -> {constructor keyword: the Settings field that fills it}.
#
# Everything a provider needs beyond the three knobs every provider takes
# (model, max tokens, temperature) and its API key. The factory walks this
# table instead of growing an `if chosen is ...` per vendor, so giving a
# provider a new knob is a line here plus a field on Settings — no factory
# edit, and nothing vendor-specific baked into a provider class.
#
# A field that is unset or blank is simply not passed, leaving the provider's
# own signature default in charge. Ollama is absent: its base URL is a derived
# property (ollama_base_url), not a plain field, so the factory builds it.
CHAT_PROVIDER_SETTING_KWARGS: dict[LLMChattingProvider, dict[str, str]] = {
    LLMChattingProvider.OPENAI: {"base_url": "OPENAI_API_BASE_URL"},
    LLMChattingProvider.NVIDIA: {"base_url": "NVIDIA_API_BASE_URL"},
}

# Ollama is absent on purpose: it runs locally and authenticates by host.
EMBEDDING_PROVIDER_API_KEY_FIELDS: dict[LLMEmbeddingProvider, str] = {
    LLMEmbeddingProvider.OPENAI: "OPENAI_API_KEY",
    LLMEmbeddingProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMEmbeddingProvider.COHERE: "COHERE_API_KEY",
    LLMEmbeddingProvider.NVIDIA: "NVIDIA_API_KEY",
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
}

# Extensions with their own structure-aware separator list. Anything not
# named here falls through to the plain-prose splitter — in particular .txt
# and .pdf.
LANGUAGE_SPLITTERS: dict[str, Language] = {
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
}
