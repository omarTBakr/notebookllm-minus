# Deployment

## What it does

Runs the whole stack with Docker Compose, and deploys `main` to one server when the test suite is
green.

## How it works

### Compose

```bash
cd Docker
cp -r env.example env           # per-service env files; fill in the REPLACE_ME values
docker compose up -d --build    # postgres profile: app, pgvector, nginx, prometheus, grafana
```

- **No `--env-file` flags**, on this or any other compose command: every service reads its own
  file in `Docker/env/` through `env_file:`, and the only compose-level setting (the nginx host
  port) is in `Docker/.env`. Each service's own config lives under `Docker/services/`;
  [`Docker/README.md`](../../Docker/README.md) has the full layout.
- **Profiles.** `DOCUMENT_DB_BACKEND` decides which database is used, not which containers run.
  Mongo and Qdrant sit behind `--profile mongo`; Langfuse behind `--profile langfuse`.
- **Workers.** `celery-process`, `celery-postprocess`, `celery-index`, `celery-studio`,
  `celery-memory`, `celery-guardrail-input`, `celery-guardrail-output`, `celery-maintenance` and
  `celery-beat`. A queue with no `-Q` consumer accepts tasks nothing will ever run.
- **nginx** is the only published entry point for the app (`:8081`). It also serves Grafana,
  Flower and RabbitMQ's management UI under `/grafana/`, `/flower/` and `/rabbitmq/`, to
  signed-in users only, for the app's Visualization view. Each tool is configured to live under
  its prefix: Grafana in `env/.env.grafana` (`GF_SERVER_SERVE_FROM_SUB_PATH`, `GF_SERVER_ROOT_URL`,
  `GF_SECURITY_ALLOW_EMBEDDING`, anonymous Viewer), Flower by `--url_prefix=flower` in compose,
  RabbitMQ by `management.path_prefix` in `services/rabbitmq/rabbitmq.conf`. **Upgrading an
  existing install:** copy the new `GF_*` lines from `env.example/.env.grafana` and the
  `VISUALIZATION_*` and `CELERY_MANAGEMENT_PATH_PREFIX` lines from `env.example/.env.app` into
  your `env/` files. Direct addresses move with the prefixes: `:3000/grafana/`,
  `:5555/flower/`, `:15672/rabbitmq/`.
- **Ollama runs on the host**, reached as `host.docker.internal`. Multimodal retrieval needs
  Ollama 0.40 or newer with the embedding model pulled.
- **The reranker** is its own container: llama.cpp's `llama-server` with a GGUF of
  `bge-reranker-v2-m3`. See [reranking](09_reranking.md).

### Continuous deployment

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
--build` in `Docker/` in the checkout. The restart *is* the deployment only because the checkout
moved first; that is why the workflow pulls before restarting.

The unit needs no `--env-file` flags and no `COMPOSE_ENV_FILES`: every service reads `env/`
through `env_file:`, and compose reads `Docker/.env` by itself. A unit written before this
still passes `--env-file env/.env.nginx`, a file that no longer exists, and compose refuses to
start with it. Its `ExecStart` should be exactly

```
ExecStart=/usr/bin/docker compose up -d --build
```

with any `Environment=COMPOSE_ENV_FILES=...` line removed, followed by
`sudo systemctl daemon-reload`. The unit itself is not in the repo, so this is a one-time
manual edit on the server. Until it is made, a deploy fails loudly and leaves the running
containers untouched.

`.github/workflows/` also holds `server-diagnostics.yml` and `repair-db-password.yml`, run by hand.

## Settings

Environment files, not application settings: `Docker/env.example/` is the template for
`Docker/env/`, which is gitignored and never committed. `tests/configuration/test_env_example.py`
fails when a key in a template is not a real setting, or a setting is missing from
`src/.env.example`; `test_docker_paths.py` checks the paths compose mounts.

## Diagrams

[operations use cases](../../software_engineering/2_use_case_diagrams/operations.png)

## Known limits

See [deployment follow-ups](../roadmap/deployment_followups.md): the server's systemd unit needs a
one-time edit, YouTube needs a proxy on a cloud host, and a long-running Docker Desktop can refuse
new bind mounts until it is restarted.
