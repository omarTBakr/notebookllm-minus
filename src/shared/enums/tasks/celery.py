"""Celery queue and task naming constants."""

from enum import StrEnum


class CeleryTaskFunction(StrEnum):
    """Function names used in task and queue identifiers."""

    PROCESS = "process_data_task"
    # The three stages PROCESS fans out into for a PDF. PROCESS itself stops
    # doing the extraction and becomes a planner: it reads the page count and
    # publishes one PARSE per page batch. Each PARSE stores its pages and
    # queues a POSTPROCESS; whichever POSTPROCESS finishes last claims the run
    # and dispatches ASSEMBLE.
    #
    # Only POSTPROCESS gets a queue of its own, because it is the only one whose
    # work is unlike the others': it waits on a local model for tens of seconds
    # a page, and behind an extraction it would block every other document's
    # parsing. PARSE and ASSEMBLE stay on PROCESS's queue, which is already
    # consumed by celery-process -- see celery_queues on why a queue with no
    # `-Q` consumer is worse than no queue at all.
    PARSE = "parse_batch_task"
    POSTPROCESS = "postprocess_batch_task"
    ASSEMBLE = "assemble_chunks_task"
    # After ASSEMBLE, beside INDEX: one message per batch of chunks, writing the
    # one-line summaries Studio generates from. On POSTPROCESS's queue, the
    # other stage that waits on a model -- see tasks/jobs/ingest/summarise.py.
    SUMMARISE = "summarise_chunks_task"
    INDEX = "index_project_task"
    # The ANN build, split out of INDEX so it is its own link in the chain,
    # its own row in task_executions, and its own bar in Flower. It shares
    # INDEX's queue rather than getting one of its own — see celery_queues.
    BUILD_INDEX = "build_vector_index_task"
    GENERATE_ARTIFACT = "generate_artifact_task"
    MAINTENANCE = "maintenance_task"
    # Extraction for a `/memory` chat message: pull durable facts out of the
    # text, upsert them, and (re-)embed each one.
    MEMORY = "extract_memory_task"


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
    """Progress through a long-running job, in order.

    The first four are the names the synchronous upload path reported through
    its in-process dict, kept identical so the browser's progress labels and
    the INGEST_STAGE_SECONDS metric carry over unchanged.

    The last two belong to Studio generation, which is a different pipeline
    reusing the same row and the same poll: a set summarises chunks it has not
    seen before, then writes items from those summaries, batch by batch.
    """

    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    STORING = "storing"
    INDEXING = "indexing"
    SUMMARISING = "summarising"
    GENERATING = "generating"
