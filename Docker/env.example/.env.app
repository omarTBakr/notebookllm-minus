# Template for Docker/env/.env.app: copy it there and fill in the REPLACE_ME values.
# Environment for the fastapi app and every Celery worker.
# Container-shaped: service names (pgvector, rabbitmq, redis) replace localhost.

# --- Application ---------------------------------------------------------------
# Required: the app refuses to start without these two.
APPLICATION_NAME="NotebookLLM-minus"
APP_VERSION="v2"

# --- Uploads -------------------------------------------------------------------
# MIME types accepted at upload.
ALLOWED_TYPES=["application/pdf", "text/plain", "text/markdown", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
# Largest upload, in bytes (50 MB). nginx caps at 64 MB.
MAX_FILE_SIZE=10485760
# Bytes per streaming read while saving an upload.
MAX_FILE_CHUNK_SIZE=65536

# --- Link sources (PDF, article or YouTube from a URL) -------------------------
# Seconds allowed to fetch a link, redirects and body included.
URL_FETCH_TIMEOUT=30
# Transcript languages to prefer for a YouTube video, in order.
YOUTUBE_TRANSCRIPT_LANGUAGES=["ar", "en"]
# http(s) proxy for transcript requests; YouTube blocks most cloud IPs.
YOUTUBE_PROXY_URL=""

# --- Document database ---------------------------------------------------------
# postgres (pgvector) | mongo (+ qdrant)
DOCUMENT_DB_BACKEND="postgres"

# Postgres. Container port 5432, not the published 5400.
POSTGRES_HOST="pgvector"
POSTGRES_PORT=5432
# Must match env/.env.postgres.
POSTGRES_USER="postgres"
POSTGRES_PASSWORD="REPLACE_ME"
POSTGRES_DB="notebookllm-minus"

# Mongo. Only read when DOCUMENT_DB_BACKEND=mongo; must match env/.env.mongo.
MONGO_HOST="mongo"
MONGO_PORT=27017
MONGO_USER="root"
MONGO_PASSWORD="REPLACE_ME"
MONGO_DB_NAME="notebookllm_minus"

# --- Vector database (qdrant, mongo backend only) ------------------------------
VECTOR_DB_HOST="qdrant"
VECTOR_DB_PORT=6333
VECTOR_DB_DISTANCE_METHOD="cosine"

# --- LLM providers and API keys ------------------------------------------------
# Which provider answers chat and which one embeds:
# anthropic | openai | google | cohere | nvidia | openrouter | ollama
GENERATION_BACKEND="ollama"
# Changing the embedding provider means rebuilding the index.
EMBEDDING_BACKEND="nvidia"

# Host's Ollama, reached from the container (localhost would be the container).
OLLAMA_HOST="host.docker.internal"
OLLAMA_PORT=11434

# Fill in the key for each backend you select; a blank one fails startup.
NVIDIA_API_KEY=""
ANTHROPIC_API_KEY=""
# Only needed for an identity-linked Anthropic key.
ANTHROPIC_WORKSPACE_ID=""
OPENAI_API_KEY=""
COHERE_API_KEY=""
GOOGLE_API_KEY=""
# Google model offered in the model picker.
GOOGLE_MODEL_ID="gemini-3.6-flash"
# OpenRouter: the `openrouter` chat/embedding backend (its models join the picker;
# ids are <publisher>/<model>) and the OCR extractor. OPENROUTER_MODEL is the OCR's.
OPENROUTER_API_KEY=""
OPENROUTER_MODEL="minimax/minimax-m3:free"

# --- Generation ----------------------------------------------------------------
# Chat model id for GENERATION_BACKEND (an Ollama tag, or a provider's model id).
GENERATION_MODEL_ID="gemma4:e4b"
GENERATION_DEFAULT_MAX_TOKENS=4096
GENERATION_DEFAULT_TEMPERATURE=0.1
# Let reasoning models think before answering.
GENERATION_THINKING="true"

# --- Embedding -----------------------------------------------------------------
# Vector width is fixed per collection: changing either means a reindex.
EMBEDDING_MODEL_ID="nvidia/nemotron-3-embed-1b"
EMBEDDING_MODEL_SIZE=2048

# --- Chat and retrieval --------------------------------------------------------
# Answer language when the question does not decide it.
DEFAULT_LANG="en"
# Previous messages sent with each question.
CHAT_HISTORY_LIMIT=10
# Passages retrieved per question.
RETRIEVAL_TOP_K=5
# Similarity floor a passage must clear (0.0 = keep all top-k).
RETRIEVAL_MIN_SCORE=0.0

# --- Ingestion (parsing and chunking) ------------------------------------------
# pymupdf: slower (~2.3 s/page) but the only loader that yields citation highlights.
PDF_LOADER="pymupdf"
# Pages per queue message, the unit of parsing and retry.
PDF_BATCH_PAGES=10
# Chunks shorter than this are merged into a neighbour on the same page.
MIN_CHUNK_CHARS=100
# Chunks embedded per request to the embedding model.
CHUNKING_BATCH_SIZE=512

# --- OCR (re-read unsearchable Arabic pages) -----------------------------------
# Off: the post-processing pass below replaced it and keeps citation highlights.
OCR_ENABLED=false
# qalam | tesseract-best | openrouter
OCR_EXTRACTOR="tesseract-best"
# qalam's word-break gap, as a fraction of the type size (upstream: 0.25).
# Needs the patched qalam build; stock qalam ignores it.
QALAM_WORD_GAP=0.15
# OCR pages with no usable text layer (scans, pictures of text) with
# tesseract-best. qalam picks the pages; costs ~1s per such page.
OCR_UNREADABLE_PAGES=true
# Where the image installs the tessdata_best models.
TESSDATA_BEST="/usr/share/tessdata-best"
# Pages with fewer characters than this are never re-read.
OCR_MIN_CHARS=80
# Pages OCR'd in parallel. 0 = sized from the container's CPUs and memory.
OCR_WORKERS=0

# --- Text post-processing (repair split and fused words with an LLM) -----------
POSTPROCESS_ENABLED=true
# <source>/<model>. NIM ids keep their publisher, hence nvidia/nvidia/...
POSTPROCESS_MODEL_ID="nvidia/nvidia/nemotron-3-super-120b-a12b"
# Pages per model call. Larger calls truncate long Arabic pages.
POSTPROCESS_PAGES_PER_CALL=5
# Model calls in flight per batch (4 hosted, 2 for a local Ollama).
POSTPROCESS_CONCURRENCY=4
# Context window; Ollama only.
POSTPROCESS_NUM_CTX=16384
# Output cap per call. Higher only feeds repetition loops.
POSTPROCESS_MAX_TOKENS=12288
# Reject a repair whose length differs from the original by more than this ratio.
POSTPROCESS_LENGTH_TOLERANCE=0.5

# --- Chunk summaries (what Studio generates from) ------------------------------
# Summarise each chunk of a new document right after it is stored, with the
# notebook's own model, so Studio's first flashcards skip the summarising pass.
# Off: Studio still summarises on first use.
INGEST_SUMMARISE=true

# --- Celery broker (RabbitMQ) --------------------------------------------------
# Must match RABBITMQ_DEFAULT_USER / _PASS in env/.env.rabbitmq.
CELERY_USERNAME="appuser"
CELERY_PASSWORD="REPLACE_ME__same_as_RABBITMQ_DEFAULT_PASS"
CELERY_HOST="rabbitmq"
CELERY_PORT=5672
CELERY_VHOST="/"
CELERY_MANAGEMENT_PORT=15672

# --- Celery result backend (Redis) ---------------------------------------------
# Password must match REDIS_PASSWORD in env/.env.redis.
CELERY_BACKEND_HOST="redis"
CELERY_BACKEND_PORT=6379
CELERY_BACKEND_PASSWORD="REPLACE_ME__same_as_REDIS_PASSWORD"
CELERY_BACKEND_DB=0

# --- Celery tasks and workers --------------------------------------------------
# Prefix for queue and task names.
CELERY_PROJECT_NAME="notebookllm"
# Parse batches per worker at once. Memory-bound; 0 = one per CPU.
CELERY_WORKER_CONCURRENCY=2
# Fixed when a queue is first declared; changing it needs the queue deleted.
CELERY_TASK_QUEUE_TYPE="quorum"
# Acknowledge after the task finishes, so a killed worker's job is redelivered.
CELERY_TASK_ACKS_LATE=true
# Seconds before a task is asked to stop. Must stay below CELERY_TASK_TIME_LIMIT (1860).
CELERY_TASK_SOFT_TIME_LIMIT=540
# Report STARTED instead of PENDING once a worker picks a task up.
CELERY_TASK_TRACK_STARTED=true
# Seconds a finished result stays readable (7 days).
CELERY_RESULT_EXPIRES=604800
# Task events; Flower shows nothing without them.
CELERY_WORKER_SEND_TASK_EVENTS=true
CELERY_TASK_SEND_SENT_EVENT=true
# Hours between runs of the task-table cleanup.
CELERY_MAINTENANCE_INTERVAL_HOURS=24
# Days a finished task row is kept.
CELERY_TASK_RETENTION_DAYS=7

# --- Flower (Celery dashboard, http://localhost:5555) --------------------------
# Must match the 5555 port mapping in docker-compose.yml.
FLOWER_PORT=5555
# Uncomment with user:password to require a login. Never set it empty: that locks everyone out.
# FLOWER_BASIC_AUTH="admin:change-me"
# Keep task history across restarts.
FLOWER_PERSISTENT=true
FLOWER_DB="/app/flower/flower.db"
FLOWER_MAX_TASKS=10000
# Seconds before an offline worker is dropped from the list.
FLOWER_PURGE_OFFLINE_WORKERS=300

# --- Logging and metrics -------------------------------------------------------
LOG_LEVEL="INFO"
# json: structured lines survive the docker log driver.
LOG_FORMAT="text"
LOG_TO_CONSOLE=true
# Off in containers; docker collects stdout.
LOG_TO_FILE=true
# Log file location and rotation; only used when LOG_TO_FILE=true.
LOG_DIR="logs"
LOG_FILE_NAME="notebookllm-minus.log"
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5

# Prometheus scrape endpoint.
METRICS_ENABLED=true
METRICS_PATH="/metrics"
WORKER_METRICS_PORT=9808
