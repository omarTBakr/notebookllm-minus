from functools import lru_cache
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enums import (
    DistanceMethod,
    LLMChattingProvider,
    LLMEmbeddingProvider,
    DbBackend,
)

SRC_DIR = Path(__file__).parent.parent


def _normalize_choice(value: str, choices: type, field_name: str) -> str:
    """Lowercase *value* and check it names a member of the *choices* enum.

    Same shape as _normalize_log_level below, factored out because four
    provider fields need it and each one names a different enum.
    """
    normalized = value.strip().lower()
    allowed = [member.value for member in choices]
    if normalized not in allowed:
        raise ValueError(f"{field_name} must be one of {allowed}, got {value!r}")
    return normalized


class Settings(BaseSettings):
    APPLICATION_NAME: str
    APP_VERSION: str

    ALLOWED_TYPES: list
    MAX_FILE_CHUNK_SIZE: int
 
    MAX_FILE_SIZE: int = 10485760  # hard ceiling on a single upload (bytes)

    # document db — which provider backs the main data store
    DOCUMENT_DB_BACKEND: str = "mongo"

    # mongo db configurations
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "notebookllm"

    # llm provider configurations
    GENERATION_BACKEND: str  # one of LLMChattingProvider
    EMBEDDING_BACKEND: str   # one of LLMEmbeddingProvider

    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE_URL: str | None = None  # for OpenAI-compatible endpoints
    GOOGLE_API_KEY: str | None = None
    COHERE_API_KEY: str | None = None
    # Ollama is local and takes no key — a reachable host is the whole config.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # A second Ollama, reached over the network rather than on this machine —
    # the "web" models in the picker. Optional: unset means local-only, which
    # is the ordinary setup and behaves exactly as before.
    OLLAMA_CLOUD_BASE_URL: str | None = None

    GENERATION_MODEL_ID: str
    # Generous on purpose: a reasoning model spends this budget on its
    # scratchpad *before* the answer, so a small cap can be consumed entirely
    # by thinking and leave the reply truncated or empty.
    GENERATION_DEFAULT_MAX_TOKENS: int = 4096
    GENERATION_DEFAULT_TEMPERATURE: float = 0.1

    # Ask the model to expose its reasoning. Ollama-only; ignored elsewhere,
    # and dropped automatically if the model doesn't support it.
    # true/false, or "low" / "medium" / "high".
    GENERATION_THINKING: str = "true"

    EMBEDDING_MODEL_ID: str
    EMBEDDING_MODEL_SIZE: int  # must match the embedding model, see .env.example

    # chat configurations
    DEFAULT_LANG: str = "en"          # prompt locale when a chat names none
    CHAT_HISTORY_LIMIT: int = 10      # prior turns sent as context
    RETRIEVAL_TOP_K: int = 5          # chunks retrieved per grounded answer

    # vector database configurations (kept for postgres/qdrant connection properties)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "[PASSWORD]"
    POSTGRES_DB: str = "notebookllm-minus"

    # VECTOR_DB_PATH: str = "assets/qdrant_db"  # embedded mode; relative resolves against src/
    VECTOR_DB_URL: str | None = None   # set to use a server instead of VECTOR_DB_PATH
    VECTOR_DB_API_KEY: str | None = None
    VECTOR_DB_DISTANCE_METHOD: str = "cosine"

    # logging configurations
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # "text" for humans, "json" for log aggregators
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_DIR: str = "logs"  # relative paths resolve against src/
    LOG_FILE_NAME: str = "notebookllm-minus.log"
    LOG_MAX_BYTES: int = 10485760  # 10 MB per file before rotating
    LOG_BACKUP_COUNT: int = 5






    @field_validator("GENERATION_BACKEND")
    @classmethod
    def _normalize_generation_backend(cls, value: str) -> str:
        return _normalize_choice(value, LLMChattingProvider, "GENERATION_BACKEND")


    @field_validator("EMBEDDING_BACKEND")
    @classmethod
    def _normalize_embedding_backend(cls, value: str) -> str:
        # Deliberately a different set from GENERATION_BACKEND: Anthropic ships
        # no embeddings API, so "anthropic" is rejected here.
        return _normalize_choice(value, LLMEmbeddingProvider, "EMBEDDING_BACKEND")


    @field_validator("DEFAULT_LANG")
    @classmethod
    def _normalize_default_lang(cls, value: str) -> str:
        # Imported here rather than at module scope: templates imports utils
        # for its logger, so a top-level import would close the cycle.
        from templates.locales import SUPPORTED_LANGS

        normalized = value.strip().lower()
        if normalized not in SUPPORTED_LANGS:
            raise ValueError(
                f"DEFAULT_LANG must be one of {list(SUPPORTED_LANGS)}, got {value!r}"
            )
        return normalized

    @field_validator("DOCUMENT_DB_BACKEND")
    @classmethod
    def _normalize_document_db_backend(cls, value: str) -> str:
        return _normalize_choice(value, DbBackend, "DOCUMENT_DB_BACKEND")


    @field_validator("VECTOR_DB_DISTANCE_METHOD")
    @classmethod
    def _normalize_distance_method(cls, value: str) -> str:
        return _normalize_choice(value, DistanceMethod, "VECTOR_DB_DISTANCE_METHOD")


    @field_validator("LOG_LEVEL")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}")
        return level


    @field_validator("LOG_FORMAT")
    @classmethod
    def _normalize_log_format(cls, value: str) -> str:
        fmt = value.strip().lower()
        if fmt not in {"text", "json"}:
            raise ValueError(f"LOG_FORMAT must be 'text' or 'json', got {value!r}")
        return fmt

    @property
    def log_file_path(self) -> Path:
        """Absolute path of the log file, anchoring a relative LOG_DIR to src/."""
        log_dir = Path(self.LOG_DIR)
        if not log_dir.is_absolute():
            log_dir = SRC_DIR / log_dir
        return log_dir / self.LOG_FILE_NAME

    # @property
    # def vector_db_path(self) -> Path:
    #     """Absolute path of the embedded vector store, anchored to src/.

    #     Same treatment as log_file_path, and for the same reason: the app is
    #     launched from src/ but need not be, and the store must not follow CWD.
    #     """
    #     db_path = Path(self.VECTOR_DB_PATH)
    #     if not db_path.is_absolute():
    #         db_path = SRC_DIR / db_path
    #     return db_path

    model_config = SettingsConfigDict(
        env_file=SRC_DIR / ".env",  # resolves to src/.env regardless of CWD
        case_sensitive=False,
        extra="ignore"
    )
    @property
    def vector_db_url(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
@lru_cache
def get_settings() -> Settings:
    """The one Settings instance for this process.

    Cached because it is a FastAPI ``Depends`` — without this, every request
    touching it re-reads .env and re-runs every validator. The consequence is
    that .env edits need a restart, which was already true in practice since
    main.py captures SETTINGS at import time.
    """
    return Settings()
