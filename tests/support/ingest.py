"""Runs what an upload queued, in-process, against the fakes.

Attaching a document used to do everything inside the request, so a test could
POST and immediately assert that chunks and vectors existed. It now queues a
chain, and the work happens in a Celery worker that no test runs.

The assertions those tests make are still the right ones — "after uploading,
the document is chunked and indexed" is the behaviour users depend on — so
rather than weakening them to "a task was queued", this executes the queued
work. It reads the task rows the route actually wrote, so the arguments under
test are the ones the route really published, not a second copy maintained
here that could drift from it.
"""

from application.services import NLPService
from application.tasks.jobs.ingest.assemble import assemble_chunks
from application.tasks.jobs.ingest.parse import parse_batch
from application.tasks.jobs.ingest.postprocess import _correct
from application.tasks.jobs.ingest.process import plan_ingestion
from data.models import ChatModel, ChunkModel, ProjectModel
from shared.enums import IN_FLIGHT, TaskExecutionStatus
from shared.exceptions import ChatNotFoundError


async def _controller_for(app, project_id):
    """The controller the worker would build: the chat's own embedding model.

    A chat may name a model other than the .env default, and its vectors were
    written at that model's width — the same reason routes/nlp.py builds its
    controller per project rather than from app.embedding_client.
    """
    try:
        chat = await ChatModel(app.db).get_chat(project_id)
    except ChatNotFoundError:
        # /process and /data create projects that never had a chat.
        chat = None

    return NLPService(
        embedding_client=app.providers.embedding(
            getattr(chat, "embedding_model", None),
            getattr(chat, "embedding_dimensions", None),
        ),
        vectordb_client=app.db.vectors(),
    )


async def drain_ingestion(app):
    """Run every queued ingestion task to completion.

    Repeats until nothing is left in flight, because a task can queue the next:
    a fetched link queues its ingestion, which queues the index.
    """
    for _ in range(6):
        if not await _drain_once(app):
            return

    raise AssertionError("ingestion kept queueing more work; a task is re-queueing itself")


async def _drain_once(app) -> bool:
    """One pass over what is queued now. True if there was anything to run."""

    db = app.db
    tasks = db.tasks()

    pending = [t for t in list(tasks.items.values()) if t.status in IN_FLIGHT]

    # In chain order, and the order matters twice over: indexing a project
    # whose chunks do not exist yet raises rather than silently indexing
    # nothing, and building the index before the vectors are in would index an
    # empty collection.
    order = {
        "process_data_task": 0,
        "fetch_url_task": 0,
        "index_project_task": 1,
        "build_vector_index_task": 2,
    }

    for task in sorted(pending, key=lambda t: order.get(t.task_name.rsplit(".", 1)[-1], 9)):
        args = task.args
        project_id = args["project_id"]

        if task.task_name.endswith("build_vector_index_task"):
            controller = await _controller_for(app, project_id)
            await controller.build_index(project_id)

        elif task.task_name.endswith("fetch_url_task"):
            from application.tasks.jobs.ingest.fetch import _fetch_and_attach

            await _fetch_and_attach(project_id, args["url"], db, task.task_id)

        elif task.task_name.endswith("process_data_task"):
            await _drain_ingest_fanout(db, project_id, args)

        else:
            project = await ProjectModel(db).get_project(project_id)

            controller = await _controller_for(app, project_id)
            await controller.index_chunks(
                chunk_model=ChunkModel(db),
                project_object_id=project.id,
                project_id=project_id,
                asset_id=args.get("asset_id"),
            )

        await tasks.update_status(task.task_id, TaskExecutionStatus.SUCCESS.value)

    return bool(pending)


async def _drain_ingest_fanout(db, project_id, args):
    """The four stages, in the order the queues would run them.

    Plan, parse each batch, correct it, then collect -- the same function
    bodies the four tasks call. Driving the real bodies rather than a
    simplified stand-in is the point: the batching, the page ordering, the
    completion claim and the highlight rebasing are exactly the parts a
    stand-in would quietly get right where production does not.
    """
    from data.models import IngestBatchModel
    from data.models.ingest import IngestBatchStatus
    from shared.utils import get_settings

    plan = await plan_ingestion(project_id, args, db)

    if not plan["batches"]:
        return

    batches = IngestBatchModel(db)
    settings = get_settings()

    for batch in plan["batches"]:
        await parse_batch(project_id, batch["asset_id"], batch["start"], batch["end"], batch["batch_index"], db)

        stored = await batches.find_batch(batch["asset_id"], batch["batch_index"])
        pages = stored.payload.get("pages", [])

        # Correction is off under test (POSTPROCESS_ENABLED=false in
        # conftest's env), so this returns the pages untouched -- but it is
        # called anyway, so the stage cannot break unnoticed.
        await _correct(pages, settings)

        stored.payload["pages"] = pages
        stored.status = IngestBatchStatus.CORRECTED
        await batches.save_batch(stored)

    # The claim, exactly as the last real batch would make it.
    for asset_id in {batch["asset_id"] for batch in plan["batches"]}:
        if await batches.claim_collection(asset_id):
            await assemble_chunks(asset_id, db)
