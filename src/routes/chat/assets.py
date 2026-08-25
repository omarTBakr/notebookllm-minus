"""Asset and document routes for a chat (notebook)."""

import hashlib

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from controllers import DataController, ProcessController
from enums import AssetType
from exceptions import AssetNotFoundError, DuplicateAssetError, InvalidInputError
from models import Asset, AssetModel, ChatModel, ChunkModel, DataChunk, Project, ProjectModel, UserModel

from ..schemas import RenameAssetRequest, SelectSourcesRequest
from ._helpers import (
    _new_id,
    _nlp_controller,
    CHAT_CHUNK_SIZE,
    CHAT_CHUNK_OVERLAP,
    indexing_finish,
    indexing_stage,
    indexing_start,
    indexing_status,
)
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


@assets_router.delete("/chats/{chat_id}/assets/{asset_id}")
async def delete_asset(chat_id: str, asset_id: str, http_request: Request):
    """Remove a source and everything derived from it.

    Three stores hold pieces of one document — the asset row, its chunks, and
    its vectors — and a source that is gone from the list while its vectors
    still answer questions is worse than one that was never deleted. They come
    out in derived-first order so a failure part-way through can be retried:
    vectors, then chunks, then the asset itself. The asset row is what the UI
    lists, so while it stands the delete is still visibly unfinished.
    """
    db = http_request.app.db

    chat = await ChatModel(db).get_chat(chat_id)
    asset = await AssetModel(db).get_asset(asset_id)

    if asset.project_id != chat_id:
        # Belongs to another notebook: report it the same way a missing one is
        # reported rather than confirming it exists somewhere else.
        raise AssetNotFoundError(f"Asset {asset_id!r} is not in this notebook")

    project_object_id = await ProjectModel(db).update_project(
        Project(project_id=chat_id, name=chat.title, description=f"Chat {chat.title}")
    )

    # --- vectors ---
    nlp = _nlp_controller(http_request, chat)
    collection = nlp.collection_name(chat_id)
    vectors_removed = 0

    if await db.vectors().collection_exists(collection):
        vectors_removed = await db.vectors().delete_by_metadata(
            collection_name=collection, key="asset_id", value=asset_id
        )

    # --- chunks ---
    removed_chunk_ids = await ChunkModel(db).delete_chunks_for_asset(
        project_object_id, asset_id
    )

    # --- the asset itself ---
    await AssetModel(db).delete_asset(asset_id)

    # A notebook with nothing left in it is not grounded any more, and the
    # composer reads this to decide whether an answer can cite anything.
    remaining = [a async for a in AssetModel(db).iter_assets_for_projects([chat_id])]
    if not remaining:
        await ChatModel(db).set_has_documents(chat_id, False)

    from ._helpers import logger
    logger.info(
        "Deleted asset %r from chat %r: %s chunk(s), %s vector(s)",
        asset_id,
        chat_id,
        len(removed_chunk_ids),
        vectors_removed,
    )

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "asset_id": asset_id,
            "name": asset.name,
            "chunks_deleted": len(removed_chunk_ids),
            "vectors_deleted": vectors_removed,
            "sources_remaining": len(remaining),
        },
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

    # From here on the UI can watch this request's progress; the finally at the
    # end clears it however this returns.
    indexing_start(chat_id, str(file.filename))

    try:
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

        # Identity is the bytes, not the name: asset_id is a fresh uuid every
        # time, and a file can be renamed or saved under another name. Checked
        # here, before the asset row exists, so a duplicate costs one indexed
        # lookup rather than a full chunk-and-embed that is then discarded.
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        asset_model = AssetModel(db)
        existing = await asset_model.find_by_content_hash(chat_id, content_hash)

        if existing is not None:
            raise DuplicateAssetError(f"{existing.name!r} is already in this notebook.")

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
            content_hash=content_hash,
        )
        asset_object_id = await asset_model.update_asset(asset)
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
        # Off the event loop: parsing and splitting are the long synchronous stretch
        # of this request, and this route is the one the UI calls for every upload.
        indexing_stage(chat_id, "chunking")
        chunked = await process_controller.process_and_split(asset.file_bytes, asset.name)

        indexing_stage(chat_id, "storing")
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
            # Embedding is the long pole — this is what the bar actually tracks.
            on_progress=lambda done, total: indexing_stage(chat_id, "indexing", done, total),
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

    finally:
        # However this ends — success, a bad PDF, a dead embedding model —
        # the bar must stop. A stale entry would leave the UI polling a
        # request that is no longer running.
        indexing_finish(chat_id)


@assets_router.get("/chats/{chat_id}/indexing")
async def indexing_progress(chat_id: str):
    """How far the document currently being attached to this chat has got.

    Polled by the sources panel while an upload is in flight. Deliberately
    does no database work: it is hit every few hundred milliseconds and only
    ever reads a dict this worker already holds.

    That this answers *at all* while an upload runs is the point — it only
    works because the ingest path awaits rather than blocking the event loop.
    """
    status = indexing_status(chat_id)

    if status is None:
        return JSONResponse(status_code=200, content={"active": False})

    total = status["total"]

    return JSONResponse(
        status_code=200,
        content={
            "active": True,
            **status,
            # None while the total is still unknown, so the UI can tell
            # "no progress yet" from "0% done".
            "percent": round(100 * status["done"] / total) if total else None,
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
