# Running NotebookLLM-minus in Docker

The application, its datastores, and the observability stack around them. Everything here is
driven from `docker-compose.yml`; run every command from **this directory**.

- [Quick start](#quick-start)
- [What is in here](#what-is-in-here)
- [The image](#the-image)
- [Services and ports](#services-and-ports)
- [Configuration](#configuration)
- [Choosing a database backend](#choosing-a-database-backend)
- [Observability](#observability)
- [Things that will catch you out](#things-that-will-catch-you-out)
- [Troubleshooting](#troubleshooting)

## Quick start

```bash
cd Docker

cp .env.example .env           # the five values compose substitutes
cp -r env.example env          # then fill in env/.env.app — see Configuration

docker compose config          # parses and resolves; fastest way to catch a typo
docker compose up -d --build
```

Then:

| | |
| --- | --- |
| the app | http://localhost — through nginx |
| the app, direct | http://localhost:8000 — bypasses nginx |
| Grafana | http://localhost:3000 — `admin` / `admin` on first boot |
| Prometheus | http://localhost:9090 — check `/targets` first |

`docker compose down` stops it; `docker compose down -v` also destroys the databases.

## What is in here

```
Docker/
├── docker-compose.yml            the whole stack
├── docker-composeV1.yml          the earlier mongo-only version, kept for reference
├── .env                          values compose substitutes into docker-compose.yml
├── .env.example                  template for the above
├── env/                          per-service environment, mounted into containers
│   ├── .env.app                  the application's own configuration
│   ├── .env.grafana
│   └── .env.postgres-exporter
├── env.example/                  templates for the above; env/ is gitignored
├── nginx/nginx.conf              reverse proxy config
├── prometheus/prometheus.yml     scrape targets
└── notebookllm-minus/
    ├── Dockerfile
    └── Dockerfile.dockerignore
```

## The image

Multi-stage, `python:3.12-slim` with the `uv` binary copied in from its own published image at
a pinned tag. Debian rather than Alpine deliberately: Alpine is musl, so PyPI's manylinux
wheels do not apply and `asyncpg`, `pydantic-core` and the tokenizers under langchain would all
compile from source.

**The build context is the repository root, not this directory** — the application lives in
`src/` and the lockfile with it. Compose handles this (`context: ..`), but a manual build must
too:

```bash
docker build -f notebookllm-minus/Dockerfile -t notebookllm-minus ..
```

### The ignore file is named `Dockerfile.dockerignore`, not `.dockerignore`

Because the context is the repository root, a `.dockerignore` would have to live *there* to be
found. BuildKit checks for `<dockerfile-name>.dockerignore` beside the Dockerfile first, which
is why it sits here under that name.

**This only works under BuildKit.** BuildKit is the default in modern Docker, but a build
forced through the classic builder (`DOCKER_BUILDKIT=0`) will not find it and will ship the
local `.venv/` — several hundred megabytes — into the build context. If the first line of build
output reports a context in the hundreds of MB rather than single digits, this is why.

## Services and ports

| service | image | host port | notes |
| --- | --- | --- | --- |
| `nginx` | `nginx:1.27-alpine` | 80 | reverse proxy; denies `/metrics` |
| `fastapi` | built here | 8000 | the application |
| `pgvector` | `pgvector/pgvector:0.8.6-pg18-trixie` | 5400 | documents *and* vectors |
| `mongo` | `mongo:8.2` | 27017 | profile `mongo` only |
| `qdrant` | `qdrant/qdrant:v1.12.4` | 6333 | profile `mongo` only |
| `prometheus` | `prom/prometheus:v3.1.0` | 9090 | |
| `grafana` | `grafana/grafana:11.4.0` | 3000 | |
| `postgres-exporter` | `prometheuscommunity/postgres-exporter:v0.16.0` | 9187 | |
| `node-exporter` | `prom/node-exporter:v1.8.2` | 9100 | host CPU, memory, disk |
| `cadvisor` | `gcr.io/cadvisor/cadvisor:v0.49.1` | 8081 | per-container usage |
| `nvidia-exporter` | `utkuozdemir/nvidia_gpu_exporter:1.2.1` | 9835 | GPU utilisation and VRAM |

Every image is pinned. `latest` on a database is a way to discover a breaking change during an
outage.

## Configuration

Two layers, and they are not interchangeable.

**`./.env`** — read by compose itself, for `${...}` substitution inside `docker-compose.yml`.
Only these five:

```
MONGO_INITDB_ROOT_USERNAME    MONGO_INITDB_ROOT_PASSWORD
POSTGRES_USERNAME             POSTGRES_PASS               POSTGRES_MAIN_DB
```

**`./env/*`** — mounted into containers as their environment. `env/.env.app` is the
application's configuration and the one you will actually edit.

The image ships **no** `src/.env`; configuration comes entirely from `env/.env.app`. Several
settings have no defaults — `APPLICATION_NAME`, `APP_VERSION`, `ALLOWED_TYPES`,
`MAX_FILE_CHUNK_SIZE`, `GENERATION_MODEL_ID`, `EMBEDDING_MODEL_ID`, `EMBEDDING_MODEL_SIZE` —
so the app fails startup validation if they are missing, naming what it wanted.

Values inside `env/.env.app` are **container-shaped, not host-shaped**:

| host | container |
| --- | --- |
| `POSTGRES_HOST=localhost`, port `5400` | `POSTGRES_HOST=pgvector`, port `5432` |
| `OLLAMA_HOST=localhost`, port `11434` | `OLLAMA_HOST=host.docker.internal`, port `11434` |

Ollama runs on the host, not in this stack. `host.docker.internal` is not automatic on Linux,
so the `fastapi` service maps it to the docker bridge gateway via `extra_hosts`.

`env/` is gitignored; `env.example/` is the committed template.

## Choosing a database backend

The app supports two, picked by `DOCUMENT_DB_BACKEND` in `env/.env.app`:

- **`postgres`** (default) — documents and vectors both in pgvector. One service. Nothing extra
  to start.
- **`mongo`** — documents in MongoDB, vectors in Qdrant. Two services, behind a compose profile
  so they do not start when unused:

  ```bash
  docker compose --profile mongo up -d
  ```

They keep separate data. Switching backends does not migrate anything.

The Postgres schema is owned by Alembic and applied at startup under an advisory lock, so
nothing needs running by hand.

## Observability

Prometheus scrapes; Grafana draws. Targets are in `prometheus/prometheus.yml`:

`prometheus` · `notebookllm` (the app's `/metrics`) · `postgres` · `node` · `qdrant`

The app's own metrics are defined in one file, `src/utils/metrics.py`. Beyond the usual HTTP
rate/errors/duration, they cover the parts that actually cost time here: per-stage ingest
duration, embedding latency and batch size, generation time-to-first-token and token counts,
retrieval latency and hit counts, and whether an answer was grounded.

Labels are deliberately bounded — `provider`, `model`, `stage` — and never carry a `chat_id`
or `asset_id`. Each distinct label value is a separate time series that is never reclaimed;
labelling by notebook id is the standard way to kill a Prometheus instance.

**Two dashboards are provisioned automatically**, along with the Prometheus data source — no
manual import, and nothing to add by hand:

| dashboard | what it is for |
| --- | --- |
| **NotebookLLM-minus — FastAPI Observability** | the app. Request rate, error share, p99 latency, requests in flight, plus ingest-stage duration and embedding/generation latency. A **PostgreSQL** row at the bottom carries database size, connections against the pool ceiling, insert/fetch rates and cache hit ratio — enough to tell whether a slow upload was the database or the model. |
| **PostgreSQL — notebookllm-minus** | the database in depth: 35 panels of connection, transaction, lock and I/O detail. Where you go when the row above says something is wrong. |
| **Host (node exporter) — notebookllm-minus** | the machine: CPU, memory, disk, network. 140 panels (Node Exporter Full, 1860). |

Both are adapted from community dashboards (16110 and 9628) with their queries rewritten onto
this project's metric names — `http_requests_total` rather than `fastapi_requests_total`,
`handler` rather than `path`, and a `$job` variable in place of `$app_name`.

`Docker/grafana/dashboards/` is watched on a 30-second interval, so editing a JSON there
updates Grafana without a restart.

**Four panels on the PostgreSQL dashboard are permanently empty**, and it is not a
misconfiguration: *Buffers (bgwriter)* and *Checkpoint Stats* read `pg_stat_bgwriter`
columns that PostgreSQL 17 moved to `pg_stat_checkpointer`. This runs PostgreSQL 18, so the
exporter's `stat_bgwriter` collector finds nothing — enabling it changes nothing, which is why
it is not enabled. *Lock tables* is empty for a related reason. `--collector.postmaster` **is**
enabled, because the *Start Time* panel needs it and it does work.

**`node_exporter` runs on the host, not as a container.** Docker Desktop's VM refuses the
`/` slave-propagation mount the container needs, and would report the VM rather than the
machine even if it did not. It is a `systemd --user` unit:

```bash
systemctl --user status node_exporter        # ~/.config/systemd/user/node_exporter.service
curl -s localhost:9100/metrics | head        # the binary is ~/.local/bin/node_exporter
loginctl enable-linger $USER                 # keep it running when logged out
```

Prometheus reaches it at `host.docker.internal:9100`, which resolves from Desktop's containers.
If the `node` target goes down, check the user service first — nothing in compose can restart it.

**`/nlp/health` is deliberately not scraped.** It performs a real embedding inference on every
call, so a 15-second scrape would fire a GPU inference four times a minute forever, pinning the
embedding model in VRAM and evicting the chat model. It stays an on-demand tool.

## Things that will catch you out

**`/metrics` is reachable directly on port 8000.** nginx denies it on port 80, but `fastapi`
also publishes 8000 to the host, which bypasses that. It is unauthenticated and describes your
route inventory, model ids and traffic shape. To close it, delete the `ports:` block from the
`fastapi` service — Prometheus still reaches it internally at `fastapi:8000`, and the app is
served through nginx on `:80`.

**One uvicorn worker, on purpose.** The lifespan builds a single provider cache and runs
Alembic under an advisory lock, and the Prometheus counters are per-process. With `--workers N`
a scrape hits one worker at random, counters appear to jump backwards, and `rate()` produces
nonsense. Scale with more containers; Prometheus will `sum()` them.

**GPU memory is the real constraint on a small card.** On an 8 GB GPU, `qwen3-embedding:8b`
(6.2 GB) and `gemma4:e4b` (9.6 GB) cannot both be resident, so Ollama evicts one to load the
other. A slow upload usually shows up as `nvidia_smi_memory_used_bytes` churning, not as
anything in the application metrics. `nvidia-exporter` needs the NVIDIA container toolkit
(`nvidia-ctk runtime configure --runtime=docker`); without it that one service fails to start
and the `gpu` scrape target sits DOWN.

**cAdvisor is the heaviest thing in the stack.** It runs privileged with `--housekeeping_interval=30s`
and `--docker_only=true` to keep it from using more CPU than the app it is watching. Drop it if
you do not need per-container graphs.

**Uploads are capped twice.** `MAX_FILE_SIZE` in `env/.env.app` (50 MB) and
`client_max_body_size` in `nginx/nginx.conf` (64 MB). Raise the app's and nginx will reject the
request with a 413 before the app ever sees it.

**A citation's highlight needs `PDF_LOADER=pymupdf` set in `env/.env.app` — it is not
inherited from anywhere.** This is the same class of failure as the `OLLAMA_HOST` one above:
`PDF_LOADER` defaults to `pymupdf` in `Settings` itself, but if `env/.env.app` was copied
from an older `env.example/` (or hand-edited before this default existed) and never got the
line added, nothing errors — uploads still work, citations still open at the right page, the
highlight is just silently never computed. Check it is actually present in the file the
container reads, not only in `.env.example`.

Real cost once it is set: pymupdf's word-by-word parsing is ~19x slower than `pypdf`, though
it runs in parallel across a process pool sized to how many CPUs the container can actually
use — checking both a `--cpus` quota and a `--cpuset-cpus` pin and taking the smaller, since
a quota alone is invisible to the usual affinity check. A container with no CPU limit set (the
default in this compose file) sees the host's full core count; one capped with `--cpus` or
`deploy.resources.limits.cpus` sees close to the serial cost once that quota is small. See
`PDF_LOADER` in `src/utils/config.py` for the measurements.

## Troubleshooting

**`docker compose config` fails** — a YAML problem, and it names the line. Fix this before
anything else; nothing downstream will work.

**The app exits at startup** with a pydantic validation error — a required key is missing from
`env/.env.app`. The error names it.

**The app cannot reach Ollama** — from inside the container `localhost` is the container.
`OLLAMA_HOST` must be `host.docker.internal`, not `localhost`, and `ollama serve` must be
running on the host. (A stray `OLLAMA_BASE_URL` line does nothing — it is not a real setting
any more and is silently ignored, which is exactly the failure mode this fixes: `OLLAMA_HOST`
then keeps its `localhost` default and the app dials itself instead of the host.)

**A Prometheus target is DOWN** — check `http://localhost:9090/targets`. Targets are addressed
by compose service name on the `backend` network, so a DOWN target usually means that service
failed to start rather than a config error. `docker compose logs <service>`.

**Grafana shows "no data"** — almost always the data source URL. It must be
`http://prometheus:9090`, not `localhost:9090`.

**The build ships hundreds of megabytes of context** — BuildKit is disabled, so
`Dockerfile.dockerignore` is being ignored. See [The image](#the-image).
