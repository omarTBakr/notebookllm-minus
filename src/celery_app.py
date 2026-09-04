from celery import Celery

from celery_queues import celery_queue_config
from utils.config import get_settings
from utils.logging_config import setup_logging

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
    include=["tasks.process", "tasks.index", "tasks.maintenance"],
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
    worker_concurrency=SETTINGS.CELERY_WORKER_CONCURRENCY,
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
