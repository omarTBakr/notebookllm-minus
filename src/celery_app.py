import os
from pathlib import Path

from celery import Celery
from celery.signals import worker_init, worker_process_shutdown

from celery_queues import celery_queue_config
from shared.utils.config import get_settings
from shared.utils.logging_config import setup_logging

# Use the cached singleton — same object the rest of the app uses, avoids
# a second .env parse and a second set of validators running at import time.
SETTINGS = get_settings()

# Workers are a separate process tree from the API, and main.py's call to this
# never runs in them. Without it a worker logged in Celery's stock format with
# no request id, so a failure in a task could not be tied back to the HTTP
# request that queued it — the logs looked like two unrelated applications.
setup_logging()

celery_app = Celery(
    # `main`, not `name`: Celery's first parameter is main, and an unknown
    # `name=` kwarg is swallowed silently, leaving app.main None. It is what
    # names the app in logs and `celery report`, and what auto-generated task
    # names are derived from for any task that does not set one explicitly.
    main="notebookllm",
    broker=SETTINGS.celery_broker_url,
    backend=SETTINGS.celery_result_backend_url,
    # Registration is by module path, and a job module missing from this list
    # never registers its tasks — the worker then rejects them as unknown, which
    # for a chord member means the whole ingestion hangs on a callback that can
    # never fire.
    include=[
        "application.tasks.jobs.ingest.fetch",
        "application.tasks.jobs.ingest.process",
        "application.tasks.jobs.ingest.parse",
        "application.tasks.jobs.ingest.postprocess",
        "application.tasks.jobs.ingest.assemble",
        "application.tasks.jobs.ingest.summarise",
        "application.tasks.jobs.index",
        "application.tasks.jobs.studio",
        "application.tasks.jobs.maintenance",
        "application.tasks.jobs.memory",
    ],
)

celery_app.conf.update(
    # --- serialisation -------------------------------------------------------
    task_serializer=SETTINGS.CELERY_TASK_SERIALIZER,
    result_serializer=SETTINGS.CELERY_RESULT_SERIALIZER,
    accept_content=SETTINGS.CELERY_ACCEPT_CONTENT,
    # --- time / locale -------------------------------------------------------
    timezone=SETTINGS.CELERY_TIMEZONE,
    enable_utc=SETTINGS.CELERY_ENABLE_UTC,
    # --- task execution ------------------------------------------------------
    task_time_limit=SETTINGS.CELERY_TASK_TIME_LIMIT,
    # Raises SoftTimeLimitExceeded *inside* the task so its `finally` blocks
    # run. The hard limit above kills the child outright and skips them.
    task_soft_time_limit=SETTINGS.CELERY_TASK_SOFT_TIME_LIMIT,
    task_acks_late=SETTINGS.CELERY_TASK_ACKS_LATE,
    # Without this a running task is reported as PENDING, indistinguishable
    # from one still sitting in the queue.
    task_track_started=SETTINGS.CELERY_TASK_TRACK_STARTED,
    # --- results -------------------------------------------------------------
    # Explicit rather than Celery's invisible 1-day default.
    result_expires=SETTINGS.CELERY_RESULT_EXPIRES,
    # --- events (Flower) -----------------------------------------------------
    # Set here rather than as -E on each worker command, so one setting covers
    # every worker and the four compose commands cannot drift apart.
    worker_send_task_events=SETTINGS.CELERY_WORKER_SEND_TASK_EVENTS,
    task_send_sent_event=SETTINGS.CELERY_TASK_SEND_SENT_EVENT,
    # --- worker --------------------------------------------------------------
    # The resolved value, not the raw field: 0 means "every available CPU"
    # and Celery would read a literal 0 as "spawn no worker processes". This is
    # also the only place the process worker's concurrency is set -- its
    # compose command no longer passes --concurrency, because a CLI flag
    # overrides conf and a shell cannot count the CPUs a cgroup allows.
    worker_concurrency=SETTINGS.celery_worker_concurrency,
    # Cancel tasks that are still running when the worker loses its broker
    # connection so they don't execute silently after a reconnect.
    worker_cancel_long_running_tasks_on_connection_loss=(
        SETTINGS.CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS
    ),
    # --- broker resilience ---------------------------------------------------
    broker_connection_retry_on_startup=SETTINGS.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP,
    broker_connection_retry=SETTINGS.CELERY_BROKER_CONNECTION_RETRY,
    broker_connection_retry_delay=SETTINGS.CELERY_BROKER_CONNECTION_RETRY_DELAY,
    broker_connection_max_retries=SETTINGS.CELERY_BROKER_CONNECTION_MAX_RETRIES,
    # --- transport options ---------------------------------------------------
    broker_transport_options=SETTINGS.CELERY_BROKER_TRANSPORT_OPTIONS,
    result_backend_transport_options=SETTINGS.CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS,
    # --- scheduled work ------------------------------------------------------
    # One entry, run by `celery beat`. Nothing else in the app is periodic, so
    # this is deliberately a literal schedule rather than a registry: a second
    # entry can be added beside it when a second job exists.
    beat_schedule={
        "sweep-task-executions": {
            "task": f"{SETTINGS.CELERY_PROJECT_NAME}.maintenance_task",
            "schedule": SETTINGS.CELERY_MAINTENANCE_INTERVAL_HOURS * 3600.0,
            "options": {"queue": SETTINGS.CELERY_QUEUE_MAINTENANCE},
        },
    },
    **celery_queue_config(SETTINGS),
)

# Set separately: conf.update() does not accept task_default_queue as a kwarg
# in all Celery versions — the attribute assignment is the safe path.
celery_app.conf.task_default_queue = SETTINGS.CELERY_TASK_DEFAULT_QUEUE


# --- worker metrics -----------------------------------------------------------
# Ingest, embedding and LLM generation all run here, in prefork children, and
# the API's /metrics never sees them: the "Ingest Stage Duration" panel sat on
# "No data" while documents were being ingested. prometheus_client's
# multiprocess mode is the standard answer -- each child writes its samples to
# files under PROMETHEUS_MULTIPROC_DIR, and the parent serves the merged view.
#
# The variable has to be set before prometheus_client is first imported, so it
# comes from compose (the x-celery-worker block), not from here; unset, as in
# the API container or a local `celery worker`, this does nothing.
_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


@worker_init.connect
def _serve_worker_metrics(**_) -> None:
    """Serve every child's metrics from the parent, on WORKER_METRICS_PORT.

    Runs once, in the parent, before the pool forks. The directory is emptied
    first: files left by a previous run would be merged in as if current.
    """
    if not (SETTINGS.METRICS_ENABLED and _MULTIPROC_DIR):
        return

    from prometheus_client import CollectorRegistry, multiprocess, start_http_server

    for stale in Path(_MULTIPROC_DIR).glob("*.db"):
        stale.unlink(missing_ok=True)

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    start_http_server(SETTINGS.WORKER_METRICS_PORT, registry=registry)


@worker_process_shutdown.connect
def _forget_dead_child(pid=None, **_) -> None:
    """Drop a finished child's live samples, so a recycled pool does not pile up."""
    if not (SETTINGS.METRICS_ENABLED and _MULTIPROC_DIR) or pid is None:
        return

    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(pid)
