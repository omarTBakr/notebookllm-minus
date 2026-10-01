from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from presentation import dependencies as deps
from shared.enums import FileStatus
from shared.utils import get_logger, get_settings

SETTINGS = get_settings()
logger = get_logger(__name__)

data_router = APIRouter(prefix="/data", tags=["data"])


@data_router.post("/upload/{project_id}")
async def upload_data(project_id: str, file: UploadFile, request: Request):
    """Validation and storage errors propagate to the handler in main.py."""
    logger.debug(
        "Upload requested for project %r: %r (%s)",
        project_id,
        file.filename,
        file.content_type,
    )

    uploads = deps.uploads(request)

    uploads.validate_file(file)

    # Read in chunks (MAX_FILE_CHUNK_SIZE from .env) and accumulate.
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(SETTINGS.MAX_FILE_CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    await file.close()

    project_object_id, asset, asset_object_id = await uploads.store_upload(
        project_id, str(file.filename), file.content_type, file_bytes
    )

    return JSONResponse(
        status_code=200,
        content={
            "project_id": project_id,
            "project_db_id": str(project_object_id),
            "asset_id": asset.asset_id,
            "asset_db_id": str(asset_object_id),
            "status": FileStatus.UPLOADED.value,
            "filename": file.filename,
            "asset_type": asset.asset_type.value,
        },
    )
