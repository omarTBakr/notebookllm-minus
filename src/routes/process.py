from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import IdempotencyController
from exceptions import CELERY_BROKER_EXCEPTIONS, CeleryBrokerError
from tasks.process import get_process_task
from tasks.status import mark_queued
from tasks.workflows import chain_results, chain_task_names, ingestion_chain

from .schemas import ProcessRequest

process_router = APIRouter(prefix="/process", tags=["process"])


@process_router.post("/{project_id}", status_code=202)
async def process_data(project_id: str, request: ProcessRequest, http_request: Request):
    """Queue document ingestion, then indexing, as one chain.

    Returns 202 when work was queued and **200 when an identical submission is
    already running** — a repeat of in-flight work is not an error, and the
    caller gets the id of the run that is actually doing it rather than a
    second one racing it.
    """
    idempotency = IdempotencyController(http_request.app.db)

    args = request.model_dump()
    task_names = chain_task_names()
    process_name, index_name, build_name = task_names

    existing = await idempotency.claim(process_name, {"project_id": project_id, **args})

    if existing is not None:
        return JSONResponse(
            status_code=200,
            content={
                "task_id": existing.task_id,
                "project_id": project_id,
                "status": existing.status.value,
                "queued": False,
            },
        )

    try:
        result = ingestion_chain(project_id, args, asset_id=request.asset_id, batch_size=None).apply_async()
    except CELERY_BROKER_EXCEPTIONS as exc:
        raise CeleryBrokerError("Could not queue document processing") from exc

    # A chain's AsyncResult names only its *last* task and reaches the earlier
    # ones through .parent. Every link gets a row, or the halves before the
    # last would be unqueryable and report UNKNOWN for the whole of their run.
    ids = dict(zip(task_names, (r.id for r in chain_results(result))))

    for name, task_id in ids.items():
        mark_queued(task_id)
        await idempotency.record(
            task_id=task_id,
            task_name=name,
            project_id=project_id,
            args={"project_id": project_id, **args},
            asset_id=request.asset_id or "",
        )

    return JSONResponse(
        status_code=202,
        content={
            # task_id stays the *process* id: it is what a client polls to
            # watch an upload, and renaming it to the chain's tail would break
            # every existing poller.
            "task_id": ids.get(process_name) or result.id,
            "index_task_id": ids.get(index_name),
            "build_index_task_id": ids.get(build_name),
            "project_id": project_id,
            "status": "queued",
            "queued": True,
        },
    )


@process_router.get("/tasks/{task_id}")
async def process_task_status(task_id: str):
    """Read the state and result of a queued processing task."""
    return get_process_task(task_id)
