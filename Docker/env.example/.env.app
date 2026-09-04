APPLICATION_NAME="NotebookLLM-minus"
APP_VERSION="v2"

ALLOWED_TYPES=["application/pdf", "text/plain", "text/markdown"]
MAX_FILE_SIZE=10485760
MAX_FILE_CHUNK_SIZE=65536
CHUNKING_BATCH_SIZE=512

# Choose backend: postgres | mongo
DOCUMENT_DB_BACKEND="postgres"

# --- postgresql (+ pgvector) -------------------------------------------------
POSTGRES_HOST="pgvector"
POSTGRES_PORT=5432
POSTGRES_USER="postgres"
POSTGRES_PASSWORD="change-me"
POSTGRES_DB="notebookllm-minus"

# --- mongodb -----------------------------------------------------------------
MONGO_HOST="mongo"
MONGO_PORT=27017
MONGO_USER="root"
MONGO_PASSWORD="change-me"
MONGO_DB_NAME="notebookllm_minus"

# --- vector database ---------------------------------------------------------
VECTOR_DB_HOST="qdrant"
VECTOR_DB_PORT=6333
VECTOR_DB_DISTANCE_METHOD="cosine"

# --- llm providers -----------------------------------------------------------
GENERATION_BACKEND="ollama"  # anthropic | openai | google | cohere | nvidia | ollama
EMBEDDING_BACKEND="nvidia"

ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""
GOOGLE_API_KEY=""
COHERE_API_KEY=""
NVIDIA_API_KEY=""      # NVIDIA NIM; see src/.env.example

OLLAMA_HOST="host.docker.internal"
OLLAMA_PORT=11434

# --- generation --------------------------------------------------------------
GENERATION_MODEL_ID="gemma4:e4b"
GENERATION_DEFAULT_MAX_TOKENS=4096
GENERATION_DEFAULT_TEMPERATURE=0.1
GENERATION_THINKING="true"

# --- embedding ---------------------------------------------------------------
EMBEDDING_MODEL_ID="nvidia/nemotron-3-embed-1b"
EMBEDDING_MODEL_SIZE=2048

# --- chat --------------------------------------------------------------------
DEFAULT_LANG="en"
CHAT_HISTORY_LIMIT=10
RETRIEVAL_TOP_K=5
RETRIEVAL_MIN_SCORE=0.0

# --- extraction --------------------------------------------------------------
# pymupdf: needed for a citation's highlight (only pymupdf captures the word
# coordinates it is drawn from). ~19x slower than pypdf, parallelised across a
# process pool sized to the container's real CPU budget. Set to "pypdf" for the
# old, faster extraction with no highlight. See PDF_LOADER in src/utils/config.py.
PDF_LOADER="pymupdf"

# --- logging -----------------------------------------------------------------
LOG_LEVEL="INFO"
LOG_FORMAT="text"
LOG_TO_CONSOLE=true
LOG_TO_FILE=true
LOG_DIR="logs"
LOG_FILE_NAME="notebookllm-minus.log"
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5

# --- Celery -------------------------------------------------------------------
# Hostnames are the compose *service* names, not localhost: this file is read
# from inside a container. CELERY_USERNAME / CELERY_PASSWORD must match
# .env.rabbitmq (RABBITMQ_DEFAULT_USER / _PASS), and CELERY_BACKEND_PASSWORD
# must match .env.redis (REDIS_PASSWORD). A mismatch is not a startup error —
# the queue simply never drains.
CELERY_USERNAME = "guest"
CELERY_PASSWORD = "password"
CELERY_HOST     = "rabbitmq"
CELERY_PORT     = 5672
CELERY_VHOST    = "/"

CELERY_BACKEND_HOST     = "redis"
CELERY_BACKEND_PORT     = 6379
CELERY_BACKEND_PASSWORD = "password"
CELERY_BACKEND_DB       = 0

# Acknowledge only after a task finishes, so a killed worker returns the job to
# the queue. Quorum queues are what stop Celery using RabbitMQ's deprecated
# global QoS; the type is fixed when a queue is declared and cannot be changed
# in place.
CELERY_TASK_ACKS_LATE = true
CELERY_TASK_QUEUE_TYPE = "quorum"

# The soft limit is the one that matters: it raises inside the task so its
# cleanup runs, where the hard limit is a SIGKILL that skips every `finally`.
# Must be below CELERY_TASK_TIME_LIMIT or it can never fire.
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TASK_TRACK_STARTED = true
CELERY_RESULT_EXPIRES = 604800

# Task events. Required for Flower to show any task history at all; the second
# is what makes a task no worker ever collected visible.
CELERY_WORKER_SEND_TASK_EVENTS = true
CELERY_TASK_SEND_SENT_EVENT = true

# The task_executions sweep: how often it runs, and how long a finished row is
# kept.
CELERY_MAINTENANCE_INTERVAL_HOURS = 24
CELERY_TASK_RETENTION_DAYS = 7

# --- Flower (Celery dashboard) ------------------------------------------------
# FLOWER_PORT must match the "5555:5555" mapping and the healthcheck in
# docker-compose.yml — a Compose ports: mapping cannot read env_file.
FLOWER_PORT = 5555
# Leave commented to run without authentication, as the rest of this stack does.
# FLOWER_BASIC_AUTH="" does NOT disable auth: Flower reads an empty string as
# "auth on, no valid users" and answers 401 everywhere except /healthcheck, so
# the container looks healthy while the dashboard is unreachable.
# FLOWER_BASIC_AUTH = "admin:change-me"
FLOWER_PERSISTENT = true
FLOWER_DB = "/app/flower/flower.db"
FLOWER_MAX_TASKS = 10000
FLOWER_PURGE_OFFLINE_WORKERS = 300
