from enum import Enum


class FileStatus(str, Enum):
    UPLOADED = "file uploaded successfully"
    INVALID_TYPE = "invalid file type"
    INVALID_SIZE = "file size exceeds limit"
    # SAVED and NOT_FOUND were dropped: SAVED duplicated UPLOADED, and "not
    # found" is now UploadedFileNotFoundError, which carries its own message.
