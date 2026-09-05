"""Celery canvas: the ingestion pipeline as one queued unit.

Processing and indexing were two independent tasks that the *client* had to
sequence — POST /process, poll it, then POST /nlp/index/push, then poll that.
Nothing enforced the order except an error string telling you which one to run
first, and a caller that stopped halfway left a project chunked but unindexed:
the state where a notebook looks ready and retrieves nothing.

A chain makes that one submission. Indexing runs only if processing succeeded,
and a failure in the first stops the second rather than indexing a half-written
project.

Three links now, not two: building the ANN index is its own task rather than
the tail of the embedding one. It is pure database work, it takes seconds where
embedding takes minutes, and as part of index_project_task a build that failed
was reported as an indexing failure with every vector already written. Split
out, it is a bar in Flower and a row in task_executions of its own.
"""

from celery import chain

from enums import CeleryTaskFunction
from utils import get_logger

from .index import build_vector_index_task, index_project_task
from .process import process_data_task

logger = get_logger(__name__)


def _index_signatures(project_id: str, asset_id: str | None, reset: bool, batch_size: int | None):
    """The indexing half of the pipeline: embed the chunks, then index them.

    One definition, used by both chains below, so the two cannot fall out of
    step over what follows indexing or with which arguments.

    ``.si`` (immutable), not ``.s``, for two independent reasons — both of
    which are silent corruption rather than an error if got wrong:

    1. A mutable signature prepends the parent's return value to the child's
       arguments, so ``index_project_task`` would receive process's result
       dict as its ``project_id`` and go looking for a project named after a
       dictionary — and ``build_vector_index_task`` would get index's.

    2. ``reset`` means different things on the two sides. On process it
       deletes the project's chunks; on index it drops the entire vector
       collection, but only when asset_id is None. Letting index inherit
       process's flag would turn "re-ingest this document" into "throw away
       every vector in the notebook".
    """
    return [
        index_project_task.si(project_id, asset_id, reset, batch_size),
        # project_id only: the index covers the whole collection whichever
        # asset triggered the run, and it is never reset here — create_index
        # is IF NOT EXISTS, so re-ingesting one document does not rebuild an
        # index that already covers it.
        build_vector_index_task.si(project_id),
    ]


def ingestion_chain(
    project_id: str,
    request_data: dict,
    asset_id: str | None = None,
    batch_size: int | None = None,
):
    """process → index → build index for one project, as a single chain.

    Indexing takes its arguments from the caller rather than from process, and
    its ``reset`` stays False: the asset-scoped path already clears that
    asset's own points before re-adding them. See _index_signatures for why
    every link is immutable.
    """
    return chain(
        process_data_task.si(project_id, request_data),
        *_index_signatures(project_id, asset_id, False, batch_size),
    )


def index_chain(
    project_id: str,
    asset_id: str | None = None,
    reset: bool = False,
    batch_size: int | None = None,
):
    """index → build index, for a project whose chunks already exist.

    /nlp/index/push queued ``index_project_task`` bare. Once the index build
    stopped being part of that task, a bare call left the collection
    permanently unindexed — so this path gets a chain too, rather than the
    route remembering to queue a second task by hand.
    """
    return chain(*_index_signatures(project_id, asset_id, reset, batch_size))


def chain_task_names() -> tuple[str, str, str]:
    """The three task names an ingestion chain publishes, in order.

    Used when recording the chain so every member gets a row: a chain's
    AsyncResult names only its *last* task, so the ids of everything earlier
    would otherwise be unqueryable and report UNKNOWN for the whole of a run.
    """
    from celery_app import SETTINGS

    return (
        f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PROCESS.value}",
        f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}",
        f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.BUILD_INDEX.value}",
    )


def index_chain_task_names() -> tuple[str, str]:
    """The same, for index_chain: the ingestion names without the process one.

    Derived rather than written out, so a rename or a new link cannot leave the
    two lists disagreeing about what a chain publishes.
    """
    return chain_task_names()[1:]


def chain_results(result) -> list:
    """Every AsyncResult in a chain, oldest first.

    ``apply_async`` hands back the *last* task's result and reaches the ones
    before it through ``.parent``. Walking it rather than naming ``.parent``
    positionally is what lets a link be added to a chain without every caller's
    bookkeeping having to be rewritten around the new depth.
    """
    results = []
    node = result

    while node is not None:
        results.append(node)
        node = getattr(node, "parent", None)

    results.reverse()

    return results
