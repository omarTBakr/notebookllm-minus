#!/usr/bin/env bash
#
# Clear this project's local data, and nothing else.
#
# This machine runs other compose projects (temporal, legal-review-agent,
# aiagent-*). Every command below is scoped to compose project `docker` -- the
# directory name, which is what compose derives the project from -- and names
# the container or database it touches explicitly. There is deliberately no
# `docker system prune`, no `docker volume prune`, and no `compose down -v`:
# each of those reaches outside this project or destroys volumes we want to
# keep, and none of them is needed to empty a database.
#
# Data is cleared *in place*. The volumes (docker_pgvector, docker_redis,
# docker_rabbitmq) survive, which matters for one specific reason: the pgvector
# volume holds the only copy of the postgres password. The container reads
# POSTGRES_PASSWORD from Docker/env/.env.postgres only when it initialises an
# empty volume; a fresh volume created with a different value there would leave
# the app unable to connect. Compose refuses to start without that file, but
# destroying and recreating the volume is still not something to do casually.
#
# Usage:  ./scripts/reset_local_data.sh          (asks first)
#         ./scripts/reset_local_data.sh --yes    (does not)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT/Docker/docker-compose.yml"

PG_CONTAINER="pgvector-db"
PG_DB="notebookllm-minus"
PG_USER="postgres"

REDIS_CONTAINER="redis"
REDIS_PASSWORD="admin"
REDIS_DB=0 # CELERY_BACKEND_DB

RABBIT_CONTAINER="rabbitmq"
CELERY_PROJECT_NAME="notebookllm"

MONGO_DB="notebookllm_minus"
MONGO_USER="root"
# The root password the running mongo container was initialised with. It used to
# differ between env/.env.app ("example") and env/.env.mongo ("admin"); both now
# say "admin", but a volume created before that keeps whatever it was created
# with, so change this only if that volume is recreated.
MONGO_PASSWORD="example"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if [ "${1:-}" != "--yes" ]; then
    cat <<EOF
This will permanently delete, for project '$PG_DB' only:

  postgres   every row in users, sessions, chats, messages, projects,
             assets, chunks, artifacts, task_executions, and every
             vec_project_* table (dropped outright)
  redis      DB $REDIS_DB (celery results)
  rabbitmq   the $CELERY_PROJECT_NAME.* queues
  mongo      database $MONGO_DB, if a mongo container is running
  disk       uploaded files, the stale embedded qdrant store, and logs

Nothing belonging to any other compose project is touched.
EOF
    read -r -p $'\nType "wipe" to continue: ' reply
    [ "$reply" = "wipe" ] || {
        echo "Aborted."
        exit 1
    }
fi

# --- the services we need, and only those ------------------------------------
say "Starting pgvector, redis, rabbitmq"
docker compose -f "$COMPOSE" up -d pgvector redis rabbitmq

printf 'Waiting for pgvector'
for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$PG_CONTAINER" 2>/dev/null)" = "healthy" ] && break
    printf '.'
    sleep 2
done
echo

# --- postgres ----------------------------------------------------------------
# The vec_project_* tables are created at runtime by PostgresVectorRepository,
# not by Alembic (alembic/env.py:47-63 filters them out of autogenerate), so
# they are dropped rather than truncated -- the app recreates each one on the
# next index run.
#
# alembic_version is deliberately left alone: the schema itself is still
# current, and clearing it would make the app re-run every migration from
# scratch against tables that already exist.
say "Clearing postgres ($PG_DB)"
docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" <<'SQL'
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name LIKE 'vec\_project\_%'
  LOOP
    EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', t);
    RAISE NOTICE 'dropped %', t;
  END LOOP;
END $$;

-- One statement so the mutual foreign keys never see a half-empty database.
-- RESTART IDENTITY because task_executions and messages carry serial ids that
-- would otherwise keep counting up from a run that no longer exists.
TRUNCATE TABLE
  chunks, assets, artifacts, task_executions,
  messages, chats, sessions, projects, users
RESTART IDENTITY CASCADE;
SQL

# --- redis -------------------------------------------------------------------
# FLUSHDB, not FLUSHALL: the app only ever uses CELERY_BACKEND_DB, and this
# redis instance is shared with nothing else in the project but may as well not
# be assumed empty elsewhere.
say "Flushing redis DB $REDIS_DB"
docker exec "$REDIS_CONTAINER" redis-cli -a "$REDIS_PASSWORD" --no-auth-warning -n "$REDIS_DB" FLUSHDB

# --- rabbitmq ----------------------------------------------------------------
# These are quorum queues, and a quorum queue's type is fixed at declaration:
# redeclaring one with a different x-queue-type fails PRECONDITION_FAILED
# (celery_queues.py:20-43). Deleting them while nothing is publishing is both
# how the backlog is cleared and how a new queue gets declared cleanly.
say "Deleting $CELERY_PROJECT_NAME.* queues"
printf 'Waiting for rabbitmq'
for _ in $(seq 1 60); do
    docker exec "$RABBIT_CONTAINER" rabbitmq-diagnostics -q ping >/dev/null 2>&1 && break
    printf '.'
    sleep 2
done
echo

for q in $(docker exec "$RABBIT_CONTAINER" rabbitmqctl list_queues -s name 2>/dev/null |
    grep "^${CELERY_PROJECT_NAME}\." || true); do
    docker exec "$RABBIT_CONTAINER" rabbitmqctl delete_queue "$q" || true
done

# --- mongo (dormant backend, but it still holds old rows) --------------------
say "Dropping mongo database $MONGO_DB (if running)"
MONGO_RUNNING="$(docker ps --filter "name=mongo" --format '{{.Names}}' | head -1)"
if [ -n "$MONGO_RUNNING" ]; then
    docker exec "$MONGO_RUNNING" mongosh --quiet \
        -u "$MONGO_USER" -p "$MONGO_PASSWORD" --authenticationDatabase admin \
        --eval "db.getSiblingDB('$MONGO_DB').dropDatabase()" ||
        echo "  (could not authenticate; skipping -- the mongo backend is not in use)"
else
    echo "  (no mongo container running; skipping)"
fi

# --- disk --------------------------------------------------------------------
# Two upload directories, not one. FileController.py:21 builds its path as
# `Path(__file__).parent.parent / "assets"`, and that expression changed meaning
# when the file moved into controllers/ingest/ (commit 508bc7b) -- so uploads
# made before the move live under src/assets and uploads after it under
# src/controllers/assets. Both are cleared here; fixing the path is a separate
# change.
say "Clearing files on disk"
rm -rf "$ROOT/src/assets/Files" "$ROOT/src/controllers/assets/Files"
rm -rf "$ROOT/src/assets/qdrant_db" # stale embedded qdrant store, gitignored
rm -f "$ROOT"/src/logs/*.log*
mkdir -p "$ROOT/src/assets"

say "Done."
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT 'remaining rows: users='||(SELECT count(*) FROM users)
        ||' projects='||(SELECT count(*) FROM projects)
        ||' assets='||(SELECT count(*) FROM assets)
        ||' chunks='||(SELECT count(*) FROM chunks)
        ||' vec tables='||(SELECT count(*) FROM information_schema.tables
                           WHERE table_schema='public' AND table_name LIKE 'vec\_%');"
