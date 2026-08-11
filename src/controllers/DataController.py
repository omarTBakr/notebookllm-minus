from fastapi import UploadFile

from enums import FileStatus
from exceptions import InvalidFileError

from .BaseController import BaseController


class DataController(BaseController):
    """
    DataController is responsible for handling data upload and validation.
    """

    def __init__(self):
        super().__init__()


    def _validate_file_extension(self, file_type: str) -> bool:
        return file_type in self.settings.ALLOWED_TYPES

    def _validate_file_size(self, file: UploadFile) -> bool:
        if file.size is None:
            return True  # size unknown — allow and let downstream handle it
        return file.size <= self.settings.MAX_FILE_SIZE

    def validate_file(self, file: UploadFile) -> None:
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
