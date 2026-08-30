"""Fixed lookup tables translating this project's enums into external vocabularies.

Each provider or backend speaks its own wire format for a concept this project
already has an enum for (ChatRole, EmbeddingInputType, DistanceMethod, ...).
These tables used to live as private dicts on whichever class happened to
consume them first; they live together here instead, next to the enums they
key off.
"""

from langchain_text_splitters import Language  # ty: ignore[unresolved-import]

from .LLMChattingEnum import ChatRole, LLMChattingProvider
from .LLMEmbeddingEnum import EmbeddingInputType, LLMEmbeddingProvider
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
}

# Ollama is absent on purpose: it runs locally and authenticates by host.
EMBEDDING_PROVIDER_API_KEY_FIELDS: dict[LLMEmbeddingProvider, str] = {
    LLMEmbeddingProvider.OPENAI: "OPENAI_API_KEY",
    LLMEmbeddingProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMEmbeddingProvider.COHERE: "COHERE_API_KEY",
}

# Extensions with their own structure-aware separator list. Anything not
# named here falls through to the plain-prose splitter — in particular .txt
# and .pdf.
LANGUAGE_SPLITTERS: dict[str, Language] = {
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
}
