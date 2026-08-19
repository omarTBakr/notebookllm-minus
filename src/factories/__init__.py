"""Swappable provider layer: text generation, embeddings, vector storage.

Each subpackage is one abstract interface plus a concrete implementation per
vendor, built by a factory that reads the backend name out of ``Settings``.
Nothing above this package names a vendor, so changing provider is an ``.env``
edit rather than a code change.
"""

from .provider_cache import ProviderCache
from .llmchatting import LLMChattingFactory, LLMChattingInterface
from .llmembedding import LLMEmbeddingFactory, LLMEmbeddingInterface
from .vectordb import VectorDBFactory, VectorDBInterface

__all__ = [
    "ProviderCache",
    "LLMChattingFactory",
    "LLMChattingInterface",
    "LLMEmbeddingFactory",
    "LLMEmbeddingInterface",
    "VectorDBFactory",
    "VectorDBInterface",
]
