"""Chat (notebook) CRUD routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models import Chat, ChatModel, SessionModel, UserModel

from ..schemas import CreateChatRequest, RenameChatRequest
from ._helpers import _new_id, _chat_controller, CHAT_CHUNK_SIZE, CHAT_CHUNK_OVERLAP
from .sessions import default_session
from utils import get_settings

chats_router = APIRouter()


@chats_router.post("/sessions/{session_id}/chats")
async def create_chat(session_id: str, request: CreateChatRequest, http_request: Request):

    session = await SessionModel(http_request.app.db).get_session(session_id)

    chat = Chat(
        chat_id=_new_id(),
        session_id=session_id,
        user_id=session.user_id,
        title=request.title,
        lang=request.lang,
    )

    await ChatModel(http_request.app.db).create_chat(chat)

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

    await SessionModel(http_request.app.db).get_session(session_id)

    chats = [
        {
            "chat_id": c.chat_id,
            "title": c.title,
            "lang": c.lang,
            "has_documents": c.has_documents,
            "created_at": c.created_at.isoformat(),
        }
        async for c in ChatModel(http_request.app.db).iter_session_chats(session_id)
    ]

    return JSONResponse(
        status_code=200, content={"session_id": session_id, "chats": chats}
    )


@chats_router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, http_request: Request):
    """One chat, with whether it can actually answer from documents."""

    chat = await ChatModel(http_request.app.db).get_chat(chat_id)

    # Asked of the vector index, not of chat.has_documents — see
    # ChatController.is_grounded.
    grounded = await _chat_controller(http_request, chat).is_grounded(chat_id)

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
            "generation_model": chat.generation_model or settings.GENERATION_MODEL_ID,
            "embedding_model": chat.embedding_model or settings.EMBEDDING_MODEL_ID,
            "embedding_dimensions": (
                chat.embedding_dimensions or settings.EMBEDDING_MODEL_SIZE
            ),
            "temperature": (
                chat.temperature
                if chat.temperature is not None
                else settings.GENERATION_DEFAULT_TEMPERATURE
            ),
            "max_tokens": chat.max_tokens or settings.GENERATION_DEFAULT_MAX_TOKENS,
            "chunk_size": chat.chunk_size or CHAT_CHUNK_SIZE,
            "overlap_size": (
                chat.overlap_size if chat.overlap_size is not None else CHAT_CHUNK_OVERLAP
            ),
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
    db = http_request.app.db

    await UserModel(db).get_user(user_id)

    chats = [
        {
            "chat_id": c.chat_id,
            "title": c.title,
            "lang": c.lang,
            "has_documents": c.has_documents,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        async for c in ChatModel(db).iter_user_chats(user_id)
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
    db = http_request.app.db

    await UserModel(db).get_user(user_id)

    session_id = await default_session(db, user_id)

    chat = Chat(
        chat_id=_new_id(),
        session_id=session_id,
        user_id=user_id,
        title=request.title,
        lang=request.lang,
    )

    await ChatModel(db).create_chat(chat)

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
    chat_model = ChatModel(http_request.app.db)

    await chat_model.get_chat(chat_id)
    await chat_model.rename(chat_id, request.title.strip())

    return JSONResponse(
        status_code=200, content={"chat_id": chat_id, "title": request.title.strip()}
    )
