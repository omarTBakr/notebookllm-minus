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

from types import SimpleNamespace

from controllers import NLPController
from enums import IN_FLIGHT, TaskExecutionStatus
from exceptions import ChatNotFoundError
from models import ChatModel, ChunkModel, ProjectModel
from tasks.process_service import process_data


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

    return NLPController(
        embedding_client=app.providers.embedding(
            getattr(chat, "embedding_model", None),
            getattr(chat, "embedding_dimensions", None),
        ),
        vectordb_client=app.db.vectors(),
    )


async def drain_ingestion(app):
    """Run every queued ingestion task to completion."""
    db = app.db
    tasks = db.tasks()

    pending = [t for t in list(tasks.items.values()) if t.status in IN_FLIGHT]

    # In chain order, and the order matters twice over: indexing a project
    # whose chunks do not exist yet raises rather than silently indexing
    # nothing, and building the index before the vectors are in would index an
    # empty collection.
    order = {
        "process_data_task": 0,
        "index_project_task": 1,
        "build_vector_index_task": 2,
    }

    for task in sorted(pending, key=lambda t: order.get(t.task_name.rsplit(".", 1)[-1], 9)):
        args = task.args
        project_id = args["project_id"]

        if task.task_name.endswith("build_vector_index_task"):
            controller = await _controller_for(app, project_id)
            await controller.build_index(project_id)

        elif task.task_name.endswith("process_data_task"):
            request = SimpleNamespace(
                asset_id=args.get("asset_id"),
                chunk_size=args.get("chunk_size") or 1000,
                overlap_size=args.get("overlap_size") if args.get("overlap_size") is not None else 200,
                reset=args.get("reset", False),
            )
            await process_data(project_id, request, db)

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
