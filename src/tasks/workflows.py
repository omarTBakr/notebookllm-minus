"""Celery canvas: the ingestion pipeline as one queued unit.

Processing and indexing were two independent tasks that the *client* had to
sequence — POST /process, poll it, then POST /nlp/index/push, then poll that.
Nothing enforced the order except an error string telling you which one to run
first, and a caller that stopped halfway left a project chunked but unindexed:
the state where a notebook looks ready and retrieves nothing.

A chain makes that one submission. Indexing runs only if processing succeeded,
and a failure in the first stops the second rather than indexing a half-written
project.
"""

from celery import chain

from enums import CeleryTaskFunction
from utils import get_logger

from .index import index_project_task
from .process import process_data_task

logger = get_logger(__name__)


def ingestion_chain(
    project_id: str,
    request_data: dict,
    asset_id: str | None = None,
    batch_size: int | None = None,
):
    """process → index for one project, as a single chain.

    ``.si`` (immutable), not ``.s``, for two independent reasons — both of
    which are silent corruption rather than an error if got wrong:

    1. A mutable signature prepends the parent's return value to the child's
       arguments, so ``index_project_task`` would receive process's result
       dict as its ``project_id`` and go looking for a project named after a
       dictionary.

    2. ``reset`` means different things on the two sides. On process it
       deletes the project's chunks; on index it drops the entire vector
       collection, but only when asset_id is None. Letting index inherit
       process's flag would turn "re-ingest this document" into "throw away
       every vector in the notebook".

    So indexing takes its arguments from the caller, and its ``reset`` stays
    False: the asset-scoped path already clears that asset's own points
    before re-adding them.
    """
    return chain(
        process_data_task.si(project_id, request_data),
        index_project_task.si(project_id, asset_id, False, batch_size),
    )


def chain_task_names() -> tuple[str, str]:
    """The two task names a chain publishes, in order.

    Used when recording the chain so both members get a row: a chain's
    AsyncResult names only its *last* task, so the ids of everything earlier
    would otherwise be unqueryable.
    """
    from celery_app import SETTINGS

    return (
        f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PROCESS.value}",
        f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}",
    )
