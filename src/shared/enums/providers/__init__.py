"""Enums naming the LLM backends this application talks to, and the lookup
tables translating this project's vocabulary into each vendor's own."""

from .chatting import ChatRole, LLMChattingProvider, ThinkingLevel
from .chatting_mappings import (
    CHAT_PROVIDER_API_KEY_FIELDS,
    CHAT_PROVIDER_SETTING_KWARGS,
    CHAT_ROLE_TO_GOOGLE,
)
from .embedding import EmbeddingInputType, LLMEmbeddingProvider, TruncateMode
from .embedding_mappings import (
    EMBEDDING_INPUT_TYPE_TO_COHERE,
    EMBEDDING_INPUT_TYPE_TO_GOOGLE,
    EMBEDDING_INPUT_TYPE_TO_NVIDIA,
    EMBEDDING_PROVIDER_API_KEY_FIELDS,
    EMBEDDING_PROVIDER_SETTING_KWARGS,
    EMBEDDING_TRUNCATE_TO_NVIDIA,
)
from .model import ModelCapability, NvidiaSafetyModelMarker

__all__ = [
    "CHAT_PROVIDER_API_KEY_FIELDS",
    "CHAT_PROVIDER_SETTING_KWARGS",
    "CHAT_ROLE_TO_GOOGLE",
    "ChatRole",
    "EMBEDDING_INPUT_TYPE_TO_COHERE",
    "EMBEDDING_INPUT_TYPE_TO_GOOGLE",
    "EMBEDDING_INPUT_TYPE_TO_NVIDIA",
    "EMBEDDING_PROVIDER_API_KEY_FIELDS",
    "EMBEDDING_PROVIDER_SETTING_KWARGS",
    "EMBEDDING_TRUNCATE_TO_NVIDIA",
    "EmbeddingInputType",
    "LLMChattingProvider",
    "LLMEmbeddingProvider",
    "ModelCapability",
    "NvidiaSafetyModelMarker",
    "ThinkingLevel",
    "TruncateMode",
]
