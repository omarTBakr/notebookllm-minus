"""Domain exceptions raised by the lower layers (models, controllers).

The rule: low-level code raises a *typed* error describing what went wrong and
knows nothing about HTTP. The boundary — a single handler in ``main.py`` —
reads ``status_code`` off the exception, logs it once, and turns it into a
response. Nothing in between catches broadly or re-wraps.

Always chain when translating a library error, so the original traceback
survives::

    raise StorageError("...") from exc
"""


class NotebookLLMError(Exception):
    """Base for every error this application raises deliberately."""

    status_code: int = 500


class InvalidInputError(NotebookLLMError):
    """The caller sent something we can't work with."""

    status_code = 400


class NotFoundError(NotebookLLMError):
    """The requested resource does not exist."""

    status_code = 404


class StorageError(NotebookLLMError):
    """The database is unreachable or rejected the operation."""

    status_code = 503


class ProcessingError(NotebookLLMError):
    """A document could not be turned into chunks."""

    status_code = 500


# --- specific errors ---------------------------------------------------------


class ProjectNotFoundError(NotFoundError):
    """No project matches the given project_id."""


class UploadedFileNotFoundError(NotFoundError):
    """The named file does not exist in the project's directory."""


class AssetNotFoundError(NotFoundError):
    """No asset matches the given asset_id."""


class InvalidFileError(InvalidInputError):
    """An upload failed validation (wrong content type, too large)."""


class UnsupportedFileTypeError(InvalidInputError):
    """No loader is registered for this file extension."""


class FileStorageError(NotebookLLMError):
    """Writing the upload to disk failed."""

    status_code = 500


class ExtractionError(ProcessingError):
    """A loader could not read the file's text."""


class ChunkingError(ProcessingError):
    """The text splitter rejected the document or its parameters."""


