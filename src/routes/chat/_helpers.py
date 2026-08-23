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
CHAT_CHUNK_SIZE = 500
CHAT_CHUNK_OVERLAP = 50


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
