"""What every job does around its own work.

Two pieces of scaffolding, written out by hand in each job before this and
identical every time:

* `job_resources` — a connected database, optionally a pool of provider
  clients, and the teardown for both. The ordering is the reason it is worth
  sharing: the pools must be closed before the connection they were opened
  beside, and the database must be closed even if closing a pool raises. Eight
  hand-written copies is eight chances for one of them to leak an HTTP pool per
  run, which is a leak that only shows up under load.

* `run_job` — the synchronous half of a task: run the coroutine, and turn a
  soft time limit into an error that names the job.

Deliberately not here: anything to do with `TaskRecorder`. Every job records
its own progress differently — stages it reports, downstream ids it abandons,
a chat message or an artifact row it has to mark failed — and a hook for each
of those would be a worse abstraction than the three lines it replaced.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from celery.exceptions import SoftTimeLimitExceeded

from exceptions import CeleryTaskError
from factories import DbFactory, ProviderCache
from utils import get_logger, get_settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class JobResources:
    """What a job is given for the length of one run."""

    settings: Any
    db: Any
    #: None unless the job asked for it, so a job that makes no model calls
    #: never builds a pool it would then have to close.
    providers: ProviderCache | None = None


@asynccontextmanager
async def job_resources(*, providers: bool = False):
    """Connect for the length of one job, and close in the right order."""
    settings = get_settings()
    db = DbFactory(settings).create()
    pool = ProviderCache(settings) if providers else None

    try:
        await db.connect()
        yield JobResources(settings=settings, db=db, providers=pool)
    finally:
        # Nested, not one block: a pool that raises on close must not take the
        # database connection with it.
        try:
            if pool is not None:
                await pool.aclose_all()
        finally:
            await db.disconnect()


def run_job(body: Callable[[], Awaitable[dict]], *, what: str) -> dict:
    """Run a job's async body, reporting a soft time limit as what it is.

    The soft limit is raised *inside* the coroutine, so `job_resources`' finally
    still runs and the connections close; the hard limit is a SIGKILL that skips
    them. `what` names this run -- "Assembling 'abc123'" -- because a timed-out
    job and a broker fault otherwise read identically in the logs, and telling
    them apart is the whole point of catching this.
    """
    try:
        return asyncio.run(body())

    except SoftTimeLimitExceeded as exc:
        limit = get_settings().CELERY_TASK_SOFT_TIME_LIMIT

        logger.error("%s exceeded its soft time limit of %ss", what, limit)

        raise CeleryTaskError(f"{what} exceeded {limit}s and was stopped") from exc
