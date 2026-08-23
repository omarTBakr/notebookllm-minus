"""Asset and document routes for a chat (notebook)."""

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from controllers import DataController, ProcessController
from enums import AssetType
from exceptions import InvalidInputError
from models import Asset, AssetModel, ChatModel, ChunkModel, DataChunk, Project, ProjectModel, UserModel

from ..schemas import RenameAssetRequest, SelectSourcesRequest
from ._helpers import _new_id, _nlp_controller, CHAT_CHUNK_SIZE, CHAT_CHUNK_OVERLAP
from utils import get_settings

assets_router = APIRouter()


@assets_router.get("/chats/{chat_id}/assets")
async def list_chat_assets(chat_id: str, http_request: Request):
    """The sources attached to one notebook.

    chat_id *is* project_id, so this is a single query on the assets a
    notebook's documents were filed under.
    """
    db = http_request.app.db

    chat = await ChatModel(db).get_chat(chat_id)
    excluded = set(chat.excluded_assets)

    assets = [
        {
            "asset_id": a.asset_id,
            "name": a.name,
            "asset_type": a.asset_type.value,
            # Whether this source is searched when a question is asked.
            "selected": a.asset_id not in excluded,
            "created_at": a.created_at.isoformat(),
        }
        async for a in AssetModel(db).iter_assets_for_projects([chat_id])
    ]

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "count": len(assets),
            "selected_count": sum(1 for a in assets if a["selected"]),
            "assets": assets,
        },
    )


@assets_router.patch("/chats/{chat_id}/sources")
async def select_sources(chat_id: str, request: SelectSourcesRequest, http_request: Request):
    """Choose which sources this notebook answers from."""

    chat_model = ChatModel(http_request.app.db)

    await chat_model.get_chat(chat_id)
    await chat_model.set_settings(chat_id, {"excluded_assets": request.excluded_assets})

    return JSONResponse(
        status_code=200,
        content={"chat_id": chat_id, "excluded_assets": request.excluded_assets},
    )


@assets_router.patch("/chats/{chat_id}/assets/{asset_id}")
async def rename_asset(
    chat_id: str, asset_id: str, request: RenameAssetRequest, http_request: Request
):
    """Rename a source.

    Only the asset document changes. The name is copied into chunk metadata
    and Qdrant payloads at index time, but nothing reads those copies for
    display any more — citations resolve the name through ``asset_id`` — so
    there is nothing to cascade.
    """
    db = http_request.app.db

    await ChatModel(db).get_chat(chat_id)
    await AssetModel(db).rename(asset_id, request.name)

    return JSONResponse(
        status_code=200,
        content={"chat_id": chat_id, "asset_id": asset_id, "name": request.name},
    )


@assets_router.get("/chats/{chat_id}/assets/{asset_id}/content")
async def asset_content(chat_id: str, asset_id: str, http_request: Request):
    """The source's own bytes, for previewing it in the UI.

    ``get_asset`` is the one read that does not project ``file_bytes`` away,
    which is exactly why it is used here and nowhere near the listings.
    """
    db = http_request.app.db

    await ChatModel(db).get_chat(chat_id)
    asset = await AssetModel(db).get_asset(asset_id)

    return Response(
        content=asset.file_bytes,
        media_type=(
            "application/pdf"
            if asset.asset_type == AssetType.PDF
            else "text/plain; charset=utf-8"
        ),
        # Inline, and named: a browser asked to render a PDF wants both.
        headers={"Content-Disposition": f'inline; filename="{asset.asset_id}"'},
    )


@assets_router.post("/chats/{chat_id}/documents")
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
        chunk_size=chat.chunk_size or CHAT_CHUNK_SIZE,
        chunk_overlap=(
            chat.overlap_size if chat.overlap_size is not None else CHAT_CHUNK_OVERLAP
        ),
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

    from ._helpers import logger
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


@assets_router.get("/users/{user_id}/assets")
async def list_user_assets(user_id: str, http_request: Request):
    """Every document this user has uploaded, across all their chats.

    Because chat_id *is* project_id, a user's chat ids are exactly the project
    ids their assets are filed under — one lookup, then one query.
    """
    db = http_request.app.db

    await UserModel(db).get_user(user_id)

    chats = {c.chat_id: c async for c in ChatModel(db).iter_user_chats(user_id)}

    assets = []

    async for asset in AssetModel(db).iter_assets_for_projects(list(chats)):
        chat = chats.get(asset.project_id)
        assets.append(
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "asset_type": asset.asset_type.value,
                "chat_id": asset.project_id,
                "chat_title": chat.title if chat else None,
                "created_at": asset.created_at.isoformat(),
            }
        )

    return JSONResponse(
        status_code=200,
        content={"user_id": user_id, "count": len(assets), "assets": assets},
    )
