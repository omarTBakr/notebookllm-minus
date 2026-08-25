from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enums import (
    DbBackend,
    DistanceMethod,
    Language,
    LLMChattingProvider,
    LLMEmbeddingProvider,
    LogFormat,
    LogLevel,
    ThinkingLevel,
)

SRC_DIR = Path(__file__).parent.parent

# Fields whose value must name a member of some enum. Annotating them with the
# enum is what validates them: pydantic reports the whole allowed set on a bad
# value, so none of these needs a hand-written validator any more. The two
# normalizers below only fix case and whitespace before that check runs.
_LOWERCASE_CHOICES = (
    "DOCUMENT_DB_BACKEND",
    "GENERATION_BACKEND",
    "EMBEDDING_BACKEND",
    "VECTOR_DB_DISTANCE_METHOD",
    "DEFAULT_LANG",
    "GENERATION_THINKING",
    "LOG_FORMAT",
)

# GENERATION_THINKING is a yes/no that also takes three levels, and .env files
# spell yes/no every which way. Mapped onto the enum rather than rejected.
_THINKING_ALIASES = {
    "1": ThinkingLevel.TRUE,
    "yes": ThinkingLevel.TRUE,
    "on": ThinkingLevel.TRUE,
    "0": ThinkingLevel.FALSE,
    "no": ThinkingLevel.FALSE,
    "off": ThinkingLevel.FALSE,
    "": ThinkingLevel.FALSE,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SRC_DIR / ".env",  # resolves to src/.env regardless of CWD
        case_sensitive=False,
        extra="ignore",
        # Store the enum's *value*, so every reader still sees a plain string:
        # these end up in log config, JSON responses and f-strings, where an
        # enum member would render as "LogLevel.INFO" rather than "INFO".
        use_enum_values=True,
        # ...including the defaults below, which pydantic would otherwise leave
        # as enum members, making a field's type depend on whether it was set.
        validate_default=True,
    )

    # --- application ---------------------------------------------------------
    APPLICATION_NAME: str
    APP_VERSION: str

    # --- uploads -------------------------------------------------------------
    ALLOWED_TYPES: list
    MAX_FILE_SIZE: int = 10485760       # hard ceiling on a single upload (bytes)
    MAX_FILE_CHUNK_SIZE: int            # streaming read size while uploading

    # --- document database ---------------------------------------------------
    # Which provider backs the main data store. Postgres also holds the vectors;
    # Mongo pairs with Qdrant. See the README's "Database backends".
    DOCUMENT_DB_BACKEND: DbBackend = DbBackend.MONGO

    # mongo — only read when DOCUMENT_DB_BACKEND is "mongo"
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "notebookllm"

    # postgres — only read when DOCUMENT_DB_BACKEND is "postgres". The DSN is
    # assembled from these by postgres_url / postgres_async_url below.
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "[PASSWORD]"
    POSTGRES_DB: str = "notebookllm-minus"

    # --- vector database -----------------------------------------------------
    # VECTOR_DB_PATH: str = "assets/qdrant_db"  # embedded mode; relative resolves against src/
    VECTOR_DB_URL: str | None = None   # set to use a server instead of VECTOR_DB_PATH
    VECTOR_DB_API_KEY: str | None = None
    # Fixed when a collection is created; changing it means rebuilding.
    VECTOR_DB_DISTANCE_METHOD: DistanceMethod = DistanceMethod.COSINE

    # --- llm providers -------------------------------------------------------
    # Deliberately different types: Anthropic ships no embeddings API, so
    # "anthropic" is valid for generation and rejected for embedding.
    GENERATION_BACKEND: LLMChattingProvider
    EMBEDDING_BACKEND: LLMEmbeddingProvider

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

    # --- generation ----------------------------------------------------------
    GENERATION_MODEL_ID: str
    # Generous on purpose: a reasoning model spends this budget on its
    # scratchpad *before* the answer, so a small cap can be consumed entirely
    # by thinking and leave the reply truncated or empty.
    GENERATION_DEFAULT_MAX_TOKENS: int = 4096
    GENERATION_DEFAULT_TEMPERATURE: float = 0.1
    # Ask the model to expose its reasoning. Ollama-only; ignored elsewhere,
    # and dropped automatically if the model doesn't support it.
    GENERATION_THINKING: ThinkingLevel = ThinkingLevel.TRUE

    # --- embedding -----------------------------------------------------------
    EMBEDDING_MODEL_ID: str
    EMBEDDING_MODEL_SIZE: int  # must match the embedding model, see .env.example

    # --- chat ----------------------------------------------------------------
    DEFAULT_LANG: Language = Language.EN  # prompt locale when a chat names none
    CHAT_HISTORY_LIMIT: int = 10          # prior turns sent as context
    RETRIEVAL_TOP_K: int = 5              # chunks retrieved per grounded answer

    # --- logging -------------------------------------------------------------
    LOG_LEVEL: LogLevel = LogLevel.INFO
    LOG_FORMAT: LogFormat = LogFormat.TEXT
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_DIR: str = "logs"  # relative paths resolve against src/
    LOG_FILE_NAME: str = "notebookllm-minus.log"
    LOG_MAX_BYTES: int = 10485760  # 10 MB per file before rotating
    LOG_BACKUP_COUNT: int = 5

    # --- normalizers ---------------------------------------------------------
    # `mode="before"` so they run ahead of the enum check: a .env saying
    # "Ollama" or " postgres " is a spelling difference, not a wrong value.

    @field_validator(*_LOWERCASE_CHOICES, mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _uppercase(cls, value: object) -> object:
        # The one option whose canonical spelling is upper case, because that
        # is how `logging` names its levels.
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("GENERATION_THINKING", mode="before")
    @classmethod
    def _resolve_thinking_alias(cls, value: object) -> object:
        return _THINKING_ALIASES.get(value, value) if isinstance(value, str) else value

    # --- derived paths and DSNs ----------------------------------------------

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

    def _postgres_url(self, driver: str) -> str:
        """DSN for *driver*, with the credentials percent-encoded.

        quote_plus because a password is free text: an unescaped `@` or `/` in
        it silently reshapes the URL and the connection fails somewhere else
        entirely.
        """
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        database = quote_plus(self.POSTGRES_DB)
        return (
            f"{driver}://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"
        )

    @property
    def postgres_url(self) -> str:
        """Plain DSN — psql, and Alembic's offline mode."""
        return self._postgres_url("postgresql")

    @property
    def postgres_async_url(self) -> str:
        """The one the app connects with: SQLAlchemy over asyncpg."""
        return self._postgres_url("postgresql+asyncpg")


@lru_cache
def get_settings() -> Settings:
    """The one Settings instance for this process.

    Cached because it is a FastAPI ``Depends`` — without this, every request
    touching it re-reads .env and re-runs every validator. The consequence is
    that .env edits need a restart, which was already true in practice since
    main.py captures SETTINGS at import time.
    """
    return Settings()
