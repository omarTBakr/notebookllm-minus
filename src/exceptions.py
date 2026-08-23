"""Domain exceptions raised by the lower layers (models, controllers).

The rule: low-level code raises a *typed* error describing what went wrong and
knows nothing about HTTP. The boundary — a single handler in ``main.py`` —
reads ``status_code`` off the exception, logs it once, and turns it into a
response. Nothing in between catches broadly or re-wraps.

Always chain when translating a library error, so the original traceback
survives::

    raise DbError("...") from exc
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


class DbError(NotebookLLMError):
    """The database is unreachable or rejected the operation."""

    status_code = 503


class DbConnectionError(DbError):
    """The database connection could not be established or was lost."""

    pass


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


class UserNotFoundError(NotFoundError):
    """No user matches the given user_id.

    Expected rather than exceptional: the browser keeps a user_id in
    localStorage, and a wiped database leaves it pointing at nothing. The UI
    treats this as "start as a new user", not as an error to show.
    """


class SessionNotFoundError(NotFoundError):
    """No session matches the given session_id."""


class ChatNotFoundError(NotFoundError):
    """No chat matches the given chat_id."""


class InvalidFileError(InvalidInputError):
    """An upload failed validation (wrong content type, too large)."""


class UnsupportedFileTypeError(InvalidInputError):
    """No loader is registered for this file extension."""


class FileDbError(NotebookLLMError):
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





class UnsupportedProviderError(InvalidInputError):
    """A factory was asked for a backend it has no implementation for.

    Also raised when the named backend exists but its API key is missing —
    from the factory's point of view it cannot be built either way.
    """


class EmbeddingError(ProcessingError):
    """Turning chunks into vectors failed, or returned the wrong number."""


