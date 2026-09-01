# NotebookLLM⁻

> **NotebookLLM-minus** — NotebookLM, minus the parts that aren't built yet.

Upload documents, ask questions, get answers grounded in *those* documents, with citations
back to the page they came from. A small, deliberately readable RAG backend on **FastAPI**,
with a web UI in English and Arabic.

Every backend is swappable from `.env` — chat model, embedding model, document store, vector
store — and the whole stack runs **offline against a local Ollama** if you want it to. Models
are discovered from what is actually installed and reachable, never pinned in code.

- [Quickstart](#quickstart) · [Configuration](#configuration) · [Features](#features)
- [Architecture](#architecture) · [Providers](#providers) · [Choosing models](#choosing-models)
- [Database backends](#database-backends) · [Data model](#data-model) · [API](#api)
- [Deployment](#deployment) · [Observability](#observability) · [Project structure](#project-structure)

## Quickstart

```bash
cd Docker
cp .env.example .env            # database credentials
cp -r env.example env           # per-service env files
docker compose up -d --build    # postgres profile: app, pgvector, nginx, prometheus, grafana
```

Then configure the app itself and run it:

```bash
cd src
cp .env.example .env            # every knob is documented in there
uv sync
uv run uvicorn main:app --reload
```

- UI and API — <http://localhost:8000> (`:8080` through nginx)
- Interactive API docs — <http://localhost:8000/docs>
- Grafana — <http://localhost:3000>, Prometheus — <http://localhost:9090>

`DOCUMENT_DB_BACKEND` decides which database is used, not which containers are running. The
compose file keeps Mongo and Qdrant behind a profile (`--profile mongo`), so the default
`postgres` setup starts one database instead of two.

## Configuration

`src/.env.example` is the authoritative copy: every setting is documented there, next to the
values it accepts, and every "one of …" list is backed by an enum so a wrong value fails at
startup with the full list rather than on first use. The ones worth knowing about:

| Setting | Does what |
| --- | --- |
| `DOCUMENT_DB_BACKEND` | `mongo` (+ Qdrant) or `postgres` (+ pgvector, one service) |
| `GENERATION_BACKEND` | `anthropic` · `openai` · `google` · `cohere` · `nvidia` · `ollama` |
| `EMBEDDING_BACKEND` | the same minus `anthropic`, which ships no embeddings API |
| `GENERATION_MODEL_ID` / `EMBEDDING_MODEL_ID` | named the way the vendor names them (`gemma4:e4b`, `nvidia/nemotron-3-embed-1b`) |
| `EMBEDDING_MODEL_SIZE` | **must match the model** — it is baked into the collection at creation |
| `PDF_LOADER` | `pymupdf` (default) · `pypdf` · `pdfplumber` |
| `OLLAMA_HOST` / `OLLAMA_PORT` | where `ollama serve` is listening |
| `OLLAMA_CLOUD_BASE_URL` | a second Ollama over the network; its models appear alongside the local ones |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MIN_SCORE` | how many passages reach the prompt, and the floor they must clear |

Only the API key for a backend you actually selected has to be filled in, and it is checked
during startup — a blank key means the app refuses to boot rather than failing on the first
question someone asks. Ollama is exempt: it authenticates by host.

**On `PDF_LOADER`.** Measured on a 274-page Arabic document: `pypdf` 29s and ~90k lost
glyphs, `pdfplumber` 52s and right-to-left text in *visual* order (reversed — not one real
Arabic word survives), `pymupdf` no lost glyphs at all. pymupdf is the default and the only
one that captures the word coordinates a citation highlight is drawn from; it is also much
slower, which `PdfLayoutController` offsets by extracting pages across a process pool.

## Features

**Documents.** Upload PDF, txt or markdown per notebook, validated on type and size and
stored as bytes in the database — nothing touches disk. A document is identified by the
sha256 of its contents scoped to one notebook, so re-uploading the same file is refused with
a 409 before anything is chunked, and renaming it does not sneak it past. Deleting a source
removes it, its chunks and its vectors, derived-first. Ingestion is idempotent and runs off
the event loop, so a 200-page PDF no longer freezes every other request.

**Chunking with a size guard.** `NLTKTextSplitter` cuts on sentence boundaries and a
`RecursiveCharacterTextSplitter` re-splits anything still over the limit — not decorative,
since PDF text often carries no sentence punctuation at all, which otherwise turns a whole
document into one chunk. Text is sanitised at extraction: damaged font encodings decode to
NUL bytes, which PostgreSQL refuses in both `text` and `jsonb`.

**Grounded chat.** Users → sessions → notebooks → messages, answers streamed token by token
over SSE. A notebook holding documents answers from them with citations; one without them is
an ordinary assistant — same endpoint, same code path, decided by whether the notebook has
vectors rather than by a flag. A citation names the source's real **page** and opens the
document there, highlighting the exact passage in a built-in PDF.js viewer, in a colour set
per notebook. Reasoning models stream their scratchpad into a panel that collapses when the
answer starts; it is shown, never stored. Generation can be stopped mid-answer and the
partial reply is kept.

**Interface.** Jinja templates plus ES modules, no build step. Markdown renders as it
streams. English and Arabic, including right-to-left layout. Sources / Chat / Studio panels
resize by dragging and fold to a rail, remembered across reloads. Per-notebook tuning of
temperature, output length, chunk size and overlap. Answers can be copied, downloaded, or
saved back into Sources as a new document.

**Operations.** Prometheus metrics at `/metrics` covering ingest duration per stage,
embedding latency and batch size, time-to-first-token, retrieval latency and whether an
answer was grounded — labels deliberately bounded to `provider`, `model` and `stage`, never a
`chat_id`. Provisioned Grafana dashboards, structured logs with per-request correlation ids,
and a live health probe that actually calls the database, the embedding model and the vector
store rather than reporting what is configured.

**Not yet.** Web-search grounding is stored per notebook and shown marked *soon*, with no
backend behind it. Formats past pdf/txt/md are enumerated in `AssetType` and not implemented.

## Architecture

Four layers, each talking only to the one below it. Nothing above `factories/` names a
vendor, which is what makes the backends swappable from config.

```mermaid
flowchart TB
    subgraph browser["Browser"]
        UI["Jinja page + ES modules"]
    end

    subgraph routes["Routes — HTTP only, no logic"]
        R1["/chat<br/>users · sessions · notebooks · SSE"]
        R2["/data · /process<br/>upload · chunk"]
        R3["/nlp<br/>index · search · health"]
    end

    subgraph controllers["Controllers — the actual work"]
        C1["ChatController<br/>retrieve → prompt → stream"]
        C2["ProcessController<br/>load documents"]
        C5["TextProcessingController<br/>sanitise → split → size guard"]
        C3["NLPController<br/>embed → upsert → search"]
        C4["ModelController<br/>discover and probe models"]
    end

    subgraph providers["factories/ — swappable backends"]
        P1["LLMChattingInterface<br/>anthropic · openai · google<br/>cohere · nvidia · ollama"]
        P2["LLMEmbeddingInterface<br/>openai · google · nvidia<br/>cohere · ollama"]
        P3["DbProvider<br/>mongo+qdrant · postgres+pgvector"]
    end

    UI -->|"fetch + SSE"| R1
    UI --> R2
    UI --> R3
    R1 --> C1
    R1 --> C4
    R2 --> C2
    R3 --> C3
    C1 --> C3
    C1 --> P1
    C3 --> P2
    C1 --> P3
    C3 --> P3
    P1 -.->|"local"| O(["Ollama"])
    P2 -.->|"local"| O
```

### Asking a question

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant R as POST /chat/chats/{id}/message
    participant CC as ChatController
    participant V as Vector store
    participant L as Chat model

    U->>R: { text }
    R->>R: read history (before storing the question,<br/>or it arrives in context twice)
    R->>R: store user turn
    R->>CC: answer_stream(question, history, lang)
    CC->>V: collection has points?
    alt has documents
        V-->>CC: passages + metadata
        CC->>CC: rag prompt + numbered documents
    else no documents
        CC->>CC: plain chat prompt
    end
    CC-->>U: meta { grounded, citations }
    CC->>L: stream
    loop while generating
        L-->>CC: thinking / content pieces
        CC-->>U: thinking · delta
    end
    CC-->>U: done
    R->>R: store assistant turn (answer only)
```

`chat_id` **is** the `project_id`, so a notebook is its own document space and reuses the
ingestion path unchanged. Vector point ids are `uuid5(asset_id + chunk_order)` — stable
across re-processing, so re-indexing overwrites in place instead of orphaning vectors.

## Providers

Each subsystem under `src/factories/` is one abstract interface, one implementation per
vendor, and a factory turning a name from `.env` into a configured instance.

| Subsystem | Backends | Interface |
| --- | --- | --- |
| `llmchatting` | `anthropic`, `openai`, `google`, `cohere`, `nvidia`, `ollama` | `generate_text(prompt, chat_history, max_tokens, temperature)` |
| `llmembedding` | `openai`, `google`, `cohere`, `nvidia`, `ollama` | `embed(texts, input_type)` |
| `db` | `mongo` (+ Qdrant), `postgres` (+ pgvector) | eight repository ABCs |

**One neutral message format.** Callers build `{"role", "content"}` dicts using `ChatRole`
and each provider translates on the way out — Anthropic and Google lift the system turn into
a separate argument, Google renames `assistant` to `model`, the rest take it inline.
`generate_text()` is concrete on the interface: it normalises, logs, times and delegates to
`_generate_text()`, so no provider can forget to log and all of them log the same fields.

Normalising includes **forcing strict alternation** — user/assistant/user/… after the system
turn. Ollama accepts any shape; NVIDIA and Anthropic answer `400 Conversation roles must
alternate`. Two ordinary things here produce a shape they reject: a stream that failed or was
abandoned stored a question and never an answer, and history is a window of the last
`CHAT_HISTORY_LIMIT` messages that can open mid-exchange. Consecutive same-role turns are
joined rather than dropped, because an unanswered question is context for the next one.

**Embeddings are batch-first**, and two failures are caught at the boundary: a count mismatch
(vectors are zipped against chunk ids by position, so one dropped vector silently misaligns
everything after it) and a width mismatch against `EMBEDDING_MODEL_SIZE`, which would
otherwise surface as an error naming the collection rather than the misconfigured setting.

**`nvidia` is NVIDIA NIM, not a sixth SDK.** Its API *is* OpenAI's, so `NvidiaChatProvider`
is `OpenAIChatProvider` under another name — one line, plus a vendor label so its errors do
not say "OpenAI". It is a separate backend rather than `openai` with a different base URL
because the two are separate accounts with separate catalogues, and a NIM model id carries
its publisher (`meta/llama-3.2-11b-vision-instruct`). `NvidiaEmbeddingProvider` overrides
`_embed` for three real differences: its models are asymmetric (`input_type` is
`passage`/`query`), a request is capped at 256 inputs — under `CHUNKING_BATCH_SIZE`, so the
provider splits the batch itself — and the width is fixed, so `dimensions` is never sent.

No provider class holds a URL, a limit or a wire string. Endpoints and limits are `Settings`
fields; vendor spellings (`passage`, `END`) are tables in `enums/ProviderMappings.py`; and
which settings reach which constructor is one more table walked by `setting_kwargs()`.
Giving a provider a new knob is a line in that table plus a field on `Settings` — the
factories never branch per vendor.

## Choosing models

A notebook can name its own chat and embedding model, and the picker is built from what is
genuinely available rather than a hardcoded list. `ModelController` merges every configured
source and groups the result twice.

**By source** — `local` and `cloud` are two Ollama hosts, `nvidia` is a hosted vendor. A
model id carries its source (`local/gemma4:e4b`, `nvidia/meta/llama-3.2-11b-vision-instruct`)
and that prefix selects the *provider*, so one notebook can run on a local model while the
next runs on NVIDIA — something a single global `GENERATION_BACKEND` cannot express.

**By parameter size** — under 8B, 8B–30B, over 30B, sorted smallest first. Ollama reports the
count; NVIDIA does not, so it is read out of the tag, where nearly every model carries one. A
mixture-of-experts tag names its total before its active count (`120b-a12b` is a 120B model),
a version is not mistaken for a size, and a tag advertising nothing is filed under "size
unknown" rather than guessed at.

**Chat and embedding lists are disjoint by capability.** Ollama embeds with *any* model —
`llama3.1:8b` answers an embed probe with 4096 dimensions — so answering was never evidence
of belonging in the embedding list. `/api/show` reports real capabilities where `/api/tags`
does not, and `completion` / `embedding` decide. A model reporting neither is offered for
chat, the one thing every model can do.

**NVIDIA models are filtered to what the key can call.** `/v1/models` lists NVIDIA's whole
catalogue, but an account may call a fraction of it and the rest answer `404 Function …: Not
found for account` only once chosen. Entitlement is checked before body validation, so
posting an empty `messages` list separates them for free — 400 means callable, 404 means not
yours, neither runs inference. Embedding NIMs refuse `/chat/completions`, so they are probed
on `/embeddings` instead, which also yields the vector width. Verdicts are cached on the
class, so the first catalogue call after a restart pays for it and later ones are a lookup.

## Database backends

`DOCUMENT_DB_BACKEND` picks the store behind users, sessions, notebooks, messages, projects,
assets and chunks. Both implement the same repository interfaces, so nothing above `app.db`
knows which is running.

| | `mongo` | `postgres` |
| --- | --- | --- |
| Documents | MongoDB via `motor` | PostgreSQL via SQLAlchemy 2.0 + `asyncpg` |
| Vectors | Qdrant, a second service | pgvector, in the same database |
| Schema | implicit; indexes ensured at startup | explicit; owned by Alembic |
| Services | two | one |

They are not interchangeable at runtime — each keeps its own data, and switching the setting
points the app at a different store rather than migrating anything. The Postgres DSN is
assembled from `POSTGRES_HOST/PORT/USER/PASSWORD/DB`, credentials percent-encoded, so there
is no `DATABASE_URL` to keep in sync and no password in a committed file.

### Migrations

Tables are declared once as SQLAlchemy models in `factories/db/postgres/base_repository.py`.
Alembic reads that metadata and every repository queries the same classes, so a column that
does not exist is an `AttributeError` at import rather than a 503 on first request.

Nothing needs running by hand: `alembic upgrade head` runs during the app's lifespan under a
Postgres advisory lock, so several workers booting at once cannot race. To drive it yourself,
from `src/` (the config lives beside the backend it migrates, hence `-c`):

```bash
uv run alembic -c factories/db/postgres/alembic.ini upgrade head
uv run alembic -c factories/db/postgres/alembic.ini revision --autogenerate --rev-id 0006 -m "…"
```

Revision ids are numbered by hand so `ls versions/` reads in order. **An autogenerate run
against a database already at head must produce an empty migration** — that is the check this
arrangement exists to make possible; if it is not empty, the models and the database have
drifted.

`0001` creates everything with `IF NOT EXISTS` so a database predating Alembic is adopted in
place. What that cannot do is reconcile a table that exists with the *wrong shape* — it skips
the whole table, columns and all — which is what `0002` exists for, after every read of a
user's sessions failed with `UndefinedColumnError`. Anything column-level needs its own
migration. Five so far: initial schema, sessions reconcile, asset content hash, chunk lookup
index, notebook highlight colour.

**pgvector collections are not Alembic's.** One table per notebook, `vec_<collection>`,
created at runtime — the vector width is not known until an embedding model is chosen, and a
shared table would mean one fixed width for everyone. Alembic creates the extension; the
repository creates the tables.

## Data model

| Collection | Key fields |
| --- | --- |
| `projects` | `project_id` (string, from the URL), `assets_ids`, `chunks_ids` |
| `assets` | `asset_id` (uuid4), `asset_type`, `project_id`, `file_bytes`, `content_hash` |
| `data_chunks` | `project_id` (**ObjectId**), `asset_id`, `chunk_order`, `chunk_content` |

The same pydantic documents back both stores; on Postgres they are tables of the same names
(`data_chunks` becomes `chunks`). The identifier distinction is worth internalising early:
`Project.project_id` is the string from the URL, while `DataChunk.project_id` is the
project's `_id`. Primary keys stay 24-hex `ObjectId` strings on Postgres too, because the
models are shared with the Mongo backend.

Users → sessions → notebooks → messages, with assets and chunks hanging off a notebook.
Nothing cascades in either store, so deletion routes walk the tree themselves, derived-first
at every level — a partial failure leaves a source visibly listed rather than leaving vectors
that outlived it.

## API

Full interactive reference at `/docs`. The shape of it:

| Method | Path | |
| --- | --- | --- |
| `GET` | `/base/health` | app name and version |
| `GET` | `/nlp/health` | live probe — database, embedding model, vector store |
| `POST` | `/data/upload/{project_id}` | store a file as an asset |
| `POST` | `/process/{project_id}` | chunk one asset, or every asset in the project |
| `POST` | `/nlp/index/push/{project_id}` | embed a project's chunks and upsert them |
| `POST` | `/nlp/index/search/{project_id}` | ranked passages with citation metadata |
| `GET` | `/nlp/index/info/{project_id}` | collection name, size, width |
| `GET/POST/PATCH/DELETE` | `/chat/users…`, `/chat/users/{id}/sessions`, `/chat/sessions/{id}/chats` | profiles, sessions, notebooks |
| `POST` | `/chat/chats/{chat_id}/message` | ask a question — streams SSE |
| `GET` | `/chat/chats/{chat_id}/messages` | transcript |
| `POST/GET/PATCH/DELETE` | `/chat/chats/{chat_id}/assets…` | a notebook's documents, content, rename, delete |
| `GET` | `/chat/chats/{chat_id}/assets/{asset_id}/chunks/{n}/locate` | where a citation sits on the page |
| `GET` | `/chat/chats/{chat_id}/indexing` | ingest progress, mid-upload |
| `GET` | `/chat/models` | the catalogue the picker is built from |
| `PATCH` | `/chat/chats/{chat_id}/models` | point a notebook at different models |
| `PATCH` | `/chat/chats/{chat_id}/settings` | temperature, output length, chunking, colour |

Asking a question streams newline-delimited JSON events — `meta` first (grounded flag and
citations, so sources paint before the first token), then `thinking` and `delta` pieces, then
`done`:

```bash
curl -N -X POST localhost:8000/chat/chats/$CHAT/message \
     -H 'content-type: application/json' -d '{"text": "what does it say about X?"}'
```

Switching a notebook's embedding model **rebuilds its index** — vector width is fixed when a
collection is created, so old vectors are unusable. The chunks are already stored, so the
rebuild re-embeds them rather than asking for the documents again.

## Deployment

`.github/workflows/deploy-main.yml` runs the suite on every push to `main` and, only if it is
green, ssh's to the host, fast-forwards the checkout and restarts the systemd unit. Secrets:
`SSH_MAIN_HOST_IP`, `SSH_MAIN_PRIVATE_KEY`. The remote user needs passwordless sudo for
exactly one command:

```
omar ALL=(root) NOPASSWD: /usr/bin/systemctl restart notebookllm-minus
```

sudoers matches a command as written, so the path and arguments must match the invocation
character for character — `sudo -n -l` over ssh lists what is actually permitted.

The unit itself is `Type=oneshot` with `RemainAfterExit=yes`, running `docker compose up -d
--build` in the checkout. The restart *is* the deployment only because the checkout moved
first; that is why the workflow pulls before restarting.

## Observability

Prometheus scrapes four targets, all addressed by compose service name so nothing depends on
a published port:

| Job | Source |
| --- | --- |
| `notebookllm` | the app's own `/metrics` |
| `postgres` | `postgres-exporter` |
| `prometheus` | itself |
| `node` | `node_exporter` **on the host**, not in the stack |

`node_exporter` is the one piece to install separately (`apt install
prometheus-node-exporter`). Both the app and Prometheus map `host.docker.internal` to
`host-gateway`, because that name is a Docker Desktop convenience that does not exist on
Linux — without it the node job sits down with a DNS error while every other target is green.

`/nlp/health` is deliberately **not** scraped: it performs a real embedding inference, so a
15s scrape would fire one four times a minute forever. Liveness comes from the app's job
being up at all.

## Error handling

Domain errors live in `exceptions.py`, each carrying the status it maps to —
`NotFoundError` 404, `InvalidInputError` 400, `DbError` 503, `LLMProviderError` 502. Routes
raise them and one handler in `main.py` turns them into responses, so no route builds an
error body by hand and no failure is logged twice. Anything unrecognised becomes a 500 with
the traceback in the log and nothing revealing in the response.

## Logging

stdlib `logging`, configured once in `utils/logging_config.py`: rotating file output under
`LOG_DIR`, optional console, and `text` or `json` format. Every request gets a correlation id
attached by `middleware/request_logging.py` and carried on every line it produces. Log lines
carry counts and sizes — never prompt text, answer text or document content, which is exactly
what should not be sitting in a log file.

## Project structure

```
src/
├── main.py                 # app, lifespan, providers, error handler
├── routes/                 # HTTP only — base, data, process, nlp, ui
│   ├── chat/               # users · sessions · chats · messages · assets · models
│   └── schemas/            # request models
├── controllers/            # the work: chat, process, text processing, nlp, models, pdf layout
├── factories/              # swappable backends
│   ├── llmchatting/        # interface + 6 providers + factory
│   ├── llmembedding/       # interface + 5 providers + factory
│   ├── db/                 # mongo/ and postgres/ behind 8 repository ABCs, + alembic/
│   └── setting_kwargs.py   # {kwarg: settings field} tables -> constructor kwargs
├── models/                 # pydantic documents + the model classes that read them
├── templates/locales/      # prompts sent TO the model, per language
├── web/                    # templates/ and static/ sent TO the browser
├── enums/                  # every .env choice, plus ProviderMappings' lookup tables
└── utils/                  # Settings, logging, metrics, model id vocabulary
Docker/                     # compose, nginx, prometheus, grafana dashboards
test/                       # unit + route tests, fakes for every backend
```

**Two directories are called templates on purpose.** `templates/` holds prompts sent to the
model; `web/templates/` holds Jinja HTML sent to the browser. Different audiences, so they
never share a file.
