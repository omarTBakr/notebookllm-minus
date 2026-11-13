# Observability, errors and logging

## What it does

Shows what the system is doing without reading logs: one Langfuse trace per answer, Prometheus
metrics from the API and every worker, provisioned Grafana dashboards, Flower for Celery, and a
durable row for every background run.

## How it works

### Visualization: the tools inside the app

A **Visualization** button in the top bar opens the operations tools over the workspace, one tab
each, so nobody has to know four ports or leave the app to see what it is doing.

| Tab | Tool | Shows | Served from |
| --- | --- | --- | --- |
| Dashboards | Grafana | app and ingestion, message queues, database, host | `/grafana/` on the app's origin |
| Traces | Langfuse | the open notebook's answers, step by step, with tokens and cost | Langfuse's own origin (`:3001`) |
| Tasks | Flower | workers and task history | `/flower/` on the app's origin |
| Queues | RabbitMQ management | queues and consumers | `/rabbitmq/` on the app's origin |

- **Behind the app's sign-in.** nginx serves Grafana, Flower and RabbitMQ under sub-paths and
  asks the app first (`auth_request` to `/auth/me`): no session, no dashboard. Grafana and Flower
  have no sign-in of their own here, so this is their gate. RabbitMQ keeps its own login on top.
- **Langfuse is the exception.** Its image cannot be served under a sub-path, so it is framed
  from its own origin and keeps its own sign-in, once per browser. It sends
  `frame-ancestors 'none'`, which would keep the tab blank, so nginx publishes it on host port
  3001 and rewrites that one directive to allow a page on the same host; the rest of Langfuse's
  policy is passed through as sent.
- **Traces follow the notebook.** A notebook's id is its Langfuse session id, so the tab opens on
  the notebook that is on screen and moves when another is opened.
- **Nothing loads until asked.** Each frame is created when its tab is first opened and then
  kept, so switching tabs does not reload a dashboard. Dashboards open in the app's own theme and
  in Grafana's kiosk mode; "Open in new tab" gives the full tool.
- **Addresses come from settings** (`application/services/core/visualization.py`,
  `GET /chat/visualization`). An empty address hides that tab; with the view off, or nothing
  configured, the button is not shown. A tool that is not running shows a note, not a blank frame.

Code: `src/presentation/web/static/js/visualization.js` (and `visualization_urls.js` for the pure
parts), `templates/partials/visualization.html`, `Docker/services/nginx/gateway.conf`.

### Tracing and prompt versions (Langfuse)

Self-hosted, in the compose profile `langfuse`. `src/shared/utils/tracing.py` opens one trace per
answer; guardrails, the plan, retrieval, rerank, sub-answers, synthesis and every model call nest
inside it, with token usage. Worker tasks continue the trace that queued them. Prompts are
versioned in Langfuse and served by label, with the locale files under
`src/application/prompts/` as the fallback (`scripts/langfuse_seed_prompts.py` seeds them). Cost
comes from `shared/utils/model_pricing.py` plus `MODEL_PRICES`; local Ollama models cost 0.

### Metrics, dashboards and Flower

Metrics at `/metrics`: ingest duration per stage, embedding latency and batch size,
time-to-first-token, retrieval latency, whether an answer was grounded. Labels are bounded to
`provider`, `model` and `stage`, never a `chat_id`.

Prometheus scrapes six jobs, all addressed by compose service name so nothing depends on
a published port:

| Job | Source |
| --- | --- |
| `notebookllm` | the API's own `/metrics` |
| `celery` | each Celery worker, on `WORKER_METRICS_PORT` (9808) |
| `postgres` | `postgres-exporter` |
| `rabbitmq` | `rabbitmq_prometheus`, on `/metrics/per-object` |
| `prometheus` | itself |
| `node` | `node_exporter` **on the host**, not in the stack |

The `celery` job exists because ingest, embedding and LLM generation run in the workers, not
the API, so the histograms they record never reached the API's `/metrics` — the ingest-stage
and generation-latency panels sat on "No data" while documents were being processed. Workers
are prefork, so each one runs prometheus_client in multiprocess mode: every child writes its
samples under `PROMETHEUS_MULTIPROC_DIR` (a tmpfs, set only on the workers in compose) and the
worker's parent serves the merged view (`celery_app.py`). The API does not get the variable;
it would switch it to file-backed metrics too.

The RabbitMQ job needs `/metrics/per-object` specifically. The default `/metrics` aggregates
everything into one series per metric with no `queue` label, which is why the queue panels
rendered empty: `rabbitmq_queue_messages_ready` came back as a single `{instance, job}` series
and the dashboard's `$queue` variable had nothing to resolve. Per-object is per *queue*, and
Celery creates far more queues than this application defines — 28 `celery_delayed_N` for
native delayed delivery, plus a `celeryev.<uuid>` and a `celery@<host>.celery.pidbox` per
worker, all churning on every restart — so a `metric_relabel_configs` drop keeps the stored
series to the queues that mean something.

**Flower** runs on `:5555` for Celery itself: live workers, task history, per-task metrics at
its own `/metrics`. It is built from the application image rather than `mher/flower` so
`--app celery_app:celery_app` reads the same broker and backend URLs the workers do, instead
of a second copy of the DSN in compose that could drift. Task history needs events, enabled
once in `celery_app.py` (`worker_send_task_events`, `task_send_sent_event`) rather than as
`-E` on four worker commands that could fall out of step.

Flower's Broker tab lists the app's queues through RabbitMQ's management API, whose URL
`src/flowerconfig.py` builds from the broker credentials (so no password is written into
compose); the same file works around Flower 2.1.0 requesting `/api/queues//` instead of
`/api/queues/%2F` on the default vhost. The per-queue *counts* there read N/A by design:
`rabbitmq.conf` turns the management metrics collector off, a deprecated feature, and queue
depths come from the Prometheus plugin into Grafana instead.

Note that `FLOWER_BASIC_AUTH=""` does **not** mean "no authentication" — Flower reads an empty
string as "auth is on, with no valid users" and answers `401` to every route except
`/healthcheck`, so the container passes its health probe while the dashboard is unreachable.
`env/.env.app` keeps the variable commented out instead.

`node_exporter` is the one piece to install separately (`apt install
prometheus-node-exporter`). Both the app and Prometheus map `host.docker.internal` to
`host-gateway`, because that name is a Docker Desktop convenience that does not exist on
Linux — without it the node job sits down with a DNS error while every other target is green.

`/nlp/health` is deliberately **not** scraped: it performs a real embedding inference, so a
15s scrape would fire one four times a minute forever. Liveness comes from the app's job
being up at all.

Grafana provisions its dashboards by scanning `Docker/services/grafana/dashboards/`, so a new `*.json`
there is picked up without being listed anywhere: FastAPI observability, PostgreSQL, host, and
RabbitMQ broker/queue health.

![Observability walkthrough: Flower workers, task history and a task's detail, Prometheus targets, then the Grafana FastAPI, ingest, latency, PostgreSQL, host and RabbitMQ views](../../demo/observability.gif)
The stack after ingesting a Wikipedia article and generating a quiz and flashcards from it, in
order: Flower's workers and task history — per-task state, runtime and worker, so a failure is a
click away instead of a log grep — and one Studio task's arguments and result; every Prometheus
target up, the six workers included; then Grafana. The FastAPI dashboard shows request volume,
per-route latency and the 2xx/5xx split; the next two frames are its worker panels, the p95 of
each ingest stage (summarising and generating dominate) and embedding versus LLM generation
latency; then PostgreSQL, the host from `node_exporter`, and RabbitMQ's ready and
unacknowledged messages per queue, filtered to the queues that mean something per the
relabeling above. The individual frames are in [`demo/observability/`](../../demo/observability/).

### The sweep

`maintenance_task` (`src/application/tasks/jobs/maintenance.py`) marks runs that can no longer be
running as `DEAD`, deletes finished rows past retention, and removes expired sign-ins. See
[ingestion](03_ingestion_pipeline.md#background-processing).

### Error handling

Domain errors live in `exceptions.py`, each carrying the status it maps to —
`NotFoundError` 404, `InvalidInputError` 400, `DbError` 503, `LLMProviderError` 502. Routes
raise them and one handler in `main.py` turns them into responses, so no route builds an
error body by hand and no failure is logged twice. Anything unrecognised becomes a 500 with
the traceback in the log and nothing revealing in the response.

### Logging

stdlib `logging`, configured once in `utils/logging_config.py`: rotating file output under
`LOG_DIR`, optional console, and `text` or `json` format. Every request gets a correlation id
attached by `middleware/request_logging.py` and carried on every line it produces. Log lines
carry counts and sizes — never prompt text, answer text or document content, which is exactly
what should not be sitting in a log file.

## Settings

| Setting | Does what |
| --- | --- |
| `LANGFUSE_ENABLED`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | tracing; off whenever either key is missing |
| `LANGFUSE_SAMPLE_RATE` / `LANGFUSE_CAPTURE_IO` | fraction of traces kept; whether prompt and answer text is sent |
| `LANGFUSE_PROMPTS_ENABLED` / `LANGFUSE_PROMPT_LABEL` / `LANGFUSE_PROMPT_CACHE_TTL` | prompts served from Langfuse by label |
| `MODEL_PRICES` | USD per million tokens, keyed `<backend>/<model id>` with wildcards |
| `METRICS_ENABLED` / `METRICS_PATH` / `WORKER_METRICS_PORT` | the API's `/metrics`, and the port each worker serves its own on (9808) |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_TO_CONSOLE`, `LOG_TO_FILE`, `LOG_DIR` | logging |
| `VISUALIZATION_ENABLED` | the Visualization button and view |
| `VISUALIZATION_GRAFANA_URL` / `_FLOWER_URL` / `_RABBITMQ_URL` | where the browser finds each tool; `/grafana/`, `/flower/`, `/rabbitmq/` behind nginx. Empty hides the tab |
| `VISUALIZATION_LANGFUSE_URL` / `VISUALIZATION_LANGFUSE_PROJECT_ID` | Langfuse as the browser reaches it, and its project id |
| `CELERY_MANAGEMENT_PATH_PREFIX` | RabbitMQ's `management.path_prefix`, so Flower's Broker tab asks the right address |
| `FLOWER_*` | read by Flower itself from the environment |

## Tests

`tests/observability/test_tracing.py`, `test_model_pricing.py`. Visualization:
`tests/domain/test_visualization.py`, `tests/api/test_visualization.py` (including a check that
nginx keeps each tool behind the session), `tests/frontend/visualization.test.mjs`.

## Diagrams

[Langfuse tracing and prompt versions](../../software_engineering/3_sequence_diagrams/langfuse_tracing.png) ·
[Visualization](../../software_engineering/3_sequence_diagrams/visualization.png) · [operations use cases](../../software_engineering/2_use_case_diagrams/operations.png) ·
[user stories](../../software_engineering/1_user_stories/06_settings_and_operations.png)

## Known limits

- Flower's per-queue counts read N/A by design; queue depths are in Grafana.
- Langfuse needs its own sign-in inside the Traces tab, once per browser.
- Visualization shows whoever is signed in the whole deployment's dashboards, not only their
  own notebooks. That suits a single-team install; a multi-tenant one should leave it off.
- `node_exporter` is installed on the host, not in the stack.
