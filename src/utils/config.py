from enum import Enum
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
    PdfLoader,
    ThinkingLevel,
)

SRC_DIR = Path(__file__).parent.parent

# LOG_LEVEL is the one enum-typed field whose canonical spelling is upper
# case (it mirrors `logging`'s own level names), so it gets its own
# validator below instead of joining this set.
_UPPERCASE_FIELDS = (LogLevel,)


def _enum_choice_fields(annotations: dict, *, exclude: tuple = ()) -> tuple[str, ...]:
    """Names of the fields, from a class body's raw annotations, whose type
    is a `str` `Enum` — every field pydantic will reject on a spelling
    difference rather than a wrong value.

    Derived instead of listed by hand: the alternative is a tuple of field
    names that silently goes stale the moment someone adds or renames an
    enum-typed setting, since nothing would force it to be updated alongside
    the field itself.
    """
    return tuple(
        name
        for name, annotation in annotations.items()
        if isinstance(annotation, type)
        and issubclass(annotation, Enum)
        and annotation not in exclude
    )


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

    # --- indexing ------------------------------------------------------------
    # How many chunks are embedded per request to the model. One batch is one
    # round trip; peak memory is one batch of text plus one batch of vectors.
    CHUNKING_BATCH_SIZE: int = 512

    # --- document database ---------------------------------------------------
    # Which provider backs the main data store. Postgres also holds the vectors;
    # Mongo pairs with Qdrant. See the README's "Database backends".
    DOCUMENT_DB_BACKEND: DbBackend = DbBackend.MONGO

    # mongo — only read when DOCUMENT_DB_BACKEND is "mongo"
    MONGO_HOST: str = "localhost"
    MONGO_PORT: int = 27017
    MONGO_USER: str = "root"
    MONGO_PASSWORD: str = "example"
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
    VECTOR_DB_HOST: str | None = None   # set to use a server instead of VECTOR_DB_PATH
    VECTOR_DB_PORT: int | None = None
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
    OLLAMA_HOST: str = "localhost"
    OLLAMA_PORT: int = 11434
    # A second Ollama, reached over the network rather than on this machine —
    # the "web" models in the picker. Optional: unset means local-only, which
    # is the ordinary setup and behaves exactly as before.
    OLLAMA_CLOUD_BASE_URL: str | None = None

    # Not a .env knob — never documented in .env.example. ProviderCache sets
    # this via model_copy() to hand a per-model client a resolved host (local
    # port vs. the cloud URL) without either factory learning there are two
    # hosts at all. See ollama_base_url below and factories/provider_cache.py.
    OLLAMA_BASE_URL_OVERRIDE: str | None = None

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
    # Passages scoring below this are dropped before they reach the prompt.
    # Scores are similarities on both backends (higher is better), so 0.0 is
    # "keep everything" and is the default: a floor that is too high silently
    # ungrounds answers, which is worse than a weak citation. Raise it once
    # you have looked at real scores for your corpus and model.
    RETRIEVAL_MIN_SCORE: float = 0.0

    # Which library extracts text from PDFs. Measured on the 274-page Arabic
    # guide (25 MB), whole document, after NFKC normalisation:
    #
    #   pypdf        29s    8-9/14 real words   90,874 lost glyphs
    #   pdfplumber   52s    0/14   real words   92,514 lost glyphs
    #   pymupdf     543s   11/14   real words        0 lost glyphs
    #
    # pdfplumber emits right-to-left text in *visual* order — the characters
    # come out reversed, so not one real Arabic word survives and retrieval on
    # an Arabic corpus collapses to nothing. It is kept here only because it
    # is markedly better at Latin tables, which the other two flatten.
    #
    # pymupdf is the default: it is the only extractor that loses no glyphs at
    # all, it recovers words the other two drop, and — the reason this class
    # of setting exists at all — it is the only one that captures the word
    # coordinates a citation's highlight is drawn from (PdfLayoutController).
    # Neither pypdf nor pdfplumber can produce a highlight regardless of this
    # setting; there is no faster way to get one.
    #
    # The cost is real and does not move with the embedding model: it is
    # entirely in pymupdf's own word-by-word PDF parsing (PdfLayoutController),
    # which runs to completion *before* embedding starts and does not touch
    # it — switching EMBEDDING_MODEL_ID to something smaller changes how long
    # embedding takes, not this. Parallelised across a process pool (pages
    # don't depend on one another), which brought the document above from
    # ~520s serial to ~79s on a 24-core machine — real, but short of a 24x
    # speedup; a handful of dense pages set a floor no amount of splitting
    # moves. Fewer cores buys less of a discount, and a container capped to
    # 1-2 CPUs sees close to the full serial cost. Set to PYPDF to trade the
    # highlight away for the old, faster extraction.
    PDF_LOADER: PdfLoader = PdfLoader.PYMUPDF

    # --- logging -------------------------------------------------------------
    LOG_LEVEL: LogLevel = LogLevel.INFO
    LOG_FORMAT: LogFormat = LogFormat.TEXT
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_DIR: str = "logs"  # relative paths resolve against src/
    LOG_FILE_NAME: str = "notebookllm-minus.log"
    LOG_MAX_BYTES: int = 10485760  # 10 MB per file before rotating
    LOG_BACKUP_COUNT: int = 5

    # --- metrics -------------------------------------------------------------
    # Prometheus scrapes METRICS_PATH. Off switches the endpoint off entirely;
    # the collectors stay defined but nothing is exposed.
    METRICS_ENABLED: bool = True
    METRICS_PATH: str = "/metrics"

    # --- normalizers ---------------------------------------------------------
    # `mode="before"` so they run ahead of the enum check: a .env saying
    # "Ollama" or " postgres " is a spelling difference, not a wrong value.
    #
    # Computed from the field annotations captured above in this same class
    # body — `__annotations__` is a real, already-populated dict at this point
    # in the class's execution, not a hardcoded guess at which fields matter.
    _LOWERCASE_FIELDS = _enum_choice_fields(__annotations__, exclude=_UPPERCASE_FIELDS)

    @field_validator(*_LOWERCASE_FIELDS, mode="before")
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
        # The alias table lives on the enum itself (enums/LLMChattingEnum.py),
        # next to the members it resolves onto — not here, where it would be
        # the one piece of Settings that knows something about ThinkingLevel
        # rather than the other way around.
        return ThinkingLevel.from_alias(value)

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

    @property
    def mongo_uri(self) -> str:
        """The DSN for MongoDB."""
        user = quote_plus(self.MONGO_USER)
        password = quote_plus(self.MONGO_PASSWORD)
        return f"mongodb://{user}:{password}@{self.MONGO_HOST}:{self.MONGO_PORT}"

    @property
    def vector_db_url(self) -> str | None:
        """The URL for the vector database (Qdrant)."""
        if self.VECTOR_DB_HOST and self.VECTOR_DB_PORT:
            return f"http://{self.VECTOR_DB_HOST}:{self.VECTOR_DB_PORT}"
        return None

    @property
    def ollama_base_url(self) -> str:
        """The URL a client should actually connect to.

        The override wins when set — that is the whole mechanism
        ProviderCache uses to point one model's client at the cloud host
        while every other setting still comes from this same object. Absent
        an override, it is the local host built from OLLAMA_HOST/PORT.
        """
        if self.OLLAMA_BASE_URL_OVERRIDE:
            return self.OLLAMA_BASE_URL_OVERRIDE
        return f"http://{self.OLLAMA_HOST}:{self.OLLAMA_PORT}"



@lru_cache
def get_settings() -> Settings:
    """The one Settings instance for this process.

    Cached because it is a FastAPI ``Depends`` — without this, every request
    touching it re-reads .env and re-runs every validator. The consequence is
    that .env edits need a restart, which was already true in practice since
    main.py captures SETTINGS at import time.
    """
    return Settings()
