"""Shared helpers for all chat sub-routers.

Centralised here so each domain module imports from one place rather than
copying the same three-line factory functions.
"""

import uuid
import json

from controllers import ChatController, NLPController
from fastapi import Request

from time import perf_counter

from utils import get_logger
from utils.metrics import INGEST_STAGE_SECONDS

logger = get_logger("routes.chat")

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


# --- indexing progress --------------------------------------------------------
# Attaching a document is one long request — extract, chunk, then embed every
# chunk through the model — so the browser sees nothing until it finishes. The
# route posts its position here as it goes and the UI polls for it.
#
# Keyed by chat because the asset id is minted inside the request: the client
# cannot poll for something whose id it will not learn until the response it is
# waiting on arrives. One upload at a time per notebook is what the UI allows.
#
# In-process and deliberately not persisted: it describes work happening in
# *this* worker right now and means nothing once the request ends.
_INDEXING: dict[str, dict] = {}


def indexing_start(chat_id: str, filename: str) -> None:
    _INDEXING[chat_id] = {
        "filename": filename,
        "stage": "extracting",
        "done": 0,
        "total": 0,
        # When the current stage began. Every transition closes the previous
        # stage's timer, so the histogram gets its duration without the route
        # having to wrap each step in its own measurement.
        "_since": perf_counter(),
    }


def indexing_stage(chat_id: str, stage: str, done: int = 0, total: int = 0) -> None:
    entry = _INDEXING.get(chat_id)
    if entry is None:
        return

    # A real transition closes the outgoing stage. Progress updates *within*
    # the indexing stage call this repeatedly with the same name, and those
    # must not each record a duration.
    if stage != entry["stage"]:
        _close_stage(entry)

    entry.update(stage=stage, done=done, total=total)


def _close_stage(entry: dict) -> None:
    """Record how long the stage that is ending took."""
    started = entry.get("_since")
    if started is not None:
        INGEST_STAGE_SECONDS.labels(entry["stage"]).observe(perf_counter() - started)
    entry["_since"] = perf_counter()


def indexing_finish(chat_id: str) -> None:
    entry = _INDEXING.pop(chat_id, None)
    # The last stage has no transition after it, so it is closed here — on the
    # failure path too, which is what makes a stalled stage visible.
    if entry is not None:
        _close_stage(entry)


def indexing_status(chat_id: str) -> dict | None:
    return _INDEXING.get(chat_id)


def _new_id() -> str:
    return str(uuid.uuid4())


def _nlp_controller(request: Request, chat=None) -> NLPController:
    """Retrieval stack, using the chat's embedding model when it names one."""
    embedding_client = request.app.providers.embedding(
        getattr(chat, "embedding_model", None),
        getattr(chat, "embedding_dimensions", None),
    )
    return NLPController(
        embedding_client=embedding_client,
        vectordb_client=request.app.db.vectors(),
    )


def _chat_controller(request: Request, chat=None) -> ChatController:
    """Answering stack, using the chat's own models when it names them.

    Falls back to the .env defaults per field, so a chat that only overrides
    its chat model still embeds with the configured one.
    """
    return ChatController(
        generation_client=request.app.providers.chatting(
            getattr(chat, "generation_model", None)
        ),
        nlp_controller=_nlp_controller(request, chat),
    )


def _sse(payload: dict) -> str:
    """One server-sent event frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
