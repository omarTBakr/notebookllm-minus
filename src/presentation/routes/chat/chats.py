"""Chat (notebook) CRUD routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps
from shared.utils import default_chat_model, default_embedding_model, get_settings

from ..schemas import CreateChatRequest, RenameChatRequest
from ._helpers import CHAT_CHUNK_OVERLAP, CHAT_CHUNK_SIZE

chats_router = APIRouter()


@chats_router.post("/sessions/{session_id}/chats")
async def create_chat(session_id: str, request: CreateChatRequest, http_request: Request):

    chat = await deps.conversations(http_request).create_chat(session_id, request.title, request.lang)

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat.chat_id,
            "session_id": session_id,
            "title": chat.title,
            "lang": chat.lang,
            "has_documents": False,
            "created_at": chat.created_at.isoformat(),
        },
    )


@chats_router.get("/sessions/{session_id}/chats")
async def list_chats(session_id: str, http_request: Request):

    chats = [
        {
            "chat_id": c.chat_id,
            "title": c.title,
            "lang": c.lang,
            "has_documents": c.has_documents,
            "created_at": c.created_at.isoformat(),
        }
        for c in await deps.conversations(http_request).list_chats(session_id)
    ]

    return JSONResponse(status_code=200, content={"session_id": session_id, "chats": chats})


@chats_router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, http_request: Request):
    """One chat, with whether it can actually answer from documents."""

    chat = await deps.conversations(http_request).get_chat(chat_id)

    # Asked of the vector index, not of chat.has_documents — see
    # ChatService.is_grounded.
    grounded = await deps.chat_service(http_request, chat).is_grounded(chat_id)

    settings = get_settings()

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat.chat_id,
            "session_id": chat.session_id,
            "user_id": chat.user_id,
            "title": chat.title,
            "lang": chat.lang,
            "has_documents": chat.has_documents,
            "grounded": grounded,
            # Qualified, because that is what the picker matches against —
            # a raw .env value spells the same model differently and shows
            # up in the UI as "Missing".
            "generation_model": chat.generation_model or default_chat_model(settings),
            "embedding_model": chat.embedding_model or default_embedding_model(settings),
            "embedding_dimensions": (chat.embedding_dimensions or settings.EMBEDDING_MODEL_SIZE),
            "temperature": (
                chat.temperature if chat.temperature is not None else settings.GENERATION_DEFAULT_TEMPERATURE
            ),
            "max_tokens": chat.max_tokens or settings.GENERATION_DEFAULT_MAX_TOKENS,
            "chunk_size": chat.chunk_size or CHAT_CHUNK_SIZE,
            "overlap_size": (chat.overlap_size if chat.overlap_size is not None else CHAT_CHUNK_OVERLAP),
            "web_search": chat.web_search,
            "highlight_color": chat.highlight_color,
            "excluded_assets": chat.excluded_assets,
            "created_at": chat.created_at.isoformat(),
        },
    )


@chats_router.get("/users/{user_id}/chats")
async def list_user_chats(user_id: str, http_request: Request):
    """Every notebook a profile owns, newest first.

    A flat list, not a session tree: the UI has no session concept, and going
    via sessions would mean one request for the list plus one per session.
    """
    chats = [
        {
            "chat_id": c.chat_id,
            "title": c.title,
            "lang": c.lang,
            "has_documents": c.has_documents,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        for c in await deps.conversations(http_request).list_user_chats(user_id)
    ]

    return JSONResponse(
        status_code=200,
        content={"user_id": user_id, "count": len(chats), "chats": chats},
    )


@chats_router.post("/users/{user_id}/chats")
async def create_user_chat(user_id: str, request: CreateChatRequest, http_request: Request):
    """Create a notebook under a profile, without the caller knowing about sessions.

    The UI's unit of work is the notebook; making it fetch a session id first
    would leak a layer it does not otherwise show.
    """
    chat = await deps.conversations(http_request).create_user_chat(user_id, request.title, request.lang)

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat.chat_id,
            "title": chat.title,
            "lang": chat.lang,
            "has_documents": False,
            "created_at": chat.created_at.isoformat(),
        },
    )


@chats_router.patch("/chats/{chat_id}")
async def rename_chat(chat_id: str, request: RenameChatRequest, http_request: Request):
    """Rename a notebook.

    Until now a title only changed as a side effect of the first question, so a
    notebook could never be named deliberately.
    """
    title = await deps.conversations(http_request).rename_chat(chat_id, request.title)

    return JSONResponse(status_code=200, content={"chat_id": chat_id, "title": title})
