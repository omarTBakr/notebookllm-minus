"""Periodic upkeep of the task_executions table.

The table is append-only during normal use: every upload writes two rows and
nothing ever removes them, so without this it grows for the life of the
deployment. Celery's own results expire on their own — Redis does it — but
these rows are the durable copy, and durable means nothing deletes them unless
something is asked to.

The sweep also closes the one gap the table cannot close by itself. A task
whose worker is killed mid-run leaves its row STARTED forever: the process
that would have written the ending no longer exists, and no amount of care in
the task body can cover that case. Anything that has been STARTED for longer
than a task is allowed to run is not running — it is gone — and saying so is
the difference between "still working" and "silently lost".
"""

import asyncio
from datetime import datetime, timedelta, timezone

from celery_app import SETTINGS, celery_app
from enums import TaskExecutionStatus
from factories import DbFactory
from models import TaskModel
from utils import get_logger

logger = get_logger(__name__)


async def _run_sweep() -> dict:
    db = DbFactory(SETTINGS).create()

    try:
        await db.connect()
        tasks = TaskModel(db)

        now = datetime.now(timezone.utc)

        # Derived from the hard time limit rather than configured separately:
        # no task may run longer than CELERY_TASK_TIME_LIMIT, so anything that
        # claims to have been running for twice that is definitionally not.
        started_before = now - timedelta(seconds=SETTINGS.CELERY_TASK_TIME_LIMIT * 2)

        # Queued work gets the full retention window instead, and the
        # difference matters: a task waits legitimately for as long as its
        # workers are down, so a tight cutoff here would report a deploy as
        # data loss. After a week it really is not going to run.
        queued_before = now - timedelta(days=SETTINGS.CELERY_TASK_RETENTION_DAYS)

        delete_before = now - timedelta(days=SETTINGS.CELERY_TASK_RETENTION_DAYS)

        # Mark first, delete second. A stale row that has also aged past
        # retention should leave as a recorded loss rather than be deleted
        # while still claiming to be running, which is what the reverse order
        # would do.
        marked = await tasks.mark_abandoned(started_before, queued_before, TaskExecutionStatus.DEAD.value)
        deleted = await tasks.delete_finished_before(delete_before)

        if marked or deleted:
            logger.info(
                "Task sweep: %d abandoned run(s) marked DEAD, %d finished row(s) removed",
                marked,
                deleted,
            )

        return {
            "marked_dead": marked,
            "deleted": deleted,
            "retention_days": SETTINGS.CELERY_TASK_RETENTION_DAYS,
        }

    finally:
        await db.disconnect()


@celery_app.task(
    name=f"{SETTINGS.CELERY_PROJECT_NAME}.maintenance_task",
    queue=SETTINGS.CELERY_QUEUE_MAINTENANCE,
)
def maintenance_task() -> dict:
    """Sweep the task table. Scheduled by beat; safe to run by hand."""
    return asyncio.run(_run_sweep())
