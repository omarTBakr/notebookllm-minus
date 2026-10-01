"""The work Celery actually runs: one module per queued job.

Each is a `@celery_app.task` entry point with an explicit name, so a task keeps
its identity — and any message already sitting on a queue stays valid — no
matter which module it lives in.

Every job is split in two: a plain `async def` taking an already-connected db
(`plan_ingestion`, `parse_batch`, `assemble_chunks`), and a task wrapper that
opens that connection -- through `tasks.runtime.job_resources` -- and does the
bookkeeping around it. The body can then be called directly from a test with no
broker and no worker, which is how `test/fakes/ingest.py` drives a whole
ingestion in-process.

Ingesting a PDF is four of them, not one, joined by queues rather than by a
canvas, and they live together in `ingest/`: `process_data_task` plans the page batches and publishes one
`parse_batch_task` each; every parse stores its pages and queues a
`postprocess_batch_task`; and whichever of those finishes last claims the
run and dispatches `assemble_chunks_task`. Completion is a row count in
`ingest_runs`, not Celery's chord accounting -- see that table's migration
for what went wrong when it was.

Registration is by module path in `celery_app.py`'s `include=[...]`. A job
module missing from that list never registers, and its queue then accepts
messages nothing will ever consume — silently, forever. Adding a module here,
or moving one, means changing it there; `test_every_job_module_is_registered`
fails when it is forgotten.
"""

from .index import build_vector_index_task, index_project_task
from .ingest import (
    assemble_chunks_task,
    parse_batch_task,
    postprocess_batch_task,
    process_data_task,
)
from .maintenance import maintenance_task
from .memory import extract_memory_task
from .studio import generate_artifact_task

__all__ = [
    "assemble_chunks_task",
    "build_vector_index_task",
    "extract_memory_task",
    "generate_artifact_task",
    "index_project_task",
    "maintenance_task",
    "parse_batch_task",
    "postprocess_batch_task",
    "process_data_task",
]
