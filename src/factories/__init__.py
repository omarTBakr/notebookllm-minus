"""Provider cache and factory wrappers.

A "factory" parses configuration, sets up credentials, and maps the provider
name to an implementation class.

A "provider" is the implementation itself: it holds the connection pool or HTTP
session, and exposes the specific backend's features through the shared
interface.
"""

from .db import DbFactory, DbProvider, VectorRepository
from .llmchatting import LLMChattingFactory, LLMChattingInterface
from .llmembedding import LLMEmbeddingFactory, LLMEmbeddingInterface
from .provider_cache import ProviderCache

__all__ = [
    "DbFactory",
    "DbProvider",
    "VectorRepository",
    "LLMChattingFactory",
    "LLMChattingInterface",
    "LLMEmbeddingFactory",
    "LLMEmbeddingInterface",
    "ProviderCache",
]
