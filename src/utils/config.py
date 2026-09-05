from enum import Enum
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enums import (
    CeleryTaskFunction,
    DbBackend,
    DistanceMethod,
    IndexType,
    Language,
    LLMChattingProvider,
    LLMEmbeddingProvider,
    LogFormat,
    LogLevel,
    PdfLoader,
    ThinkingLevel,
    TruncateMode,
)

SRC_DIR = Path(__file__).parent.parent

# NVIDIA's hosted NIM endpoint. Here rather than inside NvidiaChatProvider so
# that pointing the app at a self-hosted NIM is a .env line, and so the URL
# appears once in the codebase.
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

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
        if isinstance(annotation, type) and issubclass(annotation, Enum) and annotation not in exclude
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
    MAX_FILE_SIZE: int = 10485760  # hard ceiling on a single upload (bytes)
    MAX_FILE_CHUNK_SIZE: int  # streaming read size while uploading

    # --- Arabic OCR ----------------------------------------------------------
    # Re-read a page with OCR when its Arabic text layer cannot be searched.
    # Off by default: it costs seconds per page, and on a well-produced PDF the
    # text layer is already correct, so this must be a decision rather than a
    # habit. `ocr.language.profile()` makes that decision per page and costs
    # microseconds.
    OCR_ENABLED: bool = False
    # Which engine. tesseract-best measured 0.172 WER against 0.545 for the
    # distro `ara` model and 0.167 for Gemini — see src/ocr/reports/FINDINGS.md.
    OCR_EXTRACTOR: str = "tesseract-best"
    # Where ara.traineddata from tessdata_best lives. The distribution package
    # ships the *fast* model, which is a different model and three times worse
    # on this corpus; the image downloads the better one to this path.
    TESSDATA_BEST: str = "/usr/share/tessdata-best"
    # A page needs this many characters before its spacing is judged. Below it,
    # a plate or a chapter heading would be called unusable and re-read for
    # nothing.
    OCR_MIN_CHARS: int = 80
    # How many pages are OCR'd at once. 0 means "every CPU this process may
    # use" — cgroup quota and affinity included, see PdfLayoutController.
    #
    # Threads, not processes: a Celery prefork worker is daemonic and may not
    # fork, and it does not need to. pytesseract runs the `tesseract` binary as
    # a subprocess, so the GIL is released for the whole of the work and page
    # level parallelism scales close to linearly.
    #
    # Each concurrent page holds a 300-dpi RGB raster — roughly 26 MB for A4 —
    # so this is also the knob for OCR's peak memory. Lower it before lowering
    # the DPI, which costs accuracy.
    OCR_WORKERS: int = 0

    # --- chunking ------------------------------------------------------------
    # A chunk shorter than this is not a retrieval unit, it is debris. The
    # recursive splitter flushes whatever short splits it has accumulated as
    # soon as the next split is big enough to need recursion, so a running
    # header or a page number sitting on its own line before the body becomes a
    # standalone chunk. Measured on the 274-page Arabic book, OCR on and
    # chunk_size 1000: 110 of 803 chunks under 50 characters, 78 under 10 —
    # "سورية", "0/", "\u0661". The same book with OCR off is 742 chunks and
    # only 15 under 100, because OCR recovers the running header on pages whose
    # text layer had lost it — that is, the better the extraction, the more
    # debris this produces. They embed to
    # nothing in particular, and because there are so many of them one usually
    # still lands in the top 5, which is how a grounded answer ends up citing
    # five single words.
    #
    # 100 is above the debris (the largest orphan measured was 89) and well
    # below a real paragraph, so nothing with content in it is touched.
    MIN_CHUNK_CHARS: int = 100

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
    VECTOR_DB_HOST: str | None = None  # set to use a server instead of VECTOR_DB_PATH
    VECTOR_DB_PORT: int | None = None
    VECTOR_DB_API_KEY: str | None = None
    # Fixed when a collection is created; changing it means rebuilding.
    VECTOR_DB_DISTANCE_METHOD: DistanceMethod = DistanceMethod.COSINE
    # ANN algorithm VectorRepository.create_index() builds by default. IVFFLAT
    # is pgvector-only; Qdrant rejects it (Qdrant only builds HNSW).
    VECTOR_DB_INDEX_TYPE: IndexType = IndexType.HNSW

    # --- llm providers -------------------------------------------------------
    # Deliberately different types: Anthropic ships no embeddings API, so
    # "anthropic" is valid for generation and rejected for embedding.
    GENERATION_BACKEND: LLMChattingProvider
    EMBEDDING_BACKEND: LLMEmbeddingProvider

    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_ID: str = "claude-sonnet-4-20250514"
    # Required only for an identity-linked API key, which Anthropic rejects
    # outright without it: "anthropic-workspace-id is required when
    # authenticating with an identity-linked API key". An ordinary key needs
    # no workspace, so this stays optional and the header is omitted when
    # unset rather than sent empty. Found in the Anthropic console under
    # Settings -> Workspaces; the id looks like "wrkspc_...".
    ANTHROPIC_WORKSPACE_ID: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE_URL: str | None = None  # for OpenAI-compatible endpoints
    GOOGLE_API_KEY: str | None = None
    GOOGLE_MODEL_ID: str = "gemini-3.6-flash"
    COHERE_API_KEY: str | None = None
    # NVIDIA NIM — an OpenAI-compatible endpoint with its own key, kept
    # separate from OPENAI_API_KEY / OPENAI_API_BASE_URL so both vendors can be
    # configured at once. One key serves chat and embeddings.
    NVIDIA_API_KEY: str | None = None
    # Unlike OPENAI_API_BASE_URL, this has a real default rather than meaning
    # "the SDK's own": an OpenAI client with an nvapi key and no endpoint would
    # dial api.openai.com and fail with an authentication error that says
    # nothing about the mistake. Blank is read as unset (validator below).
    NVIDIA_API_BASE_URL: str = NVIDIA_DEFAULT_BASE_URL
    # NVIDIA's per-request input cap. Below CHUNKING_BATCH_SIZE's default of
    # 512, so NvidiaEmbeddingProvider splits a batch rather than failing one:
    # 257 inputs answers "input count 257 exceeds maximum allowed batch size".
    # A setting because it is NVIDIA's number, not ours, and they can raise it.
    NVIDIA_EMBEDDING_MAX_BATCH: int = 256
    # What to do with a text longer than the model's context. END means one
    # over-long chunk costs its own tail rather than failing the whole upload;
    # NONE is the vendor default and fails the request instead.
    NVIDIA_EMBEDDING_TRUNCATE: TruncateMode = TruncateMode.END
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
    CHAT_HISTORY_LIMIT: int = 10  # prior turns sent as context
    RETRIEVAL_TOP_K: int = 5  # chunks retrieved per grounded answer
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

    # --- task queue / Celery -------------------------------------------------
    # Broker: RabbitMQ — celery_broker_url assembles these into an amqp:// DSN.
    CELERY_USERNAME: str = "appuser"
    CELERY_PASSWORD: str = "change-me"
    CELERY_HOST: str = "localhost"
    CELERY_PORT: int = 5672
    CELERY_VHOST: str = "/"  # RabbitMQ virtual host

    # Result backend: Redis — celery_result_backend_url builds the redis:// DSN.
    CELERY_BACKEND_HOST: str = "localhost"
    CELERY_BACKEND_PORT: int = 6379
    CELERY_BACKEND_PASSWORD: str | None = None
    CELERY_BACKEND_DB: int = 0  # logical Redis database index (0-15)

    # Worker / serialisation — passed directly to the Celery app config dict.
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: list = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    CELERY_TASK_TIME_LIMIT: int = 600  # hard wall-clock limit per task (seconds)
    # The limit that actually matters for correctness. The hard limit above is
    # a SIGKILL of the worker child: it stops the task, but it also skips every
    # `finally` on the way out — the DB disconnect in tasks/process.py and the
    # provider-pool close in tasks/index.py — so each hard kill leaked a
    # connection and a pool. The soft limit raises SoftTimeLimitExceeded inside
    # the task instead, so cleanup runs and the failure is recorded; the hard
    # limit stays as the backstop for a task that ignores it. Must be lower
    # than CELERY_TASK_TIME_LIMIT or it can never fire.
    CELERY_TASK_SOFT_TIME_LIMIT: int = 540
    # Report STARTED once a worker picks a task up. Off by default in Celery,
    # which is why a running task and a queued one were both PENDING — the
    # single most misleading thing the status endpoints did.
    CELERY_TASK_TRACK_STARTED: bool = True
    # How long a finished result stays readable (seconds). Celery's default is
    # one day, unset and therefore invisible; a result that expired used to
    # reappear as PENDING, indistinguishable from "still queued". Expiry is now
    # reported as UNKNOWN instead, so this is a retention choice rather than a
    # correctness one — longer costs Redis memory, since results carry the
    # task's return payload.
    CELERY_RESULT_EXPIRES: int = 604800  # 7 days
    # Emit task-* events. Required by Flower for any task history at all, and
    # by the sent-event below for tasks that never reach a worker.
    CELERY_WORKER_SEND_TASK_EVENTS: bool = True
    # Emit task-sent when the *client* publishes, not when a worker receives.
    # This is what makes a task that no worker ever picked up visible.
    CELERY_TASK_SEND_SENT_EVENT: bool = True

    # --- maintenance sweep ---------------------------------------------------
    # How often the sweep runs. The table is append-only during normal use, so
    # without this it grows for the life of the deployment.
    CELERY_MAINTENANCE_INTERVAL_HOURS: int = 24
    # How long a finished task's row is kept. Defaults to the same week as
    # CELERY_RESULT_EXPIRES: past that point Celery has already forgotten the
    # result, so a row that outlived it can no longer be cross-checked against
    # anything and is only taking up space.
    CELERY_TASK_RETENTION_DAYS: int = 7
    # Acknowledge a message only after the task finishes, so a worker killed
    # mid-ingest returns the job to the queue instead of losing it. This was
    # False while the comment beside it described the True behaviour; True is
    # what the rest of this config already assumes — the
    # cancel_long_running_tasks_on_connection_loss setting below exists
    # specifically to stop late-acked tasks running twice after a reconnect.
    # The ingestion tasks tolerate redelivery: process skips assets that are
    # already chunked, and index upserts on a deterministic point id.
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_WORKER_CONCURRENCY: int = 2

    # Broker connection resilience
    # Retry connecting to the broker if it is not up when the worker starts.
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    # Retry after a mid-run connection loss (not just at startup).
    CELERY_BROKER_CONNECTION_RETRY: bool = True
    # Seconds to wait between consecutive retry attempts.
    CELERY_BROKER_CONNECTION_RETRY_DELAY: int = 2
    # Maximum number of retry attempts before giving up. 0 means retry forever.
    CELERY_BROKER_CONNECTION_MAX_RETRIES: int = 10

    # Transport-level options passed verbatim to the broker / backend drivers.
    # Kept as dicts here; override per-deployment if the transport needs extras
    # (e.g. {"visibility_timeout": 3600} for a Redis broker).
    CELERY_BROKER_TRANSPORT_OPTIONS: dict = {}
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS: dict = {}

    # Revoke and stop long-running tasks when the worker loses its broker
    # connection, so they don't run silently after a reconnect.
    CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS: bool = True

    # --- Flower (Celery dashboard) -------------------------------------------
    #
    # Flower reads FLOWER_*-prefixed environment variables directly, so these
    # are declared here for documentation and validation rather than because
    # anything in this application passes them on. Keeping them in Settings
    # means .env.example describes the whole stack in one place, and a typo in
    # a port shows up as a validation error instead of a container that binds
    # somewhere unexpected.
    FLOWER_PORT: int = 5555
    # Changing this alone is not enough: the published port and the container
    # healthcheck in docker-compose.yml both name 5555 literally, because a
    # Compose `ports:` mapping cannot read a value out of env_file.
    #
    # "user:password", or empty for no authentication — which matches the rest
    # of this stack, where Grafana, Prometheus and the RabbitMQ management UI
    # are all unauthenticated on the host.
    #
    # The empty default is only safe because this value is *not* exported to
    # Flower. Setting FLOWER_BASIC_AUTH="" in the environment does not disable
    # auth: Flower reads it as "auth enabled, no valid users" and answers 401
    # everywhere except /healthcheck — so the container passes its health probe
    # while the dashboard is entirely unreachable. env/.env.app therefore keeps
    # the variable commented out rather than set to "".
    FLOWER_BASIC_AUTH: str = ""
    # Keep task history across restarts. Off by default in Flower, and the
    # default is a poor fit here: a dashboard whose whole purpose is showing
    # tasks that failed or never finished is least useful right after the
    # restart that a failure tends to cause.
    FLOWER_PERSISTENT: bool = True
    FLOWER_DB: str = "/app/flower/flower.db"
    # Ring-buffer size. Each retained task holds its args and result, and the
    # ingest tasks return chunk counts rather than chunk text, so this is small.
    FLOWER_MAX_TASKS: int = 10000
    # Drop workers that have not been seen for this many seconds, so containers
    # replaced by a redeploy stop appearing as live capacity.
    FLOWER_PURGE_OFFLINE_WORKERS: int = 300

    # classic | quorum. Quorum is not just the modern default — it is what
    # stops Celery using RabbitMQ's deprecated `global_qos`. Celery sets the
    # global QoS flag on RabbitMQ unless it detects a quorum queue
    # (celery/worker/consumer/tasks.py), and RabbitMQ has announced global QoS
    # for removal. Classic mirrored queues are already gone in 4.x.
    # Changing this on a live broker needs the existing queues deleted first:
    # a queue's type is fixed at declaration and redeclaring with a different
    # x-queue-type fails with PRECONDITION_FAILED.
    CELERY_TASK_QUEUE_TYPE: str = "quorum"

    # Prefix used in every task and queue name. Queue names are derived as
    # ``CELERY_PROJECT_NAME.<task function name>`` below.
    CELERY_PROJECT_NAME: str = "notebookllm"
    # Default queue name for tasks that do not declare one explicitly.
    CELERY_TASK_DEFAULT_QUEUE: str | None = None
    # Dedicated queues keep CPU-heavy ingestion, model calls, and destructive
    # maintenance from competing for the same worker slots.
    CELERY_QUEUE_PROCESS: str | None = None
    CELERY_QUEUE_INDEX: str | None = None
    CELERY_QUEUE_CHAT: str | None = None
    CELERY_QUEUE_MAINTENANCE: str | None = None

    # --- normalizers ---------------------------------------------------------
    @model_validator(mode="after")
    def _derive_celery_queue_names(self):
        """Use ``<project>.<task function>`` for unset queue overrides."""
        defaults = {
            "CELERY_TASK_DEFAULT_QUEUE": f"{self.CELERY_PROJECT_NAME}.default",
            "CELERY_QUEUE_PROCESS": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PROCESS.value}",
            "CELERY_QUEUE_INDEX": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}",
            "CELERY_QUEUE_CHAT": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.CHAT.value}",
            "CELERY_QUEUE_MAINTENANCE": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.MAINTENANCE.value}",
        }
        for field, value in defaults.items():
            if getattr(self, field) is None:
                setattr(self, field, value)
        return self

    @model_validator(mode="after")
    def _check_soft_time_limit(self):
        """A soft limit at or above the hard one can never fire.

        Worth failing startup over rather than warning: the symptom of getting
        this wrong is silent — tasks go back to being SIGKILLed with their
        cleanup skipped, which is exactly the bug the soft limit was added to
        fix, and nothing in the logs would say so.
        """
        if self.CELERY_TASK_SOFT_TIME_LIMIT >= self.CELERY_TASK_TIME_LIMIT:
            raise ValueError(
                f"CELERY_TASK_SOFT_TIME_LIMIT ({self.CELERY_TASK_SOFT_TIME_LIMIT}) must be "
                f"below CELERY_TASK_TIME_LIMIT ({self.CELERY_TASK_TIME_LIMIT}); "
                "otherwise the hard kill always wins and task cleanup never runs"
            )
        return self

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

    @field_validator("NVIDIA_API_BASE_URL", mode="before")
    @classmethod
    def _default_nvidia_endpoint(cls, value: object) -> object:
        # `NVIDIA_API_BASE_URL = ""` in a .env reads as "I am not setting
        # this", the way every other commented-out or emptied key does — and
        # for this one field an empty string is not a usable value.
        return value or NVIDIA_DEFAULT_BASE_URL

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
        return f"{driver}://{user}:{password}" f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"

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

    @property
    def celery_broker_url(self) -> str:
        """AMQP broker URL for Celery (RabbitMQ).

        Credentials are percent-encoded so a password containing reserved URI
        characters (@ / : ?) does not silently reshape the URL.
        The default vhost "/" is encoded as "%2F" — the canonical form in
        AMQP URLs; any other vhost is encoded the same way.
        """
        user = quote_plus(self.CELERY_USERNAME)
        password = quote_plus(self.CELERY_PASSWORD)
        vhost = quote_plus(self.CELERY_VHOST)
        return f"amqp://{user}:{password}" f"@{self.CELERY_HOST}:{self.CELERY_PORT}/{vhost}"

    @property
    def celery_result_backend_url(self) -> str:
        """Redis URL for the Celery result backend.

        When CELERY_BACKEND_PASSWORD is set the auth segment is included
        (``redis://:<password>@host:port/db``); when it is absent the segment
        is omitted entirely so a Redis server without ``requirepass`` is not
        sent an empty AUTH command that it would reject.
        """
        host = self.CELERY_BACKEND_HOST
        port = self.CELERY_BACKEND_PORT
        db = self.CELERY_BACKEND_DB
        if self.CELERY_BACKEND_PASSWORD:
            password = quote_plus(self.CELERY_BACKEND_PASSWORD)
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"


@lru_cache
def get_settings() -> Settings:
    """The one Settings instance for this process.

    Cached because it is a FastAPI ``Depends`` — without this, every request
    touching it re-reads .env and re-runs every validator. The consequence is
    that .env edits need a restart, which was already true in practice since
    main.py captures SETTINGS at import time.
    """
    return Settings()
