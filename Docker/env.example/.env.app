APPLICATION_NAME="NotebookLLM-minus"
APP_VERSION="0.1"

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
GENERATION_BACKEND="ollama"
EMBEDDING_BACKEND="ollama"

ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""
GOOGLE_API_KEY=""
COHERE_API_KEY=""

OLLAMA_HOST="host.docker.internal"
OLLAMA_PORT=11434

# --- generation --------------------------------------------------------------
GENERATION_MODEL_ID="gemma4:e4b"
GENERATION_DEFAULT_MAX_TOKENS=4096
GENERATION_DEFAULT_TEMPERATURE=0.1
GENERATION_THINKING="true"

# --- embedding ---------------------------------------------------------------
EMBEDDING_MODEL_ID="qwen3-embedding:8b"
EMBEDDING_MODEL_SIZE=4096

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
