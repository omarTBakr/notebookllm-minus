"""Knowing what a task did: progress while it runs, outcome after.

`recorder` writes a job's stages into `task_executions` as it goes, which is
what lets the browser show real progress instead of a spinner; `status` reads
that back and is the single answer to "what happened to this task?", shared by
the routes and the workers so the two cannot disagree.

Separate from `jobs` because every job depends on these and none of them
depends on a job — the direction of that arrow is the reason the split holds.
"""

from .recorder import TaskRecorder, downstream_ids
from .status import mark_queued, task_status

__all__ = ["TaskRecorder", "downstream_ids", "mark_queued", "task_status"]
