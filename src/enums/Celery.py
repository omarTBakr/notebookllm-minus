"""Celery queue and task naming constants."""

from enum import StrEnum


class CeleryTaskFunction(StrEnum):
    """Function names used in task and queue identifiers."""

    PROCESS = "process_data_task"
    INDEX = "index_project_task"
    # The ANN build, split out of INDEX so it is its own link in the chain,
    # its own row in task_executions, and its own bar in Flower. It shares
    # INDEX's queue rather than getting one of its own — see celery_queues.
    BUILD_INDEX = "build_vector_index_task"
    CHAT = "answer_chat_task"
    MAINTENANCE = "maintenance_task"


class TaskExecutionStatus(StrEnum):
    """The states a persisted task row moves through.

    Deliberately a superset of Celery's own: QUEUED/STARTED/SUCCESS/FAILURE
    mirror it, and DEAD does not exist in Celery at all. A task whose worker
    vanished stays STARTED forever from Celery's point of view — the row is the
    only place that can ever say otherwise.

    IN_FLIGHT is what the idempotency check treats as "already running", and is
    the reason these are values rather than free strings: a typo in a status
    literal would silently make deduplication stop matching.
    """

    QUEUED = "QUEUED"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DEAD = "DEAD"


#: Statuses that mean "this work is still outstanding".
IN_FLIGHT = (TaskExecutionStatus.QUEUED, TaskExecutionStatus.STARTED)


class TaskStage(StrEnum):
    """Ingestion progress, in order.

    The same four names the synchronous upload path reported through its
    in-process dict, kept identical so the browser's progress labels and the
    INGEST_STAGE_SECONDS metric carry over unchanged.
    """

    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    STORING = "storing"
    INDEXING = "indexing"
