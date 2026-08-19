"""Conversations: users, sessions, chats, documents, and the answer stream.

Identity is deliberately thin — a user is an opaque uuid the browser keeps.
There is no sign-in, no password and no cookie; this exists to scope
conversations, not to prove who anyone is.
"""

import json
import uuid

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from controllers import (
    ChatController,
    DataController,
    ModelController,
    NLPController,
    ProcessController,
)
from enums import AssetType, ChatRole
from exceptions import InvalidInputError, NotebookLLMError
from models import (
    Asset,
    AssetModel,
    Chat,
    ChatModel,
    ChunkModel,
    DataChunk,
    Message,
    MessageModel,
    Project,
    ProjectModel,
    Session,
    SessionModel,
    User,
    UserModel,
)
from utils import get_logger, get_settings

from .schemas import (
    CreateChatRequest,
    CreateSessionRequest,
    MessageRequest,
    SetModelsRequest,
)

logger = get_logger(__name__)

chat_router = APIRouter(prefix="/chat", tags=["chat"])

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
        vectordb_client=request.app.vectordb_client,
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


# --- users -------------------------------------------------------------------


@chat_router.post("/users")
async def create_user(http_request: Request):
    """Mint a new user. The browser keeps the id; nothing else identifies them."""
    user = User(user_id=_new_id(), label="")

    await UserModel(http_request.app.db).create_user(user)

    logger.debug("Created user %r", user.user_id)

    return JSONResponse(
        status_code=200,
        content={"user_id": user.user_id, "created_at": user.created_at.isoformat()},
    )


@chat_router.get("/users/{user_id}")
async def get_user(user_id: str, http_request: Request):
    """Confirm a returning user still exists.

    404 here is routine, not exceptional: the browser holds an id across a
    database wipe, and the UI treats the 404 as "start fresh".
    """
    user = await UserModel(http_request.app.db).get_user(user_id)

    return JSONResponse(
        status_code=200,
        content={"user_id": user.user_id, "created_at": user.created_at.isoformat()},
    )


# --- sessions ----------------------------------------------------------------


@chat_router.post("/users/{user_id}/sessions")
async def create_session(user_id: str, request: CreateSessionRequest, http_request: Request):

    await UserModel(http_request.app.db).get_user(user_id)

    session = Session(session_id=_new_id(), user_id=user_id, title=request.title)

    await SessionModel(http_request.app.db).create_session(session)

    return JSONResponse(
        status_code=200,
        content={
            "session_id": session.session_id,
            "user_id": user_id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
        },
    )


@chat_router.get("/users/{user_id}/sessions")
async def list_sessions(user_id: str, http_request: Request):

    await UserModel(http_request.app.db).get_user(user_id)

    sessions = [
        {
            "session_id": s.session_id,
            "title": s.title,
            "created_at": s.created_at.isoformat(),
        }
        async for s in SessionModel(http_request.app.db).iter_user_sessions(user_id)
    ]

    return JSONResponse(status_code=200, content={"user_id": user_id, "sessions": sessions})


# --- chats -------------------------------------------------------------------


@chat_router.post("/sessions/{session_id}/chats")
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


@chat_router.get("/sessions/{session_id}/chats")
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


@chat_router.get("/chats/{chat_id}")
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
            "title": chat.title,
            "lang": chat.lang,
            "has_documents": chat.has_documents,
            "grounded": grounded,
            "generation_model": chat.generation_model or settings.GENERATION_MODEL_ID,
            "embedding_model": chat.embedding_model or settings.EMBEDDING_MODEL_ID,
            "embedding_dimensions": (
                chat.embedding_dimensions or settings.EMBEDDING_MODEL_SIZE
            ),
            "created_at": chat.created_at.isoformat(),
        },
    )


@chat_router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, http_request: Request):

    await ChatModel(http_request.app.db).get_chat(chat_id)

    messages = [
        {
            "message_id": m.message_id,
            "role": m.role.value,
            "content": m.content,
            "citations": m.citations,
            "created_at": m.created_at.isoformat(),
        }
        async for m in MessageModel(http_request.app.db).iter_chat_messages(chat_id)
    ]

    return JSONResponse(
        status_code=200, content={"chat_id": chat_id, "messages": messages}
    )


# --- documents ---------------------------------------------------------------


@chat_router.post("/chats/{chat_id}/documents")
async def attach_document(chat_id: str, file: UploadFile, http_request: Request):
    """Upload, chunk and index one document into this chat, in a single call.

    The chat's id *is* the project id, so this reuses the existing pieces
    directly rather than calling the app's own HTTP endpoints. Doing all three
    steps here means a document can never be left chunked-but-unindexed, which
    is the state where a chat looks grounded and retrieves nothing.
    """
    settings = get_settings()
    db = http_request.app.db

    chat = await ChatModel(db).get_chat(chat_id)

    DataController().validate_file(file)

    chunks: list[bytes] = []
    while True:
        piece = await file.read(settings.MAX_FILE_CHUNK_SIZE)
        if not piece:
            break
        chunks.append(piece)
    file_bytes = b"".join(chunks)
    await file.close()

    if not file_bytes:
        raise InvalidInputError(f"{file.filename!r} is empty")

    # The project row backs the existing chunk/vector plumbing for this chat.
    project_model = ProjectModel(db)
    project_object_id = await project_model.update_project(
        Project(
            project_id=chat_id,
            name=str(file.filename),
            description=f"Chat {chat.title}",
        )
    )

    asset = Asset(
        asset_id=_new_id(),
        asset_type=AssetType.from_content_type(file.content_type),
        project_id=chat_id,
        name=str(file.filename),
        description=f"Attached to chat {chat_id}",
        file_bytes=file_bytes,
    )
    asset_object_id = await AssetModel(db).update_asset(asset)
    await project_model.add_asset_id(chat_id, asset_object_id)

    # --- chunk ---
    # Fixed here rather than exposed in the request: the UI attaches a file
    # with one click and has nowhere sensible to ask about splitter tuning.
    # /process/{project_id} remains available for anyone who wants to choose.
    process_controller = ProcessController(
        chunk_size=CHAT_CHUNK_SIZE, chunk_overlap=CHAT_CHUNK_OVERLAP
    )
    docs = process_controller.process_bytes(asset.file_bytes, asset.name)
    chunked = process_controller.split_file(docs)

    chunk_model = ChunkModel(db)
    inserted = await chunk_model.create_chunks(
        [
            DataChunk(
                project_id=project_object_id,
                asset_id=asset.asset_id,
                chunk_order=order,
                chunk_content=doc.page_content,
                chunk_metadata=doc.metadata,
            )
            for order, doc in enumerate(chunked)
        ]
    )
    await project_model.add_chunk_ids(chat_id, inserted)

    # --- embed + index ---
    nlp = _nlp_controller(http_request, chat)
    result = await nlp.index_chunks(
        chunk_model=chunk_model,
        project_object_id=project_object_id,
        project_id=chat_id,
        asset_id=asset.asset_id,
    )

    await ChatModel(db).set_has_documents(chat_id, True)

    logger.info(
        "Attached %r to chat %r: %d chunk(s) indexed",
        file.filename,
        chat_id,
        result["chunks_indexed"],
    )

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "asset_id": asset.asset_id,
            "filename": file.filename,
            "chunks_created": len(chunked),
            "chunks_indexed": result["chunks_indexed"],
            "collection": result["collection"],
            "vector_size": result["vector_size"],
        },
    )


# --- models ------------------------------------------------------------------


@chat_router.get("/models")
async def list_models(http_request: Request):
    """Installed models, split by what each can actually do.

    Embedding capability is probed rather than assumed — Ollama's tag list does
    not say, and the probe also reports the vector width a collection built
    with that model needs.
    """
    catalogue = await ModelController().catalogue()

    return JSONResponse(status_code=200, content=catalogue)


@chat_router.patch("/chats/{chat_id}/models")
async def set_chat_models(chat_id: str, request: SetModelsRequest, http_request: Request):
    """Point one chat at different models.

    Switching the embedding model **rebuilds this chat's index**: the vector
    width is fixed when a collection is created, so old vectors are unusable by
    the new model. The chunks are already in MongoDB, so the rebuild re-embeds
    them rather than asking for the documents again.
    """
    db = http_request.app.db
    chat_model = ChatModel(db)

    chat = await chat_model.get_chat(chat_id)

    dimensions = None

    if request.embedding_model:
        dimensions = await ModelController().embedding_dimensions(request.embedding_model)

        if not dimensions:
            raise InvalidInputError(
                f"{request.embedding_model!r} cannot produce embeddings — "
                "pick one from the embedding list at GET /chat/models"
            )

    await chat_model.set_models(
        chat_id,
        generation_model=request.generation_model,
        embedding_model=request.embedding_model,
        embedding_dimensions=dimensions,
    )

    reindexed = 0

    if request.embedding_model and chat.has_documents:
        chunk_model = ChunkModel(db)
        project = await ProjectModel(db).get_project(chat_id)

        # reset=True drops the old collection so the new one is created at the
        # new width. Without it every insert would be rejected for a dimension
        # mismatch — the failure mode EMBEDDING_MODEL_SIZE exists to prevent.
        result = await _nlp_controller(
            http_request, await chat_model.get_chat(chat_id)
        ).index_chunks(
            chunk_model=chunk_model,
            project_object_id=project.id,
            project_id=chat_id,
            reset=True,
        )
        reindexed = result["chunks_indexed"]

        logger.info(
            "Re-indexed chat %r under %r: %d chunk(s)",
            chat_id,
            request.embedding_model,
            reindexed,
        )

    updated = await chat_model.get_chat(chat_id)
    settings = get_settings()

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "generation_model": updated.generation_model or settings.GENERATION_MODEL_ID,
            "embedding_model": updated.embedding_model or settings.EMBEDDING_MODEL_ID,
            "embedding_dimensions": (
                updated.embedding_dimensions or settings.EMBEDDING_MODEL_SIZE
            ),
            "reindexed_chunks": reindexed,
        },
    )


# --- the answer --------------------------------------------------------------


def _sse(payload: dict) -> str:
    """One server-sent event frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@chat_router.post("/chats/{chat_id}/message")
async def send_message(chat_id: str, request: MessageRequest, http_request: Request):
    """Ask a question; stream the answer back as server-sent events.

    Frames: one ``meta`` (grounded flag + citations), then ``delta`` per piece
    of text, then ``done``.
    """
    settings = get_settings()
    db = http_request.app.db

    chat = await ChatModel(db).get_chat(chat_id)
    message_model = MessageModel(db)

    logger.debug("Message in chat %r (lang=%s)", chat_id, chat.lang)

    # History must be read *before* the new question is stored, or the question
    # arrives in the model's context twice — once as history, once as the prompt.
    history = await message_model.get_recent_history(chat_id, settings.CHAT_HISTORY_LIMIT)

    await message_model.create_message(
        Message(
            message_id=_new_id(),
            chat_id=chat_id,
            role=ChatRole.USER,
            content=request.text,
        )
    )

    # Name the chat after its first question, so the sidebar isn't a column of
    # "New chat".
    if not history:
        title = request.text.strip()[:60]
        if title:
            await ChatModel(db).rename(chat_id, title)

    controller = _chat_controller(http_request, chat)
    top_k = request.top_k or settings.RETRIEVAL_TOP_K

    async def events():
        answer: list[str] = []
        citations: list[dict] = []

        try:
            async for event in controller.answer_stream(
                chat_id=chat_id,
                question=request.text,
                lang=chat.lang,
                history=history,
                top_k=top_k,
            ):
                if event["type"] == "meta":
                    citations = event["citations"]
                elif event["type"] == "delta":
                    answer.append(event["text"])

                yield _sse(event)

        except NotebookLLMError as exc:
            # The response has already started, so this cannot become an HTTP
            # error code — the status line went out with the first byte. The
            # failure is reported in-band and logged here, the one place that
            # departs from "raise and let the handler log it".
            logger.warning("Streaming answer failed for chat %r: %s", chat_id, exc)
            yield _sse({"type": "error", "detail": str(exc)})

        except Exception as exc:
            logger.exception("Unexpected failure streaming chat %r", chat_id)
            yield _sse({"type": "error", "detail": "Internal server error"})

        finally:
            # Persist whatever was produced. A half-finished answer is still
            # worth keeping: the user saw it, so it should survive a reload.
            text = "".join(answer)
            if text:
                await message_model.create_message(
                    Message(
                        message_id=_new_id(),
                        chat_id=chat_id,
                        role=ChatRole.ASSISTANT,
                        content=text,
                        citations=citations,
                    )
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream if this ever sits behind one.
            "X-Accel-Buffering": "no",
        },
    )
