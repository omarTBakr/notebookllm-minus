"""Conversations: users, sessions, chats, documents, and the answer stream.

Identity is deliberately thin — a user is an opaque uuid the browser keeps.
There is no sign-in, no password and no cookie; this exists to scope
conversations, not to prove who anyone is.

Sub-modules
-----------
users    — profile CRUD
sessions — session CRUD + default_session helper
chats    — notebook CRUD
assets   — document upload, source selection, asset preview
models   — model catalogue + per-chat model / settings tuning
messages — message listing + streaming answer endpoint
"""

from fastapi import APIRouter

from .assets import assets_router
from .chats import chats_router
from .messages import messages_router
from .models import models_router
from .sessions import sessions_router
from .users import users_router

chat_router = APIRouter(prefix="/chat", tags=["chat"])

chat_router.include_router(users_router)
chat_router.include_router(sessions_router)
chat_router.include_router(chats_router)
chat_router.include_router(assets_router)
chat_router.include_router(models_router)
chat_router.include_router(messages_router)

__all__ = ["chat_router"]
