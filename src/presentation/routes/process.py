from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from application.tasks.jobs.ingest.process import get_process_task
from application.tasks.tracking.status import mark_queued
from application.tasks.workflows import (
    QueuedChain,
    chain_task_names,
    ingestion_signature,
)
from presentation import dependencies as deps
from shared.exceptions import CELERY_BROKER_EXCEPTIONS, CeleryBrokerError

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
    idempotency = deps.idempotency(http_request)

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

    queued = QueuedChain()

    try:
        ingestion_signature(project_id, args, queued).apply_async()
    except CELERY_BROKER_EXCEPTIONS as exc:
        raise CeleryBrokerError("Could not queue document processing") from exc

    # One row per stage, written before any of them runs. Ingestion is not a
    # chain any more, so the ids are generated rather than read back off one.
    ids = dict(zip(task_names, queued.ids))

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
            # watch an upload, and naming any other stage here would break
            # every existing poller. `or` is gone with the chain it guarded --
            # the ids are generated up front now, so this one always exists.
            "task_id": ids[process_name],
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
