from enum import Enum 


class FileStatus(str, Enum):
    UPLOADED     = "file uploaded successfully"
    INVALID_TYPE = "invalid file type"
    INVALID_SIZE = "file size exceeds limit"
    SAVE_ERROR   = "error saving the file"
    # SAVED and NOT_FOUND were dropped: SAVED duplicated UPLOADED, and "not
    # found" is now UploadedFileNotFoundError, which carries its own message.
