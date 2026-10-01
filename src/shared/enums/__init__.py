"""The project's enums, grouped by what they describe.

Everything is re-exported here, and every caller imports from ``enums``
directly — ``from enums import AssetType``, never ``from enums.content
import AssetType``. The subpackages are an internal filing system, so a name
can move between them without touching the 65 modules that use it; this file
is the whole public surface.

    app       locales, logging, HTTP response messages
    content   what an uploaded file is, how its text is extracted and split
    providers the LLM backends, and the tables translating to each vendor
    storage   which database, which collection, how vectors are indexed
    tasks     background work and the states it moves through
"""

from .app import FileStatus, Language, LogFormat, LogLevel
from .content import (
    LANGUAGE_SPLITTERS,
    ArtifactKind,
    ArtifactStatus,
    AssetType,
    FileExtension,
    PdfLoader,
)
from .providers import (
    CHAT_PROVIDER_API_KEY_FIELDS,
    CHAT_PROVIDER_SETTING_KWARGS,
    CHAT_ROLE_TO_GOOGLE,
    EMBEDDING_INPUT_TYPE_TO_COHERE,
    EMBEDDING_INPUT_TYPE_TO_GOOGLE,
    EMBEDDING_INPUT_TYPE_TO_NVIDIA,
    EMBEDDING_PROVIDER_API_KEY_FIELDS,
    EMBEDDING_PROVIDER_SETTING_KWARGS,
    EMBEDDING_TRUNCATE_TO_NVIDIA,
    ChatRole,
    EmbeddingInputType,
    LLMChattingProvider,
    LLMEmbeddingProvider,
    ModelCapability,
    NvidiaSafetyModelMarker,
    ThinkingLevel,
    TruncateMode,
)
from .storage import (
    DISTANCE_METHOD_TO_PGVECTOR,
    DISTANCE_METHOD_TO_QDRANT,
    DatabaseCollection,
    DbBackend,
    DistanceMethod,
    IndexType,
)
from .tasks import (
    IN_FLIGHT,
    CeleryTaskFunction,
    ProcessStatus,
    TaskExecutionStatus,
    TaskStage,
)

__all__ = [
    "ArtifactKind",
    "ArtifactStatus",
    "AssetType",
    "ChatRole",
    "CeleryTaskFunction",
    "TaskExecutionStatus",
    "TaskStage",
    "IN_FLIGHT",
    "ModelCapability",
    "NvidiaSafetyModelMarker",
    "CHAT_PROVIDER_API_KEY_FIELDS",
    "CHAT_PROVIDER_SETTING_KWARGS",
    "CHAT_ROLE_TO_GOOGLE",
    "DatabaseCollection",
    "DbBackend",
    "DISTANCE_METHOD_TO_PGVECTOR",
    "DISTANCE_METHOD_TO_QDRANT",
    "DistanceMethod",
    "EmbeddingInputType",
    "EMBEDDING_INPUT_TYPE_TO_COHERE",
    "EMBEDDING_INPUT_TYPE_TO_GOOGLE",
    "EMBEDDING_INPUT_TYPE_TO_NVIDIA",
    "EMBEDDING_TRUNCATE_TO_NVIDIA",
    "EMBEDDING_PROVIDER_API_KEY_FIELDS",
    "EMBEDDING_PROVIDER_SETTING_KWARGS",
    "FileExtension",
    "PdfLoader",
    "FileStatus",
    "IndexType",
    "Language",
    "LANGUAGE_SPLITTERS",
    "LLMChattingProvider",
    "LLMEmbeddingProvider",
    "LogFormat",
    "LogLevel",
    "ProcessStatus",
    "ThinkingLevel",
    "TruncateMode",
]
