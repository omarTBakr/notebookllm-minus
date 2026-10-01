"""Shared helpers for all chat sub-routers: the splitter defaults, id minting and
the server-sent-event frame. Building services from a request lives in
`presentation/dependencies.py`."""

import json

from shared.utils import get_logger

logger = get_logger("presentation.routes.chat")

# Splitter settings for documents attached through the UI.
#
# 1000 rather than 500: chunk count is the row count of the ingest INSERT and
# the number of texts sent to the embedding model, so halving it halves both.
# A 222-page book went from ~1700 chunks to ~850 on this change alone.
CHAT_CHUNK_SIZE = 1000
# 200, not 50. The overlap is what carries a sentence spanning a chunk
# boundary into both chunks; at 50 it was smaller than most sentences, so a
# fact split across the seam belonged to neither chunk and could not be
# retrieved. 20% is the usual ratio and costs proportionally more chunks.
CHAT_CHUNK_OVERLAP = 200


def _sse(payload: dict) -> str:
    """One server-sent event frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
