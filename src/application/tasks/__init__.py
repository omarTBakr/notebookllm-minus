"""Background work, and how it is tracked.

    jobs      the Celery entry points — ingest, index, generate, maintain
    tracking  progress written while a job runs, and its outcome afterwards
    workflows the canvas that chains jobs into one queued unit

The task functions are re-exported here, so `from application.tasks import
process_data_task` works regardless of which module holds it.
"""

from .jobs import (
    assemble_chunks_task,
    build_vector_index_task,
    extract_memory_task,
    generate_artifact_task,
    index_project_task,
    maintenance_task,
    parse_batch_task,
    postprocess_batch_task,
    process_data_task,
)
from .tracking import TaskRecorder, downstream_ids, mark_queued, task_status

__all__ = [
    "assemble_chunks_task",
    "TaskRecorder",
    "build_vector_index_task",
    "downstream_ids",
    "extract_memory_task",
    "generate_artifact_task",
    "index_project_task",
    "maintenance_task",
    "mark_queued",
    "parse_batch_task",
    "postprocess_batch_task",
    "process_data_task",
    "task_status",
]
