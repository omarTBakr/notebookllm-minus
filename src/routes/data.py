import uuid

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from controllers import DataController, FileController
from enums import AssetType, FileStatus
from models import Asset, AssetModel, Project, ProjectModel
from utils import get_logger, get_settings

SETTINGS = get_settings()
logger = get_logger(__name__)

file_controller = FileController()
data_controller = DataController()
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

    data_controller.validate_file(file)

    # --- OLD: save to disk ---------------------------------------------------
    # file_path = await file_controller.save_file(project_id, file)
    # -------------------------------------------------------------------------

    # --- NEW: read in chunks (MAX_FILE_CHUNK_SIZE from .env) and accumulate --
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(SETTINGS.MAX_FILE_CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    await file.close()
    # -------------------------------------------------------------------------

    # Upsert the project record first so the asset has a valid project_id to
    # reference. created_at/updated_at come from the model's UTC defaults.
    project_model = ProjectModel(request.app.db)
    project = Project(
        project_id=project_id,
        name=str(file.filename),
        description=f"Project {file.filename}",
    )
    # update_project raises StorageError if the write fails, so reaching the
    # next line means it succeeded — there is no falsy "failed" return to check.
    project_object_id = await project_model.update_project(project)

    # Build and persist the asset document.
    asset_model = AssetModel(request.app.db)
    asset = Asset(
        asset_id=str(uuid.uuid4()),
        asset_type=AssetType.from_content_type(file.content_type),
        project_id=project_id,
        name=str(file.filename),
        description=f"Uploaded file for project {project_id}",
        file_bytes=file_bytes,
    )
    asset_object_id = await asset_model.update_asset(asset)

    # Register the asset's _id on the project so assets_ids stays in sync.
    await project_model.add_asset_id(project_id, asset_object_id)

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
