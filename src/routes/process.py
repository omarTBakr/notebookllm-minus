from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import ProcessController
from enums import ProcessStatus
from exceptions import InvalidInputError, ProjectNotFoundError
from models import Asset, AssetModel, ChunkModel, DataChunk, Project, ProjectModel
from utils import get_logger
from .schemas import ProcessRequest

logger = get_logger(__name__)

process_router = APIRouter(prefix="/process", tags=["process"])


@process_router.post("/{project_id}")
async def process_data(
    project_id: str, request: ProcessRequest, http_request: Request
):
    """Fetch the asset(s) from MongoDB, process their bytes, and store chunks.

    - If ``request.asset_id`` is provided, only that asset is processed.
    - Otherwise every asset belonging to the project is processed.

    Extraction and chunking errors propagate to the handler in main.py.
    """
    logger.debug(
        "Process requested for project %r: asset_id=%r chunk_size=%s overlap=%s reset=%s",
        project_id,
        request.asset_id,
        request.chunk_size,
        request.overlap_size,
        request.reset,
    )

    # --- Instantiate DB models once (not per-asset) ---------------------------
    asset_model = AssetModel(http_request.app.db)
    project_model = ProjectModel(http_request.app.db)
    chunk_model = ChunkModel(http_request.app.db)
    # --------------------------------------------------------------------------

    # --- Resolve the list of assets to process --------------------------------
    if request.asset_id is not None:
        # Single-asset mode: fetch the concrete Asset object so the loop body
        # can access .file_bytes / .name just like in the multi-asset path.
        # get_asset raises AssetNotFoundError (404) if absent.
        assets: list[Asset] = [await asset_model.get_asset(request.asset_id)]
    else:
        # Multi-asset mode: consume the async generator into a list up-front so
        # that we can iterate it normally in the for-loop below.
        # iter_project_assets is deliberately unpaginated — get_assets_by_project
        # defaults to page_size=10 and would silently skip the 11th asset onward.
        assets = [item async for item in asset_model.iter_project_assets(project_id)]
    # --------------------------------------------------------------------------

    # --- Old single-asset fetch logic (kept for reference) --------------------
    # # get_asset raises AssetNotFoundError (404) if absent — no manual check needed.
    # if request.asset_id:
    #     asset = await asset_model.get_asset(request.asset_id)

    # # Guard: the asset must belong to the project in the URL.
    # if asset and asset.project_id != project_id:
    #     raise InvalidInputError(
    #         f"Asset {request.asset_id!r} does not belong to project {project_id!r}"
    #     )

    # if not asset.file_bytes:
    #     raise InvalidInputError(
    #         f"Asset {request.asset_id!r} has no stored file content to process"
    #     )
    # --------------------------------------------------------------------------

    # --- Validate the whole batch before writing anything ---------------------
    # Nothing to do is not a success: an unknown project and an empty one both
    # used to return 200 with assets_processed: 0.
    if not assets:
        raise ProjectNotFoundError(
            f"Project {project_id!r} has no assets to process"
        )

    # Checked up front, for every asset, so a bad request fails before a single
    # chunk is written rather than half way through the batch.
    for asset in assets:
        # Guard: the asset must belong to the project in the URL, or one
        # project's content ends up filed under another.
        if asset.project_id != project_id:
            raise InvalidInputError(
                f"Asset {asset.asset_id!r} does not belong to project {project_id!r}"
            )

        if not asset.file_bytes:
            raise InvalidInputError(
                f"Asset {asset.asset_id!r} has no stored file content to process"
            )
    # --------------------------------------------------------------------------

    # --- Project: upserted once, never per asset ------------------------------
    # The project first: chunks reference the project's Mongo _id, so it has to
    # exist before they can be built.
    project = Project(
        project_id=project_id,
        name=assets[0].name,
        description=f"Project {project_id}",
    )
    project_object_id = await project_model.update_project(project)

    # One controller for the whole request — the parameters never change per asset.
    process_controller = ProcessController(request.chunk_size, request.overlap_size)
    # --------------------------------------------------------------------------

    results: list[dict] = []
    processed_count = 0
    skipped_count = 0

    for asset in assets:
        # --- Idempotency: an asset is chunked once ----------------------------
        # Both branches are scoped to *this* asset. A project-wide reset here
        # would delete the other assets' chunks without re-creating them, which
        # is exactly what happens if you reset while processing a single one.
        if request.reset:
            removed_ids = await chunk_model.delete_chunks_for_asset(
                project_object_id, asset.asset_id
            )
            # Pull just these ids from the project doc; the other assets' stay.
            await project_model.remove_chunk_ids(project_id, removed_ids)
            if removed_ids:
                logger.info(
                    "Reset asset %r: removed %d existing chunk(s)",
                    asset.asset_id,
                    len(removed_ids),
                )
        elif await chunk_model.has_asset_chunks(project_object_id, asset.asset_id):
            # Already ingested. Re-chunking would duplicate every passage, so
            # the caller has to ask for it explicitly with reset=true.
            logger.info(
                "Skipping asset %r (%s): already chunked", asset.asset_id, asset.name
            )
            results.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_name": asset.name,
                    "project_object_id": str(project_object_id),
                    "status": "skipped",
                    "reason": "already chunked; pass reset=true to re-ingest",
                    "chunks_created": 0,
                    "chunks_saved": 0,
                    "chunks": [],
                }
            )
            skipped_count += 1
            continue
        # ----------------------------------------------------------------------

        # --- Process the bytes through the controller -------------------------
        # process_bytes writes to a named temp file (keyed on the asset's
        # extension) so the existing loader/splitter logic is reused unchanged.
        # Awaited onto a thread: this loop is per asset, so a multi-asset
        # project would otherwise hold the event loop for the sum of them.
        chunked_docs = await process_controller.process_and_split(
            asset.file_bytes, asset.name
        )
        # ----------------------------------------------------------------------

        # --- Persist chunks to MongoDB ----------------------------------------
        # chunk_order is the position within *this* document, so asset_id is what
        # keeps two sources in one project from claiming the same orders.
        chunks = [
            DataChunk(
                project_id=project_object_id,
                asset_id=asset.asset_id,
                chunk_order=order,
                chunk_content=doc.page_content,
                chunk_metadata=doc.metadata,
            )
            for order, doc in enumerate(chunked_docs)
        ]
        inserted_ids = await chunk_model.create_chunks(chunks)

        # Register the new chunk _ids on the project document.
        await project_model.add_chunk_ids(project_id, inserted_ids)
        # ----------------------------------------------------------------------

        processed_count += 1
        results.append(
            {
                "asset_id": asset.asset_id,
                "asset_name": asset.name,
                "project_object_id": str(project_object_id),
                "status": "processed",
                "chunks_created": len(chunked_docs),
                "chunks_saved": len(inserted_ids),
                "chunks": [
                    {"content": doc.page_content, "metadata": doc.metadata}
                    for doc in chunked_docs
                ],
            }
        )

    return JSONResponse(
        status_code=200,
        content={
            "project_id": project_id,
            "chunk_size": request.chunk_size,
            "overlap_size": request.overlap_size,
            "status": ProcessStatus.PROCESSING_SUCCESS.value,
            "reset": request.reset,
            "assets_found": len(assets),
            "assets_processed": processed_count,
            "assets_skipped": skipped_count,
            "results": results,
        },
    )
