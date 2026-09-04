"""Recognises work that is already in flight, so it is not queued twice.

A user double-clicks upload; a client retries a request whose response was lost;
a proxy replays a POST. Each of those queues a second identical ingestion, and
the two then race over the same documents. Nothing downstream is corrupted by
that — the tasks are individually re-runnable, by design — but it doubles the
embedding cost, and the caller is handed a task id that is not the one doing
the work they are watching.

The check is a query, not a unique constraint, and that is a deliberate trade.
A constraint would make two genuinely simultaneous submissions a 500 for the
loser, which is worse than the thing it prevents: the tasks already tolerate
being run twice (process skips assets that are already chunked, and indexing
upserts on a deterministic point id). So this closes the common case — a repeat
that arrives after the first row is committed — and lets the rare true race
through, where the cost is duplicated effort rather than an error.
"""

import hashlib
import json

from enums import TaskExecutionStatus
from models import TaskExecution, TaskModel

from .BaseController import BaseController


class IdempotencyController(BaseController):
    def __init__(self, db) -> None:
        super().__init__()
        self.tasks = TaskModel(db)

    def fingerprint(self, task_name: str, args: dict) -> str:
        """A stable sha256 over the call this task represents.

        Canonical JSON — sorted keys, fixed separators — because the hash has
        to survive the arguments being rebuilt from a dict whose key order is
        an implementation detail. `default=str` keeps a stray non-JSON value
        (a datetime, an ObjectId) from raising here: a fingerprint that is
        merely coarse is fine, one that throws on an odd argument is not.
        """
        canonical = json.dumps(
            {"task": task_name, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        return hashlib.sha256(canonical.encode()).hexdigest()

    async def claim(self, task_name: str, args: dict) -> TaskExecution | None:
        """An in-flight task with these exact arguments, or None.

        None means "nothing is running this; go ahead and queue it".
        """
        existing = await self.tasks.find_in_flight(task_name, self.fingerprint(task_name, args))

        if existing is not None:
            self.logger.info(
                "Reusing in-flight %s task %r rather than queueing a duplicate",
                task_name,
                existing.task_id,
            )

        return existing

    async def record(
        self,
        task_id: str,
        task_name: str,
        project_id: str,
        args: dict,
        asset_id: str = "",
    ) -> TaskExecution:
        """Write the QUEUED row for a task that has just been published."""
        task = TaskExecution(
            task_id=task_id,
            task_name=task_name,
            project_id=project_id,
            asset_id=asset_id or "",
            args=args,
            args_hash=self.fingerprint(task_name, args),
            status=TaskExecutionStatus.QUEUED,
        )

        await self.tasks.create_task(task)

        return task
