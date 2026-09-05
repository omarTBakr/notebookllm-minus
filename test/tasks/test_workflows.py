"""The shape of the ingestion chain, and where its links are published to.

Building the ANN index used to be the tail of index_project_task. It is now a
link of its own, which buys visibility — a bar in Flower, a row in
task_executions — at the cost of three ways to get it silently wrong, one test
section each:

* a mutable signature, which corrupts arguments rather than raising,
* a caller that queues indexing without the build, leaving a collection that
  searches correctly and slowly forever,
* a queue no worker consumes, which leaves the task QUEUED with no error.
"""

import re
from pathlib import Path

import pytest

from enums import CeleryTaskFunction
from tasks.workflows import (
    chain_results,
    chain_task_names,
    index_chain,
    index_chain_task_names,
    ingestion_chain,
)

COMPOSE = Path(__file__).resolve().parents[2] / "Docker" / "docker-compose.yml"


def _links(canvas):
    """(task name, args, immutable) per link, in publication order."""
    return [(sig["task"], tuple(sig.args), bool(sig.immutable)) for sig in canvas.tasks]


# --- the chain builds the index, as its own link ------------------------------


def test_ingestion_ends_by_building_the_index():
    """Three links, in order. index_chunks no longer builds the index itself,
    so a chain that stopped at indexing would embed every chunk and leave the
    collection on an exact scan."""
    names = [name for name, _, _ in _links(ingestion_chain("p1", {"reset": False}))]

    assert names == [
        "notebookllm.process_data_task",
        "notebookllm.index_project_task",
        "notebookllm.build_vector_index_task",
    ]


def test_the_index_build_is_given_only_the_project():
    """The index covers the whole collection whichever asset triggered the run,
    so passing asset_id would suggest a scope the build does not have."""
    _, _, build = _links(ingestion_chain("p1", {}, asset_id="a1", batch_size=64))

    assert build == ("notebookllm.build_vector_index_task", ("p1",), True)


def test_every_link_is_immutable():
    """`.s` instead of `.si` prepends the parent's return value to the child's
    arguments — index_project_task would receive process's *result dict* as its
    project_id. Silent corruption, not an error, on every ingest."""
    for name, _, immutable in _links(ingestion_chain("p1", {}, asset_id="a1")):
        assert immutable, f"{name} would inherit its parent's return value"


def test_indexing_does_not_inherit_process_reset():
    """reset means different things on the two sides: chunks on process, the
    *whole vector collection* on index. Inheriting it would turn "re-ingest
    this document" into "throw away every vector in the notebook"."""
    _, index, _ = _links(ingestion_chain("p1", {"reset": True}, asset_id="a1", batch_size=32))

    assert index == ("notebookllm.index_project_task", ("p1", "a1", False, 32), True)


def test_the_index_chain_is_the_ingestion_chain_without_the_processing():
    """/nlp/index/push indexes chunks that already exist, so it skips process
    — but it must not skip the build, which is what a bare .delay() did."""
    assert [name for name, _, _ in _links(index_chain("p1"))] == [
        name for name, _, _ in _links(ingestion_chain("p1", {}))
    ][1:]


def test_the_index_chain_passes_its_own_reset_through():
    """Unlike the ingestion chain, this caller's reset *is* the index's reset:
    POST /nlp/index/push?reset=true means drop the collection."""
    index, build = _links(index_chain("p1", "a1", True, 16))

    assert index == ("notebookllm.index_project_task", ("p1", "a1", True, 16), True)
    assert build == ("notebookllm.build_vector_index_task", ("p1",), True)


# --- the names used to write one row per link --------------------------------


def test_the_recorded_names_match_the_tasks_the_chain_publishes():
    """These names are what each link's task_executions row is filed under. A
    name that does not match the task actually published leaves the row
    orphaned and the status poll answering UNKNOWN."""
    assert chain_task_names() == tuple(name for name, _, _ in _links(ingestion_chain("p1", {})))


def test_the_index_chain_names_are_the_tail_of_the_ingestion_names():
    assert index_chain_task_names() == chain_task_names()[1:]
    assert index_chain_task_names() == tuple(name for name, _, _ in _links(index_chain("p1")))


def test_the_build_task_has_an_enum_member():
    """Task and queue identifiers are built from this enum, so a literal string
    anywhere would be a typo waiting to route into nothing."""
    assert CeleryTaskFunction.BUILD_INDEX.value == "build_vector_index_task"


# --- walking a chain's results -----------------------------------------------


def test_chain_results_returns_the_links_oldest_first():
    """apply_async hands back the *last* task and reaches the earlier ones
    through .parent, so a route that recorded only what it was handed left
    every earlier link unqueryable."""
    from types import SimpleNamespace as NS

    last = NS(id="c", parent=NS(id="b", parent=NS(id="a", parent=None)))

    assert [r.id for r in chain_results(last)] == ["a", "b", "c"]


def test_chain_results_handles_a_task_that_is_not_in_a_chain():
    from types import SimpleNamespace as NS

    assert [r.id for r in chain_results(NS(id="solo", parent=None))] == ["solo"]


# --- a queue nobody consumes is a task that never runs ------------------------


def _compose_queues() -> set[str]:
    """The queue suffixes the compose workers actually subscribe to.

    Read from the file rather than restated here: the -Q lists live only in
    docker-compose.yml, and a queue name agreed on in Python and absent there
    is exactly the failure this guards.
    """
    queues = set()

    for line in COMPOSE.read_text().splitlines():
        if not line.strip().startswith("command:"):
            continue
        for match in re.findall(r'-Q \\"([^\\]+)\\"', line):
            for name in match.split(","):
                queues.add(name.strip().replace("$${CELERY_PROJECT_NAME}.", ""))

    return queues


def test_the_compose_queue_list_was_actually_found():
    """Guards the test below against passing because the parser matched
    nothing — which would make an unconsumed queue invisible."""
    assert "process_data_task" in _compose_queues()
    assert "index_project_task" in _compose_queues()


@pytest.mark.parametrize("task_name", [name for name, _, _ in _links(ingestion_chain("p", {}))])
def test_every_task_in_the_chain_is_routed_to_a_queue_a_worker_consumes(task_name):
    """A task published to a queue with no consumer sits QUEUED forever and
    says nothing — no error, no dead letter, no timeout. build_vector_index_task
    therefore shares the *index* queue rather than getting one of its own,
    because the worker's -Q list lives in a file this one cannot change."""
    import tasks  # noqa: F401 — registers the tasks on the app
    from celery_app import SETTINGS, celery_app

    queue = celery_app.conf.task_routes[task_name]["queue"]
    suffix = queue.removeprefix(f"{SETTINGS.CELERY_PROJECT_NAME}.")

    assert suffix in _compose_queues(), (
        f"{task_name} routes to {queue!r}, which no compose worker subscribes to"
    )


def test_the_index_build_shares_the_index_queue():
    """Stated explicitly because it is the one place a task name and its queue
    name deliberately differ, and a later "tidy-up" that gives it its own queue
    would break the rule above without touching this file."""
    import tasks  # noqa: F401
    from celery_app import SETTINGS, celery_app

    routes = celery_app.conf.task_routes

    assert (
        routes[f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.BUILD_INDEX.value}"]["queue"]
        == routes[f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}"]["queue"]
        == SETTINGS.CELERY_QUEUE_INDEX
    )
