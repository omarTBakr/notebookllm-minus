from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import ProcessController
from enums import ProcessStatus
from exceptions import InvalidInputError
from models import AssetModel, ChunkModel, DataChunk, Project, ProjectModel
from utils import get_logger
from .schemas import ProcessRequest

logger = get_logger(__name__)

process_router = APIRouter(prefix="/process", tags=["process"])


@process_router.post("/{project_id}")
async def process_data(
    project_id: str, request: ProcessRequest, http_request: Request
):
    """Fetch the asset from MongoDB, process its bytes, and store chunks.

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

    # --- Fetch the asset document from MongoDB --------------------------------
    asset_model = AssetModel(http_request.app.db)
    # get_asset raises AssetNotFoundError (404) if absent — no manual check needed.
    asset = await asset_model.get_asset(request.asset_id)

    # Guard: the asset must belong to the project in the URL.
    if asset.project_id != project_id:
        raise InvalidInputError(
            f"Asset {request.asset_id!r} does not belong to project {project_id!r}"
        )

    if not asset.file_bytes:
        raise InvalidInputError(
            f"Asset {request.asset_id!r} has no stored file content to process"
        )
    # --------------------------------------------------------------------------

    # --- Process the bytes through the controller -----------------------------
    process_controller = ProcessController(request.chunk_size, request.overlap_size)

    # process_bytes writes to a named temp file (keyed on the asset's extension)
    # so the existing loader/splitter logic is reused unchanged.
    docs = process_controller.process_bytes(asset.file_bytes, asset.name)
    chunked_docs = process_controller.split_file(docs)
    # --------------------------------------------------------------------------

    # --- Persist project + chunks to MongoDB ----------------------------------
    project_model = ProjectModel(http_request.app.db)
    chunk_model = ChunkModel(http_request.app.db)

    # The project first: chunks reference the project's Mongo _id, so it has to
    # exist before they can be built.
    project = Project(
        project_id=project_id,
        name=asset.name,
        description=f"Project for asset {asset.asset_id}",
    )
    project_object_id = await project_model.update_project(project)

    if request.reset:
        removed = await chunk_model.delete_project_chunks(project_object_id)
        # Keep chunks_ids in sync: clear the stale IDs from the project doc.
        await project_model.clear_chunk_ids(project_id)
        logger.info("Reset project %r: removed %d existing chunk(s)", project_id, removed)

    chunks = [
        DataChunk(
            project_id=project_object_id,
            chunk_order=order,
            chunk_content=doc.page_content,
            chunk_metadata=doc.metadata,
        )
        for order, doc in enumerate(chunked_docs)
    ]
    inserted_ids = await chunk_model.create_chunks(chunks)

    # Register the new chunk _ids on the project document.
    await project_model.add_chunk_ids(project_id, inserted_ids)
    # --------------------------------------------------------------------------

    return JSONResponse(
        status_code=200,
        content={
            "project_id": project_id,
            "asset_id": request.asset_id,
            "asset_name": asset.name,
            "chunk_size": request.chunk_size,
            "overlap_size": request.overlap_size,
            "status": ProcessStatus.PROCESSING_SUCCESS.value,
            "project_object_id": str(project_object_id),
            "chunks_created": len(chunked_docs),
            "chunks_saved": len(inserted_ids),
            "chunks": [
                {"content": doc.page_content, "metadata": doc.metadata}
                for doc in chunked_docs
            ],
            "reset": request.reset,
        },
    )
