"""The shape of the ingestion pipeline, and where its stages are published to.

Ingestion is no longer a Celery chain. The planner publishes its page batches
and returns; the last batch to finish claims the collection and the collector
publishes the indexing stage. What survives from the chain era is the *naming*:
three stages, in order, each with a task_executions row written before any of
them runs.

`index_chain` is still a real chain -- /nlp/index/push indexes chunks that
already exist -- so its link-shape tests are unchanged.
"""

import re
from pathlib import Path

import pytest

from application.tasks.workflows import (
    QueuedChain,
    chain_results,
    chain_task_names,
    index_chain,
    index_chain_task_names,
    ingestion_signature,
)
from shared.enums import CeleryTaskFunction

COMPOSE = Path(__file__).resolve().parents[2] / "Docker" / "docker-compose.yml"


def _links(canvas):
    """(task name, args, immutable) per link, in publication order."""
    return [(sig["task"], tuple(sig.args), bool(sig.immutable)) for sig in canvas.tasks]


# --- the three ingestion stages, and the ids they are published under ---------


def test_the_three_stage_names_are_in_publication_order():
    """These are what each stage's task_executions row is filed under, and the
    order the routes zip them against QueuedChain.ids in."""
    assert chain_task_names() == (
        "notebookllm.process_data_task",
        "notebookllm.index_project_task",
        "notebookllm.build_vector_index_task",
    )


def test_every_stage_gets_its_own_id():
    """Three rows, three ids. Reusing one would make two stages share a row and
    the second overwrite the first's status."""
    queued = QueuedChain()

    assert len(set(queued.ids)) == 3
    assert queued.ids == (queued.process_id, queued.index_id, queued.build_id)


def test_the_planner_is_published_under_the_id_the_route_recorded():
    """The browser polls the planner's id from the moment the upload returns.
    Publishing under a different one leaves that row QUEUED for ever while the
    work runs somewhere nobody is looking -- which is exactly what the chord
    did."""
    queued = QueuedChain()
    signature = ingestion_signature("p1", {"reset": False}, queued)

    assert signature.options["task_id"] == queued.process_id
    assert signature["task"] == "notebookllm.process_data_task"


def test_the_downstream_ids_ride_with_the_planner():
    """The collector runs minutes later in another process and publishes index
    and build itself. It reads their ids off the run row, which the planner
    writes from these -- so they have to reach it."""
    queued = QueuedChain()
    _, request_data = ingestion_signature("p1", {"reset": True}, queued).args

    assert request_data["parent_task_id"] == queued.process_id
    assert request_data["index_task_id"] == queued.index_id
    assert request_data["build_task_id"] == queued.build_id
    assert request_data["reset"] is True, "the caller's own arguments must survive"


def test_the_index_chain_is_the_ingestion_stages_without_the_processing():
    """/nlp/index/push indexes chunks that already exist, so it skips process
    — but it must not skip the build, which is what a bare .delay() did."""
    assert [name for name, _, _ in _links(index_chain("p1"))] == list(chain_task_names())[1:]


def test_the_index_chain_passes_its_own_reset_through():
    """Unlike the ingestion chain, this caller's reset *is* the index's reset:
    POST /nlp/index/push?reset=true means drop the collection."""
    index, build = _links(index_chain("p1", "a1", True, 16))

    assert index == ("notebookllm.index_project_task", ("p1", "a1", True, 16), True)
    assert build == ("notebookllm.build_vector_index_task", ("p1",), True)


# --- the names used to write one row per link --------------------------------


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


@pytest.mark.parametrize("task_name", chain_task_names())
def test_every_task_in_the_chain_is_routed_to_a_queue_a_worker_consumes(task_name):
    """A task published to a queue with no consumer sits QUEUED forever and
    says nothing — no error, no dead letter, no timeout. build_vector_index_task
    therefore shares the *index* queue rather than getting one of its own,
    because the worker's -Q list lives in a file this one cannot change."""
    import application.tasks  # noqa: F401 — registers the tasks on the app
    from celery_app import SETTINGS, celery_app

    queue = celery_app.conf.task_routes[task_name]["queue"]
    suffix = queue.removeprefix(f"{SETTINGS.CELERY_PROJECT_NAME}.")

    assert suffix in _compose_queues(), f"{task_name} routes to {queue!r}, which no compose worker subscribes to"


def test_the_index_build_shares_the_index_queue():
    """Stated explicitly because it is the one place a task name and its queue
    name deliberately differ, and a later "tidy-up" that gives it its own queue
    would break the rule above without touching this file."""
    import application.tasks  # noqa: F401
    from celery_app import SETTINGS, celery_app

    routes = celery_app.conf.task_routes

    assert (
        routes[f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.BUILD_INDEX.value}"]["queue"]
        == routes[f"{SETTINGS.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}"]["queue"]
        == SETTINGS.CELERY_QUEUE_INDEX
    )


def test_every_declared_queue_has_a_worker():
    """The general form of the rule above, for queues rather than tasks: a
    queue declared in celery_queues but consumed by no compose worker is dead
    config at best, and a silent black hole the day something routes to it.
    The default queue is the exception -- every task is routed explicitly, so
    nothing is ever published to it."""
    from celery_app import SETTINGS, celery_app

    prefix = f"{SETTINGS.CELERY_PROJECT_NAME}."
    declared = {
        queue.name.removeprefix(prefix) for queue in celery_app.conf.task_queues if queue.name != SETTINGS.CELERY_TASK_DEFAULT_QUEUE
    }

    assert declared, "no queues declared, so this test checks nothing"
    assert declared <= _compose_queues(), f"declared with no worker: {sorted(declared - _compose_queues())}"


def test_every_job_module_is_registered():
    """A job module missing from celery_app's `include` never registers its
    tasks. The worker then rejects them as unknown, or — worse — the queue they
    were routed to simply accepts messages nothing consumes, with no error
    anywhere. Found by reading the source rather than by importing: a module
    that is *not* registered is exactly the one nothing has imported."""
    from celery_app import celery_app

    jobs = Path(__file__).resolve().parents[2] / "src" / "application" / "tasks" / "jobs"
    registered = set(celery_app.conf.include)

    defines_a_task = {
        f"application.tasks.jobs.{path.relative_to(jobs).with_suffix('').as_posix().replace('/', '.')}"
        for path in jobs.rglob("*.py")
        # A package __init__ re-exports tasks but defines none; the decorator
        # appears there only in prose.
        if path.name != "__init__.py" and "@celery_app.task" in path.read_text()
    }

    assert defines_a_task, "no job modules found, so this test checks nothing"
    assert defines_a_task <= registered, f"not in celery_app include: {sorted(defines_a_task - registered)}"
