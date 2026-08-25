"""Shared helpers for all chat sub-routers.

Centralised here so each domain module imports from one place rather than
copying the same three-line factory functions.
"""

import uuid
import json

from controllers import ChatController, NLPController
from fastapi import Request

from utils import get_logger

logger = get_logger("routes.chat")

# Splitter settings for documents attached through the UI.
#
# 1000 rather than 500: chunk count is the row count of the ingest INSERT and
# the number of texts sent to the embedding model, so halving it halves both.
# A 222-page book went from ~1700 chunks to ~850 on this change alone.
CHAT_CHUNK_SIZE = 1000
CHAT_CHUNK_OVERLAP = 50


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
    _INDEXING[chat_id] = {"filename": filename, "stage": "extracting", "done": 0, "total": 0}


def indexing_stage(chat_id: str, stage: str, done: int = 0, total: int = 0) -> None:
    entry = _INDEXING.get(chat_id)
    if entry is not None:
        entry.update(stage=stage, done=done, total=total)


def indexing_finish(chat_id: str) -> None:
    _INDEXING.pop(chat_id, None)


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
