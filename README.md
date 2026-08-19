# NotebookLLM⁻

> **NotebookLLM-minus** — NotebookLM, minus the parts that aren't built yet.

Upload your documents, ask questions, get answers grounded in *those* documents. A small,
deliberately readable Retrieval-Augmented Generation (RAG) backend built with **FastAPI**
and **MongoDB**.

It runs end to end: upload a document, ask a question in a chat, and get a streamed answer
grounded in that document with citations back to it. A chat with no documents is simply an
ordinary assistant. Everything is behind swappable providers chosen from `.env`, and the
whole stack — chat model, embedding model, vector store — runs **offline against a local
Ollama** if you want it to.

There is a web UI at `/`, in English and Arabic (with a right-to-left layout), and the models
are picked from whatever Ollama has installed rather than pinned in code.

## Table of contents

- [Current features](#current-features)
  - [Not yet implemented](#not-yet-implemented)
- [Tech stack](#tech-stack)
- [How it fits together](#how-it-fits-together)
- [Data model](#data-model)
- [Providers](#providers)
- [Project structure](#project-structure)
- [Setup](#setup)
  - [Configuration](#configuration)
- [API](#api)
  - [`GET /base/health`](#get-basehealth)
  - [`POST /data/upload/{project_id}`](#post-datauploadproject_id)
  - [`POST /process/{project_id}`](#post-processproject_id)
  - [`GET /nlp/health`](#get-nlphealth)
  - [`POST /nlp/index/push/{project_id}`](#post-nlpindexpushproject_id)
  - [`GET /nlp/index/info/{project_id}`](#get-nlpindexinfoproject_id)
  - [`POST /nlp/index/search/{project_id}`](#post-nlpindexsearchproject_id)
  - [Chat: users, sessions and answers](#chat-users-sessions-and-answers)
- [Error handling](#error-handling)
- [Logging](#logging)
- [Known limitations](#known-limitations)

## Current features

- **Health check** endpoint reporting app name and version.
- **Per-project file upload** (`/data/upload/{project_id}`) with content-type and size
  validation. The file is read in `MAX_FILE_CHUNK_SIZE` slices and stored as bytes on an
  **asset document in MongoDB** — nothing is written to disk. The project document is
  upserted on the way through, so uploading to a new `project_id` creates it.
- **Document processing** (`/process/{project_id}`): fetches one asset by `asset_id`, or
  every asset in the project when `asset_id` is omitted, writes each one's bytes to a
  temporary file so LangChain's loaders can read it, splits the text into overlapping chunks
  with `RecursiveCharacterTextSplitter`, and persists them to the `data_chunks` collection.
- **Idempotent ingestion**: an asset that already has chunks is skipped, so `/process` can be
  re-run over a project to pick up only what is new. `reset: true` re-ingests instead —
  scoped to the assets named in the request, never the whole project.
- **Indexes created at startup**, idempotently, for all three collections.
- **Embedding and vector indexing** (`/nlp/index/push/{project_id}`): streams a project's
  chunks out of MongoDB in batches, embeds each batch, and upserts them into a per-project
  Qdrant collection. Idempotent — re-running overwrites the same points rather than
  duplicating them, because each is keyed on its chunk's Mongo `_id`.
- **Semantic search** (`/nlp/index/search/{project_id}`) returning ranked passages with the
  metadata a citation needs.
- **Live health probe** (`/nlp/health`) that actually calls MongoDB, the embedding model and
  the vector store, rather than reporting what is configured.
- **Swappable providers** for text generation (Anthropic, OpenAI, Google, Cohere, Ollama),
  embeddings (OpenAI, Google, Cohere, Ollama) and vector storage (Qdrant) — one abstract
  interface each, one implementation per vendor, and a factory that reads the backend name
  out of `.env`. Built once at startup and hung off the app object. See
  [Providers](#providers).

- **Grounded chat** (`/chat`): users → sessions → chats → messages in MongoDB, with answers
  streamed token by token over SSE. A chat holding documents answers from them **with
  citations**; a chat without them is an ordinary assistant. Same endpoint, same code path.
- **Reasoning shown live**: models that think (`gemma4`) stream their scratchpad into a panel
  that collapses the moment the answer starts. The reasoning is displayed, not stored.
- **Prompts in English and Arabic** under `templates/locales/<lang>/`, one file per feature.
- **Web UI** at `/` — Jinja templates plus ES modules, no build step. Markdown renders live as
  it streams, and the language toggle flips the whole layout to RTL.
- **Models chosen at runtime** from whatever Ollama has pulled, per chat. Switching a chat's
  embedding model re-indexes its documents automatically.

### Not yet implemented

- Highlighting the exact sentence a citation came from — chunks carry `asset_id` and
  `chunk_order`, so the anchor exists; nothing renders it yet.
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
- Native vendor SDKs for the providers — `anthropic`, `openai`, `google-genai`, `cohere`,
  `ollama` — rather than a wrapper library, so each backend's real API is visible at the
  one place it is used
- `qdrant-client` for vector storage, embedded (on-disk, no server) by default
- stdlib `logging` with rotating file output and per-request correlation ids

## How it fits together

Four layers, each only talking to the one below it. Nothing above `factories/` names a vendor,
which is what makes the models swappable at runtime.

```mermaid
flowchart TB
    subgraph browser["Browser"]
        UI["Jinja page + ES modules<br/>web/templates · web/static"]
    end

    subgraph routes["Routes — HTTP only, no logic"]
        R1["/chat<br/>users · sessions · chats · SSE"]
        R2["/data · /process<br/>upload · chunk"]
        R3["/nlp<br/>index · search · health"]
    end

    subgraph controllers["Controllers — the actual work"]
        C1["ChatController<br/>retrieve → prompt → stream"]
        C2["ProcessController<br/>load → split"]
        C3["NLPController<br/>embed → upsert → search"]
        C4["ModelController<br/>probe installed models"]
    end

    subgraph providers["factories/ — swappable backends"]
        P1["LLMChattingInterface<br/>anthropic · openai · google<br/>cohere · ollama"]
        P2["LLMEmbeddingInterface<br/>openai · google<br/>cohere · ollama"]
        P3["VectorDBInterface<br/>qdrant"]
    end

    subgraph stores["Storage"]
        M[("MongoDB<br/>users · sessions · chats<br/>messages · projects<br/>assets · data_chunks")]
        Q[("Qdrant<br/>one collection per chat")]
    end

    T["templates/locales/{en,ar}<br/>system · document · footer prompts"]

    UI -->|"fetch + SSE"| R1
    UI --> R2
    UI --> R3
    R1 --> C1
    R1 --> C4
    R2 --> C2
    R3 --> C3
    C1 --> T
    C1 --> C3
    C1 --> P1
    C3 --> P2
    C3 --> P3
    C2 --> M
    C1 --> M
    P3 --> Q
    P1 -.->|"local"| O(["Ollama"])
    P2 -.->|"local"| O
```

### What happens when you ask a question

The same endpoint serves both kinds of chat. Whether the answer is grounded is decided by
whether the chat has vectors, not by a flag someone set.

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant R as POST /chat/chats/{id}/message
    participant CC as ChatController
    participant V as Qdrant
    participant L as Chat model

    U->>R: { text }
    R->>R: read history (before storing the question,<br/>or it arrives in context twice)
    R->>R: store user turn
    R->>CC: answer_stream(question, history, lang)
    CC->>V: collection has points?
    alt has documents
        V-->>CC: yes
        CC->>V: search_by_vector(question, top_k)
        V-->>CC: passages + metadata
        CC->>CC: rag prompts + numbered documents
    else no documents
        V-->>CC: no
        CC->>CC: plain chat prompt
    end
    CC-->>U: meta { grounded, citations }
    CC->>L: stream
    loop while generating
        L-->>CC: thinking / content pieces
        CC-->>U: thinking · delta
    end
    CC-->>U: done
    R->>R: store assistant turn (answer only,<br/>not the scratchpad)
```

### Documents

`chat_id` **is** the `project_id`, so a chat is its own document space and reuses the whole
ingestion path unchanged.

```mermaid
flowchart LR
    A["POST /chat/chats/{id}/documents"] --> B["validate<br/>type + size"]
    B --> C["store bytes<br/>assets"]
    C --> D["load + split<br/>data_chunks"]
    D --> E["embed batch<br/>embedding model"]
    E --> F["upsert<br/>collection project_{chat_id}"]
    F --> G["has_documents = true"]
```

Point ids are `uuid5(asset_id + chunk_order)`, which is stable across re-processing, so
re-indexing overwrites in place instead of leaving orphaned vectors behind.

## Data model

Three collections, and one identifier distinction worth internalising early:

| Collection    | Document    | Key fields                                                        |
| ------------- | ----------- | ----------------------------------------------------------------- |
| `projects`    | `Project`   | `project_id` (string, from the URL), `assets_ids`, `chunks_ids`    |
| `assets`      | `Asset`     | `asset_id` (uuid4 string), `asset_type`, `project_id`, `file_bytes` |
| `data_chunks` | `DataChunk` | `project_id` (**ObjectId** — the project's `_id`), `asset_id`, `chunk_order`, `chunk_content` |

```mermaid
erDiagram
    USERS ||--o{ SESSIONS : owns
    SESSIONS ||--o{ CHATS : groups
    CHATS ||--o{ MESSAGES : holds
    CHATS ||--|| PROJECTS : "same id"
    PROJECTS ||--o{ ASSETS : "uploaded into"
    ASSETS ||--o{ DATA_CHUNKS : "split into"
    DATA_CHUNKS ||--o| VECTORS : "embedded as"

    USERS {
        string user_id PK
    }
    SESSIONS {
        string session_id PK
        string user_id FK
    }
    CHATS {
        string chat_id PK "also the project_id"
        string session_id FK
        string title
        string lang "en or ar"
        string generation_model "null means env default"
        string embedding_model "null means env default"
        int embedding_dimensions
        bool has_documents
    }
    MESSAGES {
        string message_id PK
        string chat_id FK
        string role "user assistant or system"
        string content
        array citations "only on grounded answers"
    }
    PROJECTS {
        string project_id PK
    }
    ASSETS {
        string asset_id PK
        string project_id FK
        bytes file_bytes
    }
    DATA_CHUNKS {
        objectid project_id FK "the project _id not the string"
        string asset_id FK
        int chunk_order "restarts per document"
        string chunk_content
    }
    VECTORS {
        string point_id PK "uuid5 of asset_id and chunk_order"
        int size "the embedding model width"
    }
```

`Project.project_id` is the human string that appears in URLs. `DataChunk.project_id` is
the project's Mongo `_id`. Resolve the string to a project first, then pass `project.id`.

`DataChunk.asset_id` is the string `Asset.asset_id`, not an ObjectId. It exists because
`chunk_order` numbers from 0 within each document: without it, two sources in one project
both claim orders `0..N` and a project-wide sort interleaves them.

Indexes are created at startup in `main.py`'s lifespan — idempotent, so restarting is safe:

| Collection    | Index                         | Serves                                     |
| ------------- | ----------------------------- | ------------------------------------------ |
| `projects`    | `project_id` (unique)          | every lookup; enforces one doc per project |
| `assets`      | `project_id, created_at desc`  | listing a project's assets                 |
| `data_chunks` | `project_id, created_at desc`  | project-wide chunk reads                   |
| `data_chunks` | `project_id, asset_id`         | the per-asset skip check and reset delete  |

## Providers

Three swappable subsystems live under `src/factories/`. Each is one abstract interface, one
implementation per vendor, and a factory that turns a backend name from `.env` into a
configured instance. Nothing above this layer names a vendor, so changing provider is a
config edit.

| Subsystem | Backends | Interface |
| --------- | -------- | --------- |
| `llmchatting`  | `anthropic`, `openai`, `google`, `cohere`, `ollama` | `generate_text(prompt, chat_history, max_tokens, temperature) -> str` |
| `llmembedding` | `openai`, `google`, `cohere`, `ollama`              | `embed(texts, input_type) -> list[list[float]]` |
| `vectordb`     | `qdrant`                                            | `create_collection`, `insert_many`, `search_by_vector`, … |

**Anthropic is absent from the embedding list on purpose** — it ships no embeddings API, so
`EMBEDDING_BACKEND` validates against a different set than `GENERATION_BACKEND`. The two need
not agree: local Ollama embeddings with hosted answers is a reasonable setup.

**One neutral message format.** Callers build `{"role": ..., "content": ...}` dicts using
`ChatRole`, and each provider translates on the way out — Anthropic and Google lift the
system turn into a separate argument, Google renames `assistant` to `model`, OpenAI, Cohere
and Ollama take it inline. `generate_text()` itself is concrete on the interface: it
normalizes the messages, logs, times the call, and delegates to the provider's
`_generate_text()`. Same shape for `embed()` / `_embed()`, which also owns the empty-input
short circuit and result validation. A provider therefore cannot forget to log, and the five
of them log identical fields.

**Embeddings are batch-first** (`list[str]` in, vectors out, in input order) because an asset
embeds all its chunks at once. Two failures are caught at the boundary rather than
downstream: a count mismatch (vectors are zipped against chunk ids by position, so a dropped
one silently misaligns everything after it) and a width mismatch against
`EMBEDDING_MODEL_SIZE` (baked into the Qdrant collection at creation, so the real error would
otherwise name the collection, not the misconfigured setting).

**Keys are checked at startup, not on first use.** A blank key for the selected backend
raises `UnsupportedProviderError` during the lifespan, so the app refuses to start rather
than failing on the first question a user asks. Ollama is exempt — it runs locally and
authenticates by host (`OLLAMA_BASE_URL`).

**Qdrant runs embedded by default**: `VECTOR_DB_PATH` is a local directory and no server is
needed. Set `VECTOR_DB_URL` to point at a server or Cloud cluster instead; the two are
mutually exclusive and setting both is rejected at construction. Point ids are UUIDs, so
Mongo ObjectIds are hashed through `uuid5` — deterministically, meaning re-processing an
asset overwrites its vectors instead of duplicating them.

```python
# how they are built — see main.py's lifespan
app.generation_client = LLMChattingFactory(SETTINGS).create()        # GENERATION_BACKEND
app.embedding_client  = LLMEmbeddingFactory(SETTINGS).create()       # EMBEDDING_BACKEND
app.vectordb_client   = VectorDBFactory(SETTINGS).create()           # VECTOR_DB_BACKEND
await app.vectordb_client.connect()

# or override explicitly, by enum or by (messy) string
LLMChattingFactory(SETTINGS).create(LLMChattingProvider.OLLAMA)
LLMChattingFactory(SETTINGS).create("  Cohere ")
```

## Project structure

```
src/
├── main.py                     # FastAPI app, logging setup, Mongo lifespan, index creation
├── routes/
│   ├── base.py                 # /base/health
│   ├── chat.py                 # /chat — conversations + SSE answers
│   ├── ui.py                   # GET / — the Jinja page
│   ├── data.py                 # /data/upload/{project_id}
│   ├── process.py              # /process/{project_id}
│   └── schemas/                # ProcessRequest, FileExtension enum
├── controllers/
│   ├── BaseController.py       # loads Settings, provides self.logger
│   ├── ChatController.py       # retrieve → prompt → stream the answer
│   ├── DataController.py       # upload validation (type, size)
│   ├── ModelController.py      # what Ollama has, and what each model can do
│   ├── FileController.py       # disk storage — dormant, see note below
│   └── ProcessController.py    # loading + chunking documents
├── middleware/
│   └── request_logging.py      # request id + per-request access logging
├── exceptions.py               # domain errors + the status code each maps to
├── models/
│   ├── BaseModel.py            # binds a collection, provides self.logger
│   ├── ProjectModel.py         # projects: upsert, id bookkeeping, paged reads
│   ├── ConversationModels.py   # UserModel, SessionModel, ChatModel, MessageModel
│   ├── AssetModel.py           # assets: upsert, fetch by id/project/type
│   ├── ChunkModel.py           # data_chunks: batched insert, paged read, per-asset delete
│   └── db_schema/              # Project, Asset, DataChunk pydantic documents
├── factories/                  # swappable providers, see Providers above
│   ├── llmchatting/            # LLMChattingInterface + 5 providers + LLMChattingFactory
│   ├── llmembedding/           # LLMEmbeddingInterface + 4 providers + LLMEmbeddingFactory
│   ├── vectordb/               # VectorDBInterface + QdrantProvider + VectorDBFactory
│   └── cohere_support.py       # shared shutdown helper (Cohere's client has no close())
├── templates/                  # model-facing prompts (NOT html)
│   ├── template_parser.py
│   └── locales/{en,ar}/        # rag.py + chat.py — one file per feature
├── web/                        # user-facing UI
│   ├── templates/              # base.html, index.html, partials/
│   └── static/{css,js}/        # 3 stylesheets, 7 ES modules, no build step
├── enums/                      # FileStatus, ProcessStatus, AssetType, DatabaseCollection,
│                               # LLMChattingProvider, LLMEmbeddingProvider, VectorDBProvider
├── utils/
│   ├── config.py               # Settings (pydantic-settings)
│   └── logging_config.py       # handlers, formatters, get_logger()
├── qdrant_db/                  # embedded vector store (git-ignored)
└── logs/                       # rotating log files (git-ignored)
```

**Two directories are called templates, on purpose.** `templates/` holds prompts sent *to the
model*; `web/templates/` holds Jinja HTML sent *to the browser*. Different audiences, so they
never share a file.

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

Then [Ollama](https://ollama.com), since the default configuration runs the whole stack
locally with no API keys:

```bash
ollama serve                      # if it isn't already running
ollama pull gemma4:e4b            # GENERATION_MODEL_ID — reasons, and streams its thinking
ollama pull qwen3-embedding:8b    # EMBEDDING_MODEL_ID — 4096 dimensions, multilingual
```

> **Chat models generally cannot embed.** Ollama rejects `gemma3`/`gemma4` with *"this model
> does not support embeddings"*, so the two roles need different models. `GET /chat/models`
> probes every installed model and tells you which can do which.

> **`qwen3-embedding` is multilingual**, which is what makes Arabic retrieval work: the same
> sentence in English and Arabic embeds to a cosine similarity of **0.81**, against 0.31 for
> unrelated English text. An English-only model like `nomic-embed-text` (768d) works fine if
> your documents are English — just set `EMBEDDING_MODEL_SIZE` to match.

To use a hosted provider instead, set `GENERATION_BACKEND` / `EMBEDDING_BACKEND` and the
matching API key — see [Configuration](#configuration).

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
`GET /nlp/health` is the fastest way to confirm all three backends are actually reachable.

One note on running it: **skip `--reload` when exercising the vector endpoints.** Embedded
Qdrant takes an exclusive lock on `VECTOR_DB_PATH`, and reload's double-start collides on it.
Point `VECTOR_DB_URL` at a Qdrant server if you want reload.

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

# --- providers ---
GENERATION_BACKEND = "anthropic"    # anthropic | openai | google | cohere | ollama
EMBEDDING_BACKEND  = "openai"       # openai | google | cohere | ollama

GENERATION_MODEL_ID  = "gemma4:e4b"
EMBEDDING_MODEL_ID   = "qwen3-embedding:8b"
EMBEDDING_MODEL_SIZE = 4096
```

These are only the **defaults**. Any chat can name its own models at runtime
(`PATCH /chat/chats/{id}/models`), so nothing is pinned to what is written here.

```dotenv
GENERATION_DEFAULT_MAX_TOKENS = 4096   # reasoning spends this budget too — see below
GENERATION_THINKING = "true"           # true|false|low|medium|high
DEFAULT_LANG        = "en"             # en | ar
CHAT_HISTORY_LIMIT  = 10               # prior turns sent as context
RETRIEVAL_TOP_K     = 5                # chunks retrieved per grounded answer
```

**Give the token budget room.** A reasoning model spends `GENERATION_DEFAULT_MAX_TOKENS` on
its scratchpad *before* the answer. Measured on `gemma4:e4b`: at a 200-token cap it produced
168 thinking chunks and **no answer at all**; at 1024 the reasoning took 812 characters and
left the reply cramped. 4096 leaves room for both.

`MAX_ASSET_SIZE_BYTES` is enforced by the `Asset` schema rather than the upload route, and
must stay comfortably under MongoDB's 16 MB per-document limit, since the bytes live inside
the asset document.

Supply the API key for whichever backends you selected — only those are checked, and the
check happens at startup:

```dotenv
ANTHROPIC_API_KEY = ""
OPENAI_API_KEY    = ""
GOOGLE_API_KEY    = ""
COHERE_API_KEY    = ""
# OPENAI_API_BASE_URL = ""                   # for OpenAI-compatible endpoints
OLLAMA_BASE_URL = "http://localhost:11434"   # ollama takes no key, just a host
```

`EMBEDDING_MODEL_SIZE` **must match the model** — it is baked into the Qdrant collection at
creation, so a mismatch means every insert is rejected and changing it later requires
rebuilding the collection. `.env.example` carries the sizes for each supported model
(`text-embedding-3-small` 1536, `embed-v4.0` 1536, `gemini-embedding-001` 3072,
`nomic-embed-text` 768, …). The hosted providers are asked for that width explicitly; Ollama
cannot be, so the first `embed()` call verifies it and fails with the actual width.

Vector storage defaults to an embedded, on-disk Qdrant — nothing extra to run:

```dotenv
VECTOR_DB_BACKEND = "qdrant"
VECTOR_DB_PATH    = "qdrant_db"       # relative paths resolve against src/
# VECTOR_DB_URL     = "http://localhost:6333"   # a server *instead of* VECTOR_DB_PATH
# VECTOR_DB_API_KEY = ""
VECTOR_DB_DISTANCE_METHOD = "cosine"  # cosine | dot | euclid — fixed at creation
```

All four backend names are validated against their enums when `Settings` loads, so a typo
fails immediately with the list of valid options rather than at first use.

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
Loads and chunks the project's stored assets. **`asset_id` is optional**: pass it to process
one document, omit it to process every asset in the project, oldest first.

```bash
# one document
curl -X POST "http://127.0.0.1:8000/process/1" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": "9f8e7d6c-5b4a-4c3d-9e8f-7a6b5c4d3e2f", "chunk_size": 500, "overlap_size": 50}'

# every document in the project
curl -X POST "http://127.0.0.1:8000/process/1" \
  -H "Content-Type: application/json" \
  -d '{"chunk_size": 500, "overlap_size": 50}'
```

| Field          | Type   | Default | Notes                                                     |
| -------------- | ------ | ------- | --------------------------------------------------------- |
| `asset_id`     | string | `null`  | optional; omit to process the whole project               |
| `chunk_size`   | int    | 100     | characters per chunk, must be > 0                         |
| `overlap_size` | int    | 20      | character overlap, must be < `chunk_size`                 |
| `reset`        | bool   | false   | re-ingest assets that are already chunked                 |

**Processing is idempotent.** Every asset is chunked at most once: an asset that already has
chunks is skipped and reported as such, so re-running `/process` over a project only picks up
what is new. `reset: true` deletes that asset's existing chunks and re-chunks it.

Both behaviours are scoped to the assets **named in the request** — resetting a single
`asset_id` leaves every other asset's chunks untouched.

Constraint violations (including an explicit `null` on a non-optional field) are rejected by
the request schema with a `422` naming the offending field.

Every asset is validated before anything is written — a request naming an asset that belongs
to another project, or one with no stored bytes, fails with a `400` having inserted no
chunks. A project with no assets at all is a `404`, not an empty success.

The response carries a `results` entry per asset:

```json
{
  "project_id": "1",
  "chunk_size": 500,
  "overlap_size": 50,
  "status": "processing completed successfully",
  "reset": false,
  "assets_found": 2,
  "assets_processed": 1,
  "assets_skipped": 1,
  "results": [
    {
      "asset_id": "9f8e7d6c-…",
      "asset_name": "document.pdf",
      "project_object_id": "68a1f0c3e4b0a1d2c3e4b0a1",
      "status": "processed",
      "chunks_created": 34,
      "chunks_saved": 34,
      "chunks": [{ "content": "…", "metadata": { "source": "document.pdf", "page": 0 } }]
    },
    {
      "asset_id": "1a2b3c4d-…",
      "asset_name": "notes.txt",
      "project_object_id": "68a1f0c3e4b0a1d2c3e4b0a1",
      "status": "skipped",
      "reason": "already chunked; pass reset=true to re-ingest",
      "chunks_created": 0,
      "chunks_saved": 0,
      "chunks": []
    }
  ]
}
```

`chunk_order` counts from 0 **within each document**, so `asset_id` on the chunk is what
distinguishes two sources in the same project. `metadata.source` is the document's name —
the loaders stamp it with a temporary file path, which `ProcessController.process_bytes`
overwrites on the way out.

The response still echoes every chunk's full text, for every asset — see
[Known limitations](#known-limitations).

### `GET /nlp/health`
Live readiness probe. Calls all three backends rather than echoing configuration, so it
catches "`ollama serve` isn't running" and "the model was never pulled". Returns **200** when
every check passes and **503** when any fails, so it doubles as a container probe.

```bash
curl localhost:8000/nlp/health
```

```json
{ "status": "ok", "application": "NotebookLLM-minus", "version": "0.1",
  "generation_model": "gemma3:4b",
  "checks": {
    "mongodb":   { "status": "ok", "latency_ms": 0.8, "database": "notebookllm_minus" },
    "embedding": { "status": "ok", "latency_ms": 41.7, "provider": "ollama",
                   "model": "nomic-embed-text", "dimensions": 768 },
    "vectordb":  { "status": "ok", "latency_ms": 0.0, "backend": "qdrant", "collections": 2 } } }
```

A failed check keeps the others intact and reports its own error, so the body says *which*
dependency is down:

```json
{ "status": "degraded",
  "checks": { "mongodb": { "status": "ok", "...": "..." },
              "embedding": { "status": "error", "error": "Ollama embedding failed at ..." },
              "vectordb": { "status": "ok", "...": "..." } } }
```

### `POST /nlp/index/push/{project_id}`
Embeds the project's stored chunks and upserts them into its Qdrant collection. Requires the
project to have been `/process`-ed first — indexing nothing is a 404, not an empty success.

```bash
# every chunk in the project
curl -X POST localhost:8000/nlp/index/push/demo \
  -H 'Content-Type: application/json' -d '{}'

# just one document, rebuilding the collection from scratch
curl -X POST localhost:8000/nlp/index/push/demo \
  -H 'Content-Type: application/json' \
  -d '{"asset_id": "a0b15c0a-...", "reset": true, "batch_size": 64}'
```

| Field | Default | Meaning |
| ----- | ------- | ------- |
| `asset_id` | `null` | Index only this asset's chunks; every asset when omitted |
| `reset` | `false` | Drop and rebuild the collection first |
| `batch_size` | `64` | Chunks per embed + upsert round trip (1–512) |

```json
{ "project_id": "demo", "collection": "project_demo", "asset_id": null, "reset": false,
  "status": "processing completed successfully",
  "chunks_found": 8, "chunks_indexed": 8, "batches": 1,
  "embedding_model": "nomic-embed-text", "vector_size": 768 }
```

`chunks_found` counts the whole project; `chunks_indexed` counts what this call wrote, which
is narrower when `asset_id` is set.

**Re-running without `reset` is safe.** Points are keyed on each chunk's Mongo `_id`, hashed
deterministically to a UUID, so a second push overwrites rather than duplicates.

### `GET /nlp/index/info/{project_id}`
What the vector store currently holds. Returns 200 with `"indexed": false` for a project that
was never pushed — "is this indexed?" is the question, and no is an answer.

```json
{ "project_id": "demo", "collection": "project_demo", "indexed": true,
  "chunks_in_db": 8, "embedding_model": "nomic-embed-text",
  "points_count": 8, "vector_size": 768, "distance": "Cosine", "status": "green" }
```

`chunks_in_db` (MongoDB) against `points_count` (Qdrant) is the staleness check: a gap means
the project was chunked again without being re-pushed.

### `POST /nlp/index/search/{project_id}`
Semantic search. The query is embedded with the *query* input type, not the document one —
asymmetric models treat the two differently and mixing them quietly costs recall.

```bash
curl -X POST localhost:8000/nlp/index/search/demo \
  -H 'Content-Type: application/json' \
  -d '{"text": "how do plants make energy from light", "limit": 3}'
```

```json
{ "project_id": "demo", "collection": "project_demo",
  "query": "how do plants make energy from light", "limit": 3, "hits_found": 3,
  "results": [
    { "id": "9c1e...", "score": 0.7792,
      "text": "Photosynthesis is the process by which green plants convert sunlight ...",
      "metadata": { "project_id": "6a84...", "asset_id": "a0b1...",
                    "chunk_order": 2, "source": "doc.txt" } } ] }
```

Searching a project that was never indexed returns **404** naming the push endpoint, rather
than an empty result set that looks like a bad query.

### Chat: users, sessions and answers

No sign-in. `POST /chat/users` mints an opaque id the browser keeps in `localStorage`;
"current user" reuses it. It scopes conversations, it does not prove anything.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `POST` | `/chat/users` | Mint a user |
| `GET` | `/chat/users/{user_id}` | Confirm a returning user (404 → start fresh) |
| `POST` | `/chat/users/{user_id}/sessions` | New session |
| `GET` | `/chat/users/{user_id}/sessions` | List sessions |
| `POST` | `/chat/sessions/{session_id}/chats` | New chat (`title`, `lang`) |
| `GET` | `/chat/sessions/{session_id}/chats` | List chats |
| `GET` | `/chat/chats/{chat_id}` | One chat + `grounded` + models in use |
| `GET` | `/chat/chats/{chat_id}/messages` | Full history |
| `POST` | `/chat/chats/{chat_id}/documents` | Upload → chunk → index, one call |
| `PATCH` | `/chat/chats/{chat_id}/models` | Switch this chat's models |
| `POST` | `/chat/chats/{chat_id}/message` | **SSE** — the answer |
| `GET` | `/chat/models` | Installed models, split by capability |

#### The answer stream

```bash
curl -N -X POST localhost:8000/chat/chats/$CHAT/message \
  -H 'Content-Type: application/json' \
  -d '{"text": "What do you think of this CV?"}'
```

```
data: {"type":"meta","grounded":true,"citations":[{"num":1,"source":"cv.txt","chunk_order":0,"score":0.71}]}
data: {"type":"thinking","text":"Analyse the request: the user wants an assessment…"}
data: {"type":"delta","text":"The CV presents a strong profile"}
data: {"type":"done"}
```

`meta` arrives first so sources can be shown before any text. `thinking` appears only for
reasoning models. Concatenating the `delta` frames gives the answer, which is what gets
stored — the scratchpad is not persisted. A mid-stream failure arrives as
`{"type":"error","detail":"…"}` rather than an HTTP status, because the response has already
begun.

#### Choosing models

`GET /chat/models` probes each installed model by trying to embed with it — Ollama's tag list
does not say which can, and the probe also returns the vector width:

```json
{ "chat": [{"id": "gemma4:e4b", "size_gb": 9.61, "parameters": "8.0B"}, …],
  "embedding": [{"id": "qwen3-embedding:8b", "dimensions": 4096},
                {"id": "nomic-embed-text:latest", "dimensions": 768}],
  "current": {"chat": "gemma4:e4b", "embedding": "qwen3-embedding:8b",
              "embedding_dimensions": 4096} }
```

```bash
curl -X PATCH localhost:8000/chat/chats/$CHAT/models \
  -H 'Content-Type: application/json' \
  -d '{"generation_model": "qwen3.6:27b", "embedding_model": "nomic-embed-text:latest"}'
```

Changing the **embedding** model rebuilds that chat's collection at the new width and
re-embeds its chunks from MongoDB — reported back as `reindexed_chunks`. A model that cannot
embed is refused with a 400 rather than accepted and failed later.

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
| `UnsupportedProviderError`  | 400    | unknown backend name, or the selected backend's API key is blank |
| `ProjectNotFoundError`      | 404    | no project matches the `project_id`, or it has no assets to process |
| `AssetNotFoundError`        | 404    | no asset matches the `asset_id`                  |
| `UploadedFileNotFoundError` | 404    | *(dormant — belongs to the disk-storage path)*   |
| `LLMProviderError`          | 502    | an LLM vendor failed, timed out, or returned no usable text |
| `StorageError`              | 503    | MongoDB unreachable or rejected the operation    |
| `VectorDBError`             | 503    | Qdrant unreachable or rejected the operation     |
| `FileStorageError`          | 500    | *(dormant — writing an upload to disk failed)*   |
| `ExtractionError`           | 500    | a loader could not read the file                 |
| `ChunkingError`             | 500    | the splitter rejected the document               |
| `EmbeddingError`            | 500    | an embedding call failed, or returned the wrong count/width |

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

**Providers.** Every call out to a vendor or to Qdrant is recorded, and — because the logging
lives in the interfaces rather than in each provider — all of them report the same fields:

```
INFO  | factories.llmembedding.OpenAIEmbeddingProvider | Embedded 42 texts into 1536-dim vectors in 310 ms (provider=OpenAIEmbeddingProvider, model=text-embedding-3-small)
INFO  | factories.llmchatting.AnthropicChatProvider    | Generated 812 chars in 2140 ms (provider=AnthropicChatProvider, model=claude-sonnet-4-5)
DEBUG | factories.llmchatting.AnthropicChatProvider    | Token usage (provider=AnthropicChatProvider, model=claude-sonnet-4-5, input=1204, output=193)
DEBUG | factories.vectordb.QdrantProvider              | Searched 'chunks': 5/5 hits in 3 ms (top_score=0.8817)
```

Duration, counts and sizes go on every line, plus token usage at DEBUG where the vendor
reports it (each SDK names it differently; the providers normalise it). Each also passes the
same values through `extra={...}`, so `LOG_FORMAT = "json"` yields queryable
`duration_ms` / `input_tokens` / `hit_count` fields rather than prose.

**No document content is ever logged** — not prompts, not answers, not chunk text, not
payloads. Only counts, lengths and timings. Logs are shipped and retained; user documents
are exactly what should not be sitting in them.

Provider failures are **not** logged where they happen. They raise (`LLMProviderError`,
`EmbeddingError`, `VectorDBError`) and stay quiet, so `main.py`'s handler remains the single
record of each one — the same rule the models and controllers follow.

At INFO a request logs only what its outcome was. `LOG_LEVEL = "DEBUG"` adds the inbound
`-> METHOD /path` line (with client and query string) plus each layer's parameters — useful
when a request hangs, since a hung request has no completion line at INFO.

## Known limitations

- The whole upload is buffered in memory and stored inside a single MongoDB document, which
  caps a source at well under the 16 MB BSON limit. GridFS or object storage is the way out.
- The providers are built at startup but nothing calls them: `/process` still ends at
  chunking. Until the pipeline is wired up, a valid API key buys you a client that is only
  ever opened and closed.
- Embedded Qdrant holds an exclusive lock on `VECTOR_DB_PATH`, so two processes against the
  same directory — `--reload` double-starts included — will collide. Point `VECTOR_DB_URL` at
  a server if you need more than one.
- Provider calls have no timeout, retry or backoff beyond each SDK's own defaults, and no
  rate limiting. A slow vendor holds the request open for as long as it likes.
- Chunks are persisted to MongoDB, but `/process` still returns every chunk's full text in
  the response as well. For a real document that payload is large and redundant.
- The skip check matches on `asset_id`, so chunks written **before that field existed** are
  invisible to it — a project ingested by an older build will be chunked a second time.
  One `reset: true` pass over such a project clears the legacy rows and rebuilds them.
- `reset` deletes then re-inserts without a transaction (standalone MongoDB has none), so a
  failure mid-insert leaves that asset with fewer chunks than it started with.
- Nothing dedupes uploads: the same file uploaded twice becomes two assets with different
  `asset_id`s, and both get chunked. Skipping is per-asset, not per-document-content.
- `ChunkModel.get_project_chunks()` still sorts by `chunk_order` alone, so a project holding
  two documents comes back interleaved. Now that chunks carry `asset_id`, that read wants to
  sort on `(asset_id, chunk_order)` — or filter by asset — before retrieval is built on it.
- Both routes overwrite the project's `name` and `description` with placeholders derived
  from the current file on every upload and process call. In whole-project mode the name
  comes from whichever asset happens to be first.
- Answering is not yet implemented — which is, for now, the "minus". Ingestion, embedding,
  and retrieval all work; nothing yet turns retrieved passages into a written answer.
- Indexing is synchronous: `POST /nlp/index/push` embeds every chunk before responding, so a
  large project holds the request open for as long as that takes. A background job with a
  status endpoint is the way out.
- The index does not track the chunks it was built from. Re-running `/process` leaves stale
  vectors behind until the next push, and deleting an asset never removes its vectors.
  `GET /nlp/index/info` exposes the gap (`chunks_in_db` vs `points_count`) but nothing
  reconciles it automatically.
- Embedded Qdrant holds an exclusive lock on `VECTOR_DB_PATH`, so two processes against the
  same directory collide — `uvicorn --reload` included. Use `VECTOR_DB_URL` for a server.
