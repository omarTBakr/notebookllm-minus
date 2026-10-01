import uuid

from data.models import Asset, AssetModel, Project, ProjectModel
from shared.enums import AssetType, FileStatus
from shared.exceptions import InvalidFileError

from ..core.BaseService import BaseService
from .upload import Upload


class DataService(BaseService):
    """
    DataService is responsible for handling data upload and validation.
    """

    def __init__(self, db=None):
        super().__init__()
        self.db = db

    def _validate_file_extension(self, file_type: str) -> bool:
        return file_type in self.settings.ALLOWED_TYPES

    def _validate_file_size(self, file: Upload) -> bool:
        if file.size is None:
            return True  # size unknown — allow and let downstream handle it
        return file.size <= self.settings.MAX_FILE_SIZE

    def validate_file(self, file: Upload) -> None:
        """Raise InvalidFileError naming the reason; return None if the file is fine."""
        if not self._validate_file_extension(str(file.content_type)):
            raise InvalidFileError(
                f"{FileStatus.INVALID_TYPE.value}: {file.content_type!r} is not one of "
                f"{self.settings.ALLOWED_TYPES}"
            )

        if not self._validate_file_size(file):
            raise InvalidFileError(
                f"{FileStatus.INVALID_SIZE.value}: {file.size} bytes exceeds the "
                f"{self.settings.MAX_FILE_SIZE} byte limit"
            )

        self.logger.debug(
            "Accepted upload %r (type=%s, size=%s)",
            file.filename,
            file.content_type,
            file.size,
        )

    async def store_upload(self, project_id: str, filename: str, content_type: str | None, file_bytes: bytes):
        """Upsert the project, then file the bytes under it as an asset.

        Returns ``(project_object_id, asset, asset_object_id)``. Write failures
        raise DbError, so there is no falsy "failed" return to check.
        """
        # The project first, so the asset has a valid project_id to reference.
        # created_at/updated_at come from the model's UTC defaults.
        db = self.db
        project_model = ProjectModel(db)
        project_object_id = await project_model.update_project(
            Project(project_id=project_id, name=filename, description=f"Project {filename}")
        )

        asset = Asset(
            asset_id=str(uuid.uuid4()),
            asset_type=AssetType.from_content_type(content_type),
            project_id=project_id,
            name=filename,
            description=f"Uploaded file for project {project_id}",
            file_bytes=file_bytes,
        )
        asset_object_id = await AssetModel(db).update_asset(asset)

        # Register the asset's _id on the project so assets_ids stays in sync.
        await project_model.add_asset_id(project_id, asset_object_id)

        return project_object_id, asset, asset_object_id
