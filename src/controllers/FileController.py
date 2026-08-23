import random
import string
from pathlib import Path

import aiofiles  # ty:ignore[unresolved-import]
from fastapi import UploadFile

from enums import FileStatus
from exceptions import FileDbError

from .BaseController import BaseController


class FileController(BaseController):
    """
    FileController is responsible for handling file upload and validation.
    """

    def __init__(self):
        super().__init__()  # BaseController already sets self.settings
        self.save_dir = Path(__file__).parent.parent / "assets" / "Files"
        self.save_dir.mkdir(parents=True, exist_ok=True)

    async def save_file(self, project_id: str, file: UploadFile) -> Path:
        """Stream the upload to disk, or raise FileDbError."""
        file_path = self.generate_unique_path(project_id, file)
        bytes_written = 0

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(file_path, "wb") as f:
                while chunk := await file.read(self.settings.MAX_FILE_CHUNK_SIZE):
                    await f.write(chunk)
                    bytes_written += len(chunk)
        except OSError as exc:
            raise FileDbError(
                f"{FileStatus.SAVE_ERROR.value}: {file.filename!r} for project "
                f"{project_id!r} ({bytes_written} bytes written)"
            ) from exc
        finally:
            await file.close()

        self.logger.info(
            "Saved %r for project %r to %s (%s bytes)",
            file.filename,
            project_id,
            file_path.name,
            bytes_written,
        )
        return file_path

    def generate_unique_path(self, project_id: str, file: UploadFile) -> Path:
        def _make_unique_name() -> str:
            original = Path(str(file.filename))
            suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            return original.stem + "_" + suffix + original.suffix

        file_path = self.save_dir / project_id / _make_unique_name()

        while file_path.exists():
            file_path = self.save_dir / project_id / _make_unique_name()

        return file_path
