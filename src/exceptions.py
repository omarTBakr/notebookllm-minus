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


# --- LLM & vector store -----------------------------------------------------


class LLMProviderError(NotebookLLMError):
    """An upstream LLM vendor failed, timed out, or returned nothing usable.

    502 rather than 500: the fault is with a service we depend on, not with
    this application, and the distinction matters when reading logs.
    """

    status_code = 502


class VectorDBError(NotebookLLMError):
    """The vector store is unreachable or rejected the operation.

    The vector-store counterpart of StorageError, and 503 for the same reason.
    """

    status_code = 503


class UnsupportedProviderError(InvalidInputError):
    """A factory was asked for a backend it has no implementation for.

    Also raised when the named backend exists but its API key is missing —
    from the factory's point of view it cannot be built either way.
    """


class EmbeddingError(ProcessingError):
    """Turning chunks into vectors failed, or returned the wrong number."""


