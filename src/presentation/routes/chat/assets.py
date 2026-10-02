"""Asset and document routes for a chat (notebook)."""

from urllib.parse import quote

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from application.services import SourceService
from application.services.ingest.tabular import xlsx_preview_text
from application.services.ingest.TextProcessingService import (
    normalize_text,
    strip_nulls,
)
from application.services.rag.citations import located_from_metadata
from presentation import dependencies as deps
from shared.enums import IN_FLIGHT, AssetType
from shared.exceptions import DuplicateAssetError, InvalidFileError, InvalidInputError
from shared.utils import get_settings
from shared.utils.metrics import INGEST_DOCUMENTS

from ..schemas import AddLinkRequest, RenameAssetRequest, SelectSourcesRequest
from ._helpers import (
    CHAT_CHUNK_OVERLAP,
    CHAT_CHUNK_SIZE,
)

assets_router = APIRouter()


async def _drain(file: UploadFile) -> bytes:
    """Every byte of an upload, read in bounded pieces.

    Bounded rather than a single `.read()`: the piece size is the most memory
    one in-flight upload adds at a time, and MAX_FILE_CHUNK_SIZE is what .env
    sets it with.
    """
    settings = get_settings()
    piece_size = settings.MAX_FILE_CHUNK_SIZE
    content = bytearray()

    try:
        while True:
            piece = await file.read(piece_size)
            if not piece:
                break

            content.extend(piece)
            if len(content) > settings.MAX_FILE_SIZE:
                raise InvalidFileError(f"file size exceeds the {settings.MAX_FILE_SIZE} byte limit")
    finally:
        await file.close()

    return bytes(content)


@assets_router.get("/chats/{chat_id}/assets")
async def list_chat_assets(chat_id: str, http_request: Request):
    """The sources attached to one notebook.

    chat_id *is* project_id, so this is a single query on the assets a
    notebook's documents were filed under.
    """
    chat, found = await deps.sources(http_request).list_sources(chat_id)
    excluded = set(chat.excluded_assets)

    assets = [
        {
            "asset_id": a.asset_id,
            "name": a.name,
            "asset_type": a.asset_type.value,
            # The link a source was added from, or "" for an upload. The
            # panel badges it and the preview offers "open original" from it.
            "source_url": a.source_url,
            # Whether this source is searched when a question is asked.
            "selected": a.asset_id not in excluded,
            "created_at": a.created_at.isoformat(),
        }
        for a in found
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
    await deps.sources(http_request).select(chat_id, request.excluded_assets)

    return JSONResponse(
        status_code=200,
        content={"chat_id": chat_id, "excluded_assets": request.excluded_assets},
    )


@assets_router.patch("/chats/{chat_id}/assets/{asset_id}")
async def rename_asset(chat_id: str, asset_id: str, request: RenameAssetRequest, http_request: Request):
    """Rename a source.

    Only the asset document changes. The name is copied into chunk metadata
    and Qdrant payloads at index time, but nothing reads those copies for
    display any more — citations resolve the name through ``asset_id`` — so
    there is nothing to cascade.
    """
    await deps.sources(http_request).rename(chat_id, asset_id, request.name)

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
    result = await deps.sources(http_request).delete(chat_id, asset_id)

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "asset_id": asset_id,
            **result,
        },
    )


@assets_router.get("/chats/{chat_id}/assets/{asset_id}/content")
async def asset_content(chat_id: str, asset_id: str, http_request: Request, download: bool = False):
    """The source's own bytes.

    ``get_asset`` is the one read that does not project ``file_bytes`` away,
    which is exactly why it is used here and nowhere near the listings.

    ``download=true`` is the only thing that changes: everything else —
    lookup, ownership check, ETag — is identical whether the browser is about
    to render this inline or save it, so it is one route with one branch
    rather than two copies of the same logic.
    """
    _, asset = await deps.sources(http_request).owned(chat_id, asset_id)

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

    if asset.asset_type == AssetType.YOUTUBE:
        # The stored transcript, as stored: the preview needs every line's
        # start and end to build the player's transcript, which the ingested
        # text alone does not carry. Its text was normalised line by line at
        # ingest, so what the preview lists is what was cited.
        return Response(content=content, media_type="application/json", headers=headers)

    if asset.asset_type == AssetType.XLSX and not download:
        # A workbook is a zip archive: shown as text it is noise. The preview
        # gets its rows instead, headed by the `Sheet · row N` a citation names.
        return Response(
            content=xlsx_preview_text(content).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers=headers,
        )

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
        media_type=("application/pdf" if asset.asset_type == AssetType.PDF else "text/plain; charset=utf-8"),
        headers=headers,
    )


@assets_router.get("/chats/{chat_id}/assets/{asset_id}/chunks/{chunk_order}/locate")
async def locate_chunk(chat_id: str, asset_id: str, chunk_order: int, http_request: Request):
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
    asset, chunk = await deps.sources(http_request).locate(chat_id, asset_id, chunk_order)

    metadata = chunk.chunk_metadata or {}
    located = located_from_metadata(metadata) or {}

    text_range = None
    if asset.asset_type in (AssetType.TEXT, AssetType.MARKDOWN):
        start = metadata.get("start_index")
        # `>= 0`, not truthiness: 0 is the first chunk of the document, and
        # enforce_size's rebase (TextProcessingService) marks "could not
        # be located" with -1, which must not be read as a real offset.
        if isinstance(start, int) and start >= 0:
            text_range = [start, start + len(chunk.chunk_content)]

    return JSONResponse(
        status_code=200,
        content={
            "page_number": located.get("page_number"),
            "page_label": located.get("page_label"),
            # A spreadsheet chunk: its row (and sheet), null for everything else.
            "row": located.get("row"),
            "sheet": located.get("sheet"),
            "highlight": metadata.get("highlight"),
            "text_range": text_range,
            # [start_s, end_s] of video, for a transcript's chunk; null otherwise.
            "time_range": metadata.get("time_range"),
            "text": chunk.chunk_content,
        },
    )


@assets_router.post("/chats/{chat_id}/documents")
async def attach_document(chat_id: str, file: UploadFile, http_request: Request):
    """Store one document and queue its ingestion. Returns 202.

    The chat's id *is* the project id, so this reuses the existing pieces
    directly rather than calling the app's own HTTP endpoints.

    What is left here is the request: draining the upload, naming the
    notebook, and shaping the reply. Deciding whether these bytes are already
    in the notebook, and queueing the chain that ingests them, is
    `AssetIngestService` — neither needs a request, and both are worth a
    test that does not go through a client.

    Chunking and indexing are one chain rather than two queued steps, which
    preserves what the old inline version guaranteed: a document is never left
    chunked-but-unindexed, the state where a chat looks grounded and retrieves
    nothing. What changed is where the work happens — the request no longer
    holds an API worker for the length of an embedding run, and progress is
    read from the task row instead of a dict private to one process.
    """
    sources = deps.sources(http_request)
    chat = await sources.get_chat(chat_id)

    deps.uploads(http_request).validate_file(file)

    try:
        file_bytes = await _drain(file)

        if not file_bytes:
            raise InvalidInputError(f"{file.filename!r} is empty")

        return await _ingest(
            sources,
            chat,
            filename=str(file.filename),
            content_type=file.content_type,
            file_bytes=file_bytes,
        )

    except DuplicateAssetError:
        # Not a failure — the dedupe check doing its job. Counted separately so
        # a wall of duplicates does not read as an error rate.
        INGEST_DOCUMENTS.labels("duplicate").inc()
        raise

    except Exception:
        INGEST_DOCUMENTS.labels("failed").inc()
        raise


@assets_router.post("/chats/{chat_id}/sources/url")
async def add_link(chat_id: str, request: AddLinkRequest, http_request: Request):
    """Add a source from a link: an online PDF, an article, a YouTube video or a Google Sheet.

    Returns 202 at once with the id of the task that fetches it. The fetch runs on
    a worker, which then attaches the bytes like an upload and queues the usual
    ingestion; the task reports back, on the same status endpoint, whatever went
    wrong with the link (unreachable, no transcript, not public, already attached)
    and the id of the ingestion that follows (`next_task_id`).
    """
    task_id = await deps.sources(http_request).queue_link(chat_id, request.url)

    return JSONResponse(
        status_code=202,
        content={"chat_id": chat_id, "task_id": task_id, "status": "queued", "filename": request.url},
    )


async def _ingest(
    sources: SourceService,
    chat,
    *,
    filename: str,
    content_type: str | None,
    file_bytes: bytes,
    asset_type: AssetType | None = None,
    content_hash: str | None = None,
    source_url: str = "",
) -> JSONResponse:
    """Store one source and queue its ingestion. Returns the 202 both routes send."""
    # Fixed splitter settings rather than request parameters: the UI attaches a
    # file with one click and has nowhere sensible to ask about tuning.
    # /process/{project_id} remains available for anyone who does.
    asset, queued = await sources.attach(
        chat,
        filename=filename,
        content_type=content_type,
        file_bytes=file_bytes,
        chunk_size=chat.chunk_size or CHAT_CHUNK_SIZE,
        overlap_size=chat.overlap_size if chat.overlap_size is not None else CHAT_CHUNK_OVERLAP,
        asset_type=asset_type,
        content_hash=content_hash,
        source_url=source_url,
    )

    return JSONResponse(
        # 202, not 200: the document is accepted and stored, but chunking
        # and embedding have not happened yet.
        status_code=202,
        content={
            "chat_id": chat.chat_id,
            "asset_id": asset.asset_id,
            "filename": filename,
            "task_id": queued.task_id,
            "index_task_id": queued.index_task_id,
            "status": "queued",
        },
    )


@assets_router.get("/chats/{chat_id}/indexing")
async def indexing_progress(chat_id: str, http_request: Request, task_id: str | None = None):
    """How far this chat's ingestion has got.

    Polled by the sources panel every few hundred milliseconds. It reads the
    task row rather than process memory, which is the whole point: the upload
    is handled by a worker now, and even before that the answer was wrong
    whenever the poll reached a different API process than the upload.

    *task_id* pins the answer to one run. Without it the most recent
    unfinished task for the chat is reported, which is what a page that
    reloaded mid-upload needs.
    """
    task = await deps.sources(http_request).indexing_task(chat_id, task_id)

    if task is None:
        return JSONResponse(status_code=200, content={"active": False})

    active = task.status in IN_FLIGHT

    return JSONResponse(
        status_code=200,
        content={
            "active": active,
            "task_id": task.task_id,
            "status": task.status.value,
            "stage": task.stage,
            "done": task.done,
            "total": task.total,
            # None while the total is still unknown, so the UI can tell
            # "no progress yet" from "0% done".
            "percent": round(100 * task.done / task.total) if task.total else None,
            "error": task.error,
            # A task that only *starts* the real work (fetching a link, then
            # queueing its ingestion) names the task that carries on, so the
            # reader keeps one progress bar from the paste to the last chunk
            # instead of seeing "done" while the indexing has not begun.
            "next_task_id": task.result.get("ingestion_task_id") or None,
        },
    )


@assets_router.get("/users/{user_id}/assets")
async def list_user_assets(user_id: str, http_request: Request):
    """Every document this user has uploaded, across all their chats.

    Because chat_id *is* project_id, a user's chat ids are exactly the project
    ids their assets are filed under — one lookup, then one query.
    """
    chats, found = await deps.sources(http_request).list_for_user(user_id)

    assets = []

    for asset in found:
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
