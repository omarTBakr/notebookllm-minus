---
name: nlm-stack
description: Bring up, rebuild or check the NotebookLLM-minus Docker Compose stack on this machine (the My Passport drive, Docker Desktop). Use before any live check, evaluation, load test or demo recording, or when the user says the stack is up/down, asks to rebuild, or a container will not start.
---

# The local stack: bring it up, rebuild it, check it

All commands from `Docker/`. Never edit `Docker/env/*` or `Docker/.env` without asking: they hold live secrets and the classifier blocks it. Give the user the lines to paste instead.

## 1. Use the stack's own compose project name
The running containers belong to a project name, not the directory name. Find it first:

```bash
docker inspect fastapi-app --format '{{index .Config.Labels "com.docker.compose.project"}}'   # e.g. nlm-e2e
export COMPOSE_PROJECT_NAME=nlm-e2e
```

Without it, compose uses `docker`, tries to create a second set of volumes and containers, and fails on a name conflict, leaving empty `docker_*` volumes behind (2026-11-13). Those are safe to remove only with the user's say-so.

## 2. Ports another project already holds
This machine also runs another stack (`basir-*`) that holds 3000, 9090 and 9187. Compose stops at the first failed port and leaves later services (nginx, Grafana) unstarted. Pass free ports through the environment:

```bash
export GRAFANA_PORT=3010 PROMETHEUS_PORT=9092 POSTGRES_EXPORTER_PORT=9188
docker compose --profile langfuse up -d --no-build
```

`NGINX_PORT` (default 8081) works the same way. Check what is taken with `ss -ltnH`.

## 3. Before recreating anything: is the drive's share stale?
Docker Desktop's view of this NTFS drive goes stale after days of uptime: a replaced file keeps its old contents inside containers. Check a file you changed:

```bash
wc -l services/nginx/gateway.conf
docker run --rm -v "$PWD/services/nginx/gateway.conf:/c:ro" alpine wc -l /c
```

If the numbers differ, either give the file a fresh name and update the mount (what `gateway.conf` is), or `systemctl --user restart docker-desktop`, which also restarts the other project's containers, so ask first.

## 4. Rebuilding the app image is slow on this drive
`docker compose build fastapi` scans the whole repository before uploading context. Expect 10 to 15 minutes even with a 28 MB context. Run it in the background and wait on a marker line, not with `| tail`. If the upload passes 100 MB, something is not excluded: check `services/notebookllm-minus/Dockerfile.dockerignore` (the image only needs `src/`; `**/.venv*/` and nested caches must be excluded).

## 5. Order matters for a few services
- Langfuse's host port 3001 belongs to nginx (it rewrites Langfuse's frame policy). When switching an old stack over, recreate `langfuse-web` first (`up -d --no-deps langfuse-web`) so the port is free for nginx.
- After a change to `services/rabbitmq/rabbitmq.conf`, `docker compose restart rabbitmq`: compose does not recreate it for a content change.
- Grafana reads `env/.env.grafana`; it needs the `GF_SERVER_SERVE_FROM_SUB_PATH` / `GF_SECURITY_ALLOW_EMBEDDING` lines from `env.example/.env.grafana` for Visualization.

## 6. Check it
```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -v basir | sort
for p in /base/health /grafana/ /flower/ /rabbitmq/; do echo "$p $(curl -s -m 10 -o /dev/null -w '%{http_code}' http://localhost:8081$p)"; done
```
Signed out, the three tools answer 401 (nginx's session gate). `/base/health` answers 200. Langfuse is `http://localhost:3001`.
