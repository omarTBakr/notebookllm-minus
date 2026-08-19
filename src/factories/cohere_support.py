"""Shutdown helper shared by the two Cohere providers.

``cohere.AsyncClientV2`` exposes no ``close()``. The real ``httpx.AsyncClient``
sits behind ``_client_wrapper.httpx_client.httpx_client`` — private, and nested
under two attributes that happen to share a name. Rather than hardcode that
path in both providers, walk down it defensively: if a future SDK version
changes the nesting, closing becomes a no-op instead of an AttributeError at
shutdown.
"""

from utils import get_logger

logger = get_logger(__name__)

_MAX_DEPTH = 4


async def aclose_cohere_client(client) -> None:
    """Close the httpx pool behind a Cohere async client, if it can be found."""

    node = getattr(client, "_client_wrapper", None)

    for _ in range(_MAX_DEPTH):

        if node is None:
            break

        if hasattr(node, "aclose"):
            await node.aclose()
            return

        node = getattr(node, "httpx_client", None)

    # Reached only if the SDK moved the client. Harmless at process exit, but
    # worth a line: it means a pool is being left to the garbage collector.
    logger.debug(
        "Could not locate the httpx client inside %s; connection pool left unclosed",
        type(client).__name__,
    )
