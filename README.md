# NotebookLLM⁻

> **NotebookLLM-minus** — NotebookLM, minus the parts that aren't built yet.

Upload your documents, ask questions, get answers grounded in *those* documents. A small,
deliberately readable Retrieval-Augmented Generation (RAG) backend built with **FastAPI**
and **MongoDB**.

It is being built incrementally. The current state covers the **ingestion pipeline**:
uploading documents per project, storing them, and processing (loading + chunking) them.
Embedding, vector search, and the answering endpoint are the remaining half — the status
enums for them exist as placeholders only.

## Table of contents

- [Current features](#current-features)
  - [Not yet implemented](#not-yet-implemented)
- [Tech stack](#tech-stack)
- [Data model](#data-model)
- [Project structure](#project-structure)
- [Setup](#setup)
  - [Configuration](#configuration)
- [API](#api)
  - [`GET /base/health`](#get-basehealth)
  - [`POST /data/upload/{project_id}`](#post-datauploadproject_id)
  - [`POST /process/{project_id}`](#post-processproject_id)
- [Error handling](#error-handling)
- [Logging](#logging)
- [Known limitations](#known-limitations)

## Current features

- **Health check** endpoint reporting app name and version.
- **Per-project file upload** (`/data/upload/{project_id}`) with content-type and size
  validation. The file is read in `MAX_FILE_CHUNK_SIZE` slices and stored as bytes on an
  **asset document in MongoDB** — nothing is written to disk. The project document is
  upserted on the way through, so uploading to a new `project_id` creates it.
- **Document processing** (`/process/{project_id}`): fetches an asset by `asset_id`,
  writes its bytes to a temporary file so LangChain's loaders can read it, splits the text
  into overlapping chunks with `RecursiveCharacterTextSplitter`, and persists the chunks to
  the `data_chunks` collection. `reset: true` clears the project's existing chunks first.
- **Indexes created at startup**, idempotently, for all three collections.

### Not yet implemented

- Embedding generation and vector storage (`ProcessStatus.EMBEDDING_FAILED`,
  `VECTOR_DB_ERROR` are placeholders).
- Retrieval and the `/answer`-style endpoint that makes this a notebook rather than a
  chunker.
- Citations pointing back at the source passage — the reason `chunk_order` and
  `chunk_metadata` are stored.
- Anything past `.pdf` and `.txt`, though `AssetType` and `FileExtension` already enumerate
  the formats to come.

## Tech stack

- Python 3.12
- FastAPI + Uvicorn
- pydantic-settings for configuration
- LangChain (`langchain-community`, `langchain-text-splitters`) for loading/splitting
- `pypdf` for PDF parsing
- MongoDB via `motor` (async driver), with `mongo-express` in the Compose file for a look
  inside
- stdlib `logging` with rotating file output and per-request correlation ids

## Data model

Three collections, and one identifier distinction worth internalising early:

| Collection    | Document    | Key fields                                                        |
| ------------- | ----------- | ----------------------------------------------------------------- |
| `projects`    | `Project`   | `project_id` (string, from the URL), `assets_ids`, `chunks_ids`    |
| `assets`      | `Asset`     | `asset_id` (uuid4 string), `asset_type`, `project_id`, `file_bytes` |
| `data_chunks` | `DataChunk` | `project_id` (**ObjectId** — the project's `_id`), `chunk_order`, `chunk_content` |

`Project.project_id` is the human string that appears in URLs. `DataChunk.project_id` is
the project's Mongo `_id`. Resolve the string to a project first, then pass `project.id`.

## Project structure

```
src/
├── main.py                     # FastAPI app, logging setup, Mongo lifespan, index creation
├── routes/
│   ├── base.py                 # /base/health
│   ├── data.py                 # /data/upload/{project_id}
│   ├── process.py              # /process/{project_id}
│   └── schemas/                # ProcessRequest, FileExtension enum
├── controllers/
│   ├── BaseController.py       # loads Settings, provides self.logger
│   ├── DataController.py       # upload validation (type, size)
│   ├── FileController.py       # disk storage — dormant, see note below
│   └── ProcessController.py    # loading + chunking documents
├── middleware/
│   └── request_logging.py      # request id + per-request access logging
├── exceptions.py               # domain errors + the status code each maps to
├── models/
│   ├── BaseModel.py            # binds a collection, provides self.logger
│   ├── ProjectModel.py         # projects: upsert, id bookkeeping, paged reads
│   ├── AssetModel.py           # assets: upsert, fetch by id/project/type
│   ├── ChunkModel.py           # data_chunks: batched insert, paged read, delete
│   └── db_schema/              # Project, Asset, DataChunk pydantic documents
├── enums/                      # FileStatus, ProcessStatus, AssetType, DatabaseCollection
├── utils/
│   ├── config.py               # Settings (pydantic-settings)
│   └── logging_config.py       # handlers, formatters, get_logger()
└── logs/                       # rotating log files (git-ignored)
```

`FileController` and its `assets/Files/` directory are the earlier disk-based storage path.
Nothing calls them since uploads moved into MongoDB; they're kept for reference and are the
obvious starting point if large files ever need GridFS or object storage instead.

## Setup

MongoDB first — the Compose file brings up Mongo and mongo-express:

```bash
cd Docker
cp .env.example .env        # set MONGO_INITDB_ROOT_USERNAME / _PASSWORD
docker compose up -d
```

Then the app, which uses [uv](https://docs.astral.sh/uv/):

```bash
cd src

# 1. Create your .env from the example
cp .env.example .env

# 2. Install dependencies
uv sync

# 3. Run the API
uv run uvicorn main:app --reload
```

The API will be available at `http://127.0.0.1:8000`, with interactive docs at
`http://127.0.0.1:8000/docs`, and mongo-express at `http://127.0.0.1:8080`.

### Configuration

`Settings` requires the following variables (all present in `.env.example`):

```dotenv
APPLICATION_NAME = 'NotebookLLM-minus'
APP_VERSION = "0.1"

ALLOWED_TYPES = ["application/pdf", "text/plain"]
MAX_FILE_SIZE = 10485760          # 10 MB — rejected at upload
MAX_ASSET_SIZE_BYTES = 10485760   # hard ceiling on the bytes stored per asset
MAX_FILE_CHUNK_SIZE = 1024        # streaming read chunk size in bytes

MONGO_URI = "mongodb://root:example@localhost:27017"
MONGO_DB_NAME = "notebookllm_minus"
```

`MAX_ASSET_SIZE_BYTES` is enforced by the `Asset` schema rather than the upload route, and
must stay comfortably under MongoDB's 16 MB per-document limit, since the bytes live inside
the asset document.

The logging variables are all **optional** — the defaults below apply when unset:

```dotenv
LOG_LEVEL = "INFO"          # DEBUG | INFO | WARNING | ERROR | CRITICAL
LOG_FORMAT = "text"         # text (human-readable) | json (structured)
LOG_TO_CONSOLE = true
LOG_TO_FILE = true
LOG_DIR = "logs"            # relative paths resolve against src/
LOG_FILE_NAME = "notebookllm-minus.log"
LOG_MAX_BYTES = 10485760    # 10 MB before rotating
LOG_BACKUP_COUNT = 5        # number of rotated files to keep
```

## API

### `GET /base/health`
Returns the application name and version.

```json
{ "application_name": "NotebookLLM-minus", "app_version": "0.1" }
```

### `POST /data/upload/{project_id}`
Multipart file upload. Validates content type (`application/pdf`, `text/plain`) and size,
then stores the bytes as an asset. Creates the project if it doesn't exist.

```bash
curl -X POST "http://127.0.0.1:8000/data/upload/1" \
  -F "file=@document.pdf"
```

Response:
```json
{
  "project_id": "1",
  "project_db_id": "68a1f0c3e4b0a1d2c3e4b0a1",
  "asset_id": "9f8e7d6c-5b4a-4c3d-9e8f-7a6b5c4d3e2f",
  "asset_db_id": "68a1f0c3e4b0a1d2c3e4b0a2",
  "status": "file uploaded successfully",
  "filename": "document.pdf",
  "asset_type": "pdf"
}
```

Keep the `asset_id` — it's what `/process` takes.

### `POST /process/{project_id}`
Loads and chunks a stored asset. The asset must belong to the project in the URL.

```bash
curl -X POST "http://127.0.0.1:8000/process/1" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "9f8e7d6c-5b4a-4c3d-9e8f-7a6b5c4d3e2f", "chunk_size": 500, "overlap_size": 50}'
```

| Field          | Type   | Default | Notes                                          |
| -------------- | ------ | ------- | ---------------------------------------------- |
| `asset_id`     | string | —       | required, the uuid returned by upload          |
| `chunk_size`   | int    | 100     | characters per chunk, must be > 0              |
| `overlap_size` | int    | 20      | character overlap, must be < `chunk_size`      |
| `reset`        | bool   | false   | delete the project's existing chunks first     |

Constraint violations (including an explicit `null`) are rejected by the request schema
with a `422` naming the offending field.

The response reports `chunks_created` / `chunks_saved` and currently echoes every chunk's
full text — see [Known limitations](#known-limitations).

## Error handling

One rule, applied top to bottom: **low-level code raises, the boundary decides.**

- **Models and controllers** raise a typed error from `exceptions.py` and stay silent about
  failures. They don't know about HTTP, don't catch broadly, and always chain
  (`raise StorageError(...) from exc`) so the driver's traceback survives.
- **Routes** contain no `try`/`except`. They do the work and let errors propagate.
- **`main.py`** holds the only handler. It reads `status_code` off the exception, logs it
  once, and returns `{"detail": "<message>"}`.

Every error below inherits from `NotebookLLMError`, which is what the handler is registered
against.

| Exception                   | Status | Raised when                                      |
| --------------------------- | ------ | ------------------------------------------------ |
| `InvalidFileError`          | 400    | upload has a disallowed content type or is too large |
| `UnsupportedFileTypeError`  | 400    | no loader for the file's extension               |
| `InvalidInputError`         | 400    | the asset belongs to another project, or has no stored bytes |
| `ProjectNotFoundError`      | 404    | no project matches the `project_id`              |
| `AssetNotFoundError`        | 404    | no asset matches the `asset_id`                  |
| `UploadedFileNotFoundError` | 404    | *(dormant — belongs to the disk-storage path)*   |
| `StorageError`              | 503    | MongoDB unreachable or rejected the operation    |
| `FileStorageError`          | 500    | *(dormant — writing an upload to disk failed)*   |
| `ExtractionError`           | 500    | a loader could not read the file                 |
| `ChunkingError`             | 500    | the splitter rejected the document               |

New errors only need a class with a `status_code`; no handler changes. Errors below 500 log
a single WARNING line (a stack trace would be noise); 500s log the full traceback including
the chained cause. Anything unanticipated becomes `{"detail": "Internal server error"}` —
the traceback goes to the log, never to the client.

## Logging

Everything logs through the stdlib `logging` module. `utils/logging_config.py` owns the
one-time setup, which `main.py` performs at import time — after uvicorn installs its own
config, so the project's format applies to uvicorn's lines too.

**Using it.** Modules take a logger named after themselves; controllers and models inherit
one from their base class:

```python
from utils import get_logger
logger = get_logger(__name__)

logger.info("Extracted %d document(s) from %s", len(docs), file_path.name)
logger.exception("Chunking failed for %s", file_path)   # inside an `except` block
```

**Output.** Two handlers, both configurable: the console, and a `RotatingFileHandler`
writing to `src/logs/notebookllm-minus.log` (rotates at `LOG_MAX_BYTES`, keeps
`LOG_BACKUP_COUNT` files). `LOG_FORMAT = "json"` swaps both to one JSON object per line,
including anything passed via `extra={...}`.

**Request correlation.** `RequestLoggingMiddleware` does two things nothing else does: it
assigns each request a short id — reusing an inbound `X-Request-ID` header when present,
and echoing it back on the response — and it emits the access line (status + duration).
It replaces uvicorn's access log, which is disabled in `logging_config.py` to avoid a
second, id-less copy of every request.

Every line logged while handling a request carries that id, so one request's work is
greppable end to end:

```
2026-08-11 12:14:02 | INFO | 86839af58919 | models.AssetModel:70 | Saved asset '9f8e7d6c-…' (_id=68a1f0c3e4b0a1d2c3e4b0a2)
2026-08-11 12:14:02 | INFO | 86839af58919 | middleware.request_logging:67 | <- POST /data/upload/1 200 in 12.4ms
```

Completion lines are INFO for 2xx/3xx, WARNING for 4xx, ERROR for 5xx. Lines logged outside
a request (startup, shutdown) show `-` as the id.

At INFO a request logs only what its outcome was. `LOG_LEVEL = "DEBUG"` adds the inbound
`-> METHOD /path` line (with client and query string) plus each layer's parameters — useful
when a request hangs, since a hung request has no completion line at INFO.

## Known limitations

- The whole upload is buffered in memory and stored inside a single MongoDB document, which
  caps a source at well under the 16 MB BSON limit. GridFS or object storage is the way out.
- Chunks are persisted to MongoDB, but `/process` still returns every chunk's full text in
  the response as well. For a real document that payload is large and redundant.
- `reset` deletes then re-inserts without a transaction (standalone MongoDB has none), so a
  failure mid-insert leaves the project with fewer chunks than it started with.
- `ChunkModel.ensure_indexes()` builds the `(project_id, chunk_order)` index that paged
  reads actually sort on, but startup creates `(project_id, created_at)` instead and never
  calls it.
- Both routes overwrite the project's `name` and `description` with placeholders derived
  from the current file on every upload and process call.
- Embedding generation, vector storage, and retrieval/answering are not yet implemented —
  which is, for now, the "minus".
