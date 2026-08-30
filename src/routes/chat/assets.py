"""Asset and document routes for a chat (notebook)."""

import hashlib
from urllib.parse import quote

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from controllers import DataController, ProcessController
from controllers.TextProcessingController import normalize_text, strip_nulls
from enums import AssetType
from exceptions import AssetNotFoundError, DuplicateAssetError, InvalidInputError
from models import Asset, AssetModel, ChatModel, ChunkModel, DataChunk, Project, ProjectModel, UserModel

from ..schemas import RenameAssetRequest, SelectSourcesRequest
from ._pages import located_from_metadata
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
from utils.metrics import INGEST_DOCUMENTS

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
async def asset_content(
    chat_id: str, asset_id: str, http_request: Request, download: bool = False
):
    """The source's own bytes.

    ``get_asset`` is the one read that does not project ``file_bytes`` away,
    which is exactly why it is used here and nowhere near the listings.

    ``download=true`` is the only thing that changes: everything else —
    lookup, ownership check, ETag — is identical whether the browser is about
    to render this inline or save it, so it is one route with one branch
    rather than two copies of the same logic.
    """
    db = http_request.app.db

    await ChatModel(db).get_chat(chat_id)
    asset = await AssetModel(db).get_asset(asset_id)

    if asset.project_id != chat_id:
        # Belongs to another notebook. Until this check existed, naming any
        # valid chat alongside any asset id returned that asset's bytes —
        # chat_id was validated and then thrown away. Reported as missing
        # rather than forbidden, so the reply does not confirm it exists
        # somewhere else. delete_asset has always done this.
        raise AssetNotFoundError(f"Asset {asset_id!r} is not in this notebook")

    if download:
        # RFC 5987 (filename*=), not a bare filename="...": this corpus
        # includes Arabic filenames, which plain quoting cannot carry
        # correctly in every browser. attachment, and the source's own name —
        # the citation/preview path below keeps asset_id, since that one must
        # never change what the browser's PDF viewer shows in its own tab.
        disposition = f"attachment; filename*=UTF-8''{quote(asset.name)}"
    else:
        # Inline, and named by id: a browser asked to render a PDF wants both.
        disposition = f'inline; filename="{asset.asset_id}"'

    # The bytes are a 25 MB read out of Postgres for a large PDF, and opening
    # a citation re-requests the same file every time. content_hash is already
    # stored, so it is a free ETag: the second open costs a 304 instead.
    etag = f'"{asset.content_hash}"' if asset.content_hash else None
    headers = {"Content-Disposition": disposition}

    if etag:
        headers["ETag"] = etag
        # private: this is a user's own document, never a shared cache's.
        headers["Cache-Control"] = "private, max-age=300"

        if http_request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)

    content = asset.file_bytes

    if asset.asset_type != AssetType.PDF and not download:
        # The inline preview shows the *sanitised* text — NFKC-normalised,
        # NUL-stripped, whitespace-collapsed — which is what was actually
        # split and embedded, not the raw upload. That match matters now
        # that citation highlighting exists: a chunk's start_index is an
        # offset into the sanitised text, so highlighting the raw bytes
        # instead would be off by however much sanitising shifted things —
        # invisible for plain ASCII, real the moment a document carries a
        # bidi control or an Arabic presentation form. download=true still
        # gets the untouched original; a download must return exactly what
        # was uploaded, not the version the pipeline reshaped internally.
        try:
            content = normalize_text(strip_nulls(content.decode("utf-8"))).encode("utf-8")
        except UnicodeDecodeError:
            # Ingest itself assumes UTF-8 (TextLoader, no encoding override),
            # so a stored TEXT/MARKDOWN asset should already be decodable —
            # this is a defensive fallback, not the expected path. Falling
            # back to the raw bytes keeps the preview working; it just will
            # not line up with a citation's highlight for this one asset.
            pass

    return Response(
        content=content,
        media_type=(
            "application/pdf"
            if asset.asset_type == AssetType.PDF
            else "text/plain; charset=utf-8"
        ),
        headers=headers,
    )


@assets_router.get("/chats/{chat_id}/assets/{asset_id}/chunks/{chunk_order}/locate")
async def locate_chunk(
    chat_id: str, asset_id: str, chunk_order: int, http_request: Request
):
    """Where one chunk sits in its source: page, and a highlight if one exists.

    Fetched on click, not embedded in the citation — a citation is persisted
    into every future reload of the conversation, and highlight rectangles
    (dozens of floats per chunk) belong to the chunk row, which a re-ingest
    can correct, not frozen into a message that cannot.

    ``highlight`` is ``null`` for any chunk ingested before PDF_LOADER=pymupdf
    captured word boxes. ``text_range`` is its equivalent for a TEXT/MARKDOWN
    source that never had a page at all: ``[start, end)`` into that asset's
    own *sanitised* text — the same text ``asset_content`` serves inline, and
    the same one ``start_index`` was measured against, so slicing it at these
    two numbers reproduces the cited passage exactly. ``text`` (the chunk's
    own content) is returned regardless, as a fallback for a chunk with
    neither — one predating this feature, on either kind of source.
    """
    db = http_request.app.db

    await ChatModel(db).get_chat(chat_id)
    asset = await AssetModel(db).get_asset(asset_id)

    if asset.project_id != chat_id:
        # Same reasoning as asset_content: reported as missing, not
        # forbidden, so the reply never confirms the asset exists elsewhere.
        raise AssetNotFoundError(f"Asset {asset_id!r} is not in this notebook")

    chunks = await ChunkModel(db).get_chunks_by_orders(asset_id, [chunk_order])
    chunk = chunks.get(chunk_order)

    if chunk is None:
        raise AssetNotFoundError(
            f"Chunk {chunk_order} of asset {asset_id!r} was not found"
        )

    metadata = chunk.chunk_metadata or {}
    located = located_from_metadata(metadata) or {}

    text_range = None
    if asset.asset_type in (AssetType.TEXT, AssetType.MARKDOWN):
        start = metadata.get("start_index")
        # `>= 0`, not truthiness: 0 is the first chunk of the document, and
        # enforce_size's rebase (TextProcessingController) marks "could not
        # be located" with -1, which must not be read as a real offset.
        if isinstance(start, int) and start >= 0:
            text_range = [start, start + len(chunk.chunk_content)]

    return JSONResponse(
        status_code=200,
        content={
            "page_number": located.get("page_number"),
            "page_label": located.get("page_label"),
            "highlight": metadata.get("highlight"),
            "text_range": text_range,
            "text": chunk.chunk_content,
        },
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

        INGEST_DOCUMENTS.labels("ok").inc()

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

    except DuplicateAssetError:
        # Not a failure — the dedupe check doing its job. Counted separately so
        # a wall of duplicates does not read as an error rate.
        INGEST_DOCUMENTS.labels("duplicate").inc()
        raise

    except Exception:
        INGEST_DOCUMENTS.labels("failed").inc()
        raise

    finally:
        # However this ends — success, a bad PDF, a dead embedding model —
        # the bar must stop. A stale entry would leave the UI polling a
        # request that is no longer running. First, so a metric call can never
        # be the reason the bar sticks.
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
