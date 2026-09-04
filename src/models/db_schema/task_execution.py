"""A durable record of one Celery task run.

Celery already tracks task state, but only in the Redis result backend and only
for CELERY_RESULT_EXPIRES seconds. That is enough to answer "is it done yet?"
and nothing else: the record cannot be joined to the project it acted on, it
disappears on expiry, and a task whose worker died stays STARTED forever
because nothing outlives the worker to say otherwise.

This row is the part that survives. It is written when the task is published,
updated as the task runs, and kept after it finishes — so ingestion history is
queryable per project, the browser can poll progress from any API process
rather than only the one that happened to accept the upload, and a repeated
submission can be recognised as work already in flight.
"""

from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from enums import TaskExecutionStatus

from .project import utcnow


class TaskExecution(BaseModel):
    # populate_by_name lets the model be built either from Mongo documents
    # (`_id`) or from keyword arguments (`id=`).
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")

    # Celery's own uuid. Named task_id rather than celery_task_id to match the
    # asset_id/project_id convention: the business id sits beside `id`, which
    # is the storage id, and every repository filters on the former.
    task_id: str = Field(..., min_length=1, max_length=200)

    # Fully qualified, e.g. "notebookllm.process_data_task".
    task_name: str = Field(..., min_length=1, max_length=200)

    # What the work was for. The column the progress poll reads, which is why
    # it is required rather than a nullable convenience.
    project_id: str = Field(..., min_length=1, max_length=200)

    # Set when the task targets a single document; "" for a whole-project run.
    asset_id: str = Field("", max_length=200)

    # The call's arguments, as published. Sensitive only in the sense that it
    # must not carry file bytes — the tasks take ids and re-read from the
    # database, so this stays small.
    args: dict = Field(default_factory=dict)

    # sha256 over a canonical encoding of (task_name, args), hex. "" rather
    # than None when not computed, matching Asset.content_hash: both the
    # lookup and the index short-circuit on the empty sentinel, so an
    # unhashed row can never collide with another unhashed row.
    args_hash: str = Field("", max_length=64)

    status: TaskExecutionStatus = Field(default=TaskExecutionStatus.QUEUED)

    # Ingestion progress. Free-form rather than the TaskStage enum because a
    # task that reports no stage at all is normal, and "" is not a member.
    stage: str = Field("", max_length=50)
    done: int = Field(0, ge=0)
    total: int = Field(0, ge=0)

    # The task's return value, with bulk stripped out — see summarize_result.
    result: dict = Field(default_factory=dict)

    # The same pair the status endpoint reports, kept so a failure survives
    # the Redis TTL that used to be the only place it existed.
    error: str = Field("", max_length=2000)
    error_type: str = Field("", max_length=200)

    # Nullable: a queued task has not started, and a running one has not
    # finished. created_at is when the row appeared, which is not the same as
    # when a worker picked the task up.
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def summarize_result(result: dict | None) -> dict:
    """A task's return value with the bulk removed, safe to store.

    ``process_data`` returns, per asset, every chunk it created *including the
    full text* — the whole document, in other words. That payload is already
    serialised into Redis on every run; writing it here as well would copy
    every ingested document into the task table a second time, and a 274-page
    PDF would make a single row larger than the asset it describes.

    Counts answer every question the row exists to answer ("did it work, how
    much did it do"), so the chunk bodies are dropped and their number kept.
    """
    if not isinstance(result, dict):
        return {}

    summary = {k: v for k, v in result.items() if k != "results"}

    entries = result.get("results")

    if isinstance(entries, list):
        summary["results"] = [
            {k: v for k, v in entry.items() if k != "chunks"} if isinstance(entry, dict) else entry for entry in entries
        ]

    return summary
