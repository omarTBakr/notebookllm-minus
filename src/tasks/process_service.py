from controllers import ProcessController
from enums import ProcessStatus, TaskStage
from exceptions import InvalidInputError, ProjectNotFoundError
from models import Asset, AssetModel, ChunkModel, DataChunk, Project, ProjectModel
from utils import get_logger

logger = get_logger(__name__)


async def process_data(project_id: str, request, db, recorder=None) -> dict:
    """Process a project's assets using an already-connected database.

    *recorder* is optional and every call on it is a no-op when absent, so the
    synchronous and direct-call paths behave exactly as before. When present
    it reports the same four stage names the browser upload used to publish
    from its in-process dict.
    """

    async def stage(name: str, done: int = 0, total: int = 0) -> None:
        if recorder is not None:
            await recorder.stage(name, done, total)

    asset_model = AssetModel(db)
    project_model = ProjectModel(db)
    chunk_model = ChunkModel(db)

    if request.asset_id is not None:
        assets: list[Asset] = [await asset_model.get_asset(request.asset_id)]
    else:
        assets = [item async for item in asset_model.iter_project_assets(project_id)]

    if not assets:
        message = f"Project {project_id!r} has no assets to process"
        raise ProjectNotFoundError(message)

    for asset in assets:
        if asset.project_id != project_id:
            message = f"Asset {asset.asset_id!r} does not belong to project {project_id!r}"
            raise InvalidInputError(message)
        if not asset.file_bytes:
            message = f"Asset {asset.asset_id!r} has no stored file content to process"
            raise InvalidInputError(message)

    project = Project(
        project_id=project_id,
        name=assets[0].name,
        description=f"Project {project_id}",
    )
    project_object_id = await project_model.update_project(project)
    process_controller = ProcessController(request.chunk_size, request.overlap_size)

    await stage(TaskStage.EXTRACTING.value, 0, len(assets))

    results: list[dict] = []
    processed_count = 0
    skipped_count = 0

    for asset in assets:
        if request.reset:
            removed_ids = await chunk_model.delete_chunks_for_asset(
                project_object_id,
                asset.asset_id,
            )
            await project_model.remove_chunk_ids(project_id, removed_ids)
            if removed_ids:
                logger.info(
                    "Reset asset %r: removed %d existing chunk(s)",
                    asset.asset_id,
                    len(removed_ids),
                )
        elif await chunk_model.has_asset_chunks(project_object_id, asset.asset_id):
            logger.info(
                "Skipping asset %r (%s): already chunked",
                asset.asset_id,
                asset.name,
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
                },
            )
            skipped_count += 1
            continue

        await stage(TaskStage.CHUNKING.value, processed_count, len(assets))

        chunked_docs = await process_controller.process_and_split(
            asset.file_bytes,
            asset.name,
        )
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
        await stage(TaskStage.STORING.value, processed_count, len(assets))

        inserted_ids = await chunk_model.create_chunks(chunks)
        await project_model.add_chunk_ids(project_id, inserted_ids)

        processed_count += 1
        results.append(
            {
                "asset_id": asset.asset_id,
                "asset_name": asset.name,
                "project_object_id": str(project_object_id),
                "status": "processed",
                "chunks_created": len(chunked_docs),
                "chunks_saved": len(inserted_ids),
                "chunks": [{"content": doc.page_content, "metadata": doc.metadata} for doc in chunked_docs],
            },
        )

    return {
        "project_id": project_id,
        "chunk_size": request.chunk_size,
        "overlap_size": request.overlap_size,
        "status": ProcessStatus.PROCESSING_SUCCESS.value,
        "reset": request.reset,
        "assets_found": len(assets),
        "assets_processed": processed_count,
        "assets_skipped": skipped_count,
        "results": results,
    }
