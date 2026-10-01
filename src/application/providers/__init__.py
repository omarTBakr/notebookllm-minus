"""Vendor-facing adapters: the LLM chat/embedding clients a service calls.

Distinct from `application/services/llm/`, which holds the business logic
*about* models (discovering what a provider can be asked for, insisting on a
structured answer) — this is the adapter layer underneath it, one class per
vendor API. Not a data-tier concern despite the historical "factories"
grouping this replaced: a provider call produces an answer, not a stored row.

    chatting/       one class per chat vendor (Anthropic, Cohere, Google, ...)
    embedding/      the same shape, for embedding vendors
    provider_cache  ProviderCache, so a chat client is built once per model
                     and reused rather than reconnected on every call
"""

from .provider_cache import ProviderCache

__all__ = [
    "ProviderCache",
]
