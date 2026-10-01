from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from application.tasks.jobs.index import get_index_task
from application.tasks.tracking.status import mark_queued
from application.tasks.workflows import (
    chain_results,
    index_chain,
    index_chain_task_names,
)
from presentation import dependencies as deps
from shared.exceptions import (
    CELERY_BROKER_EXCEPTIONS,
    CeleryBrokerError,
)
from shared.utils import get_logger, get_settings

from .schemas import PushRequest, SearchRequest

logger = get_logger(__name__)

nlp_router = APIRouter(prefix="/nlp", tags=["nlp"])


@nlp_router.post("/index/push/{project_id}", status_code=202)
async def push_index(project_id: str, request: PushRequest, http_request: Request):
    """Queue embedding and vector writes on the dedicated index worker.

    Idempotent: re-running without ``reset`` overwrites the same points rather
    than duplicating them, because each one is keyed on its chunk's Mongo _id.

    Embedding and vector-store errors propagate to the handler in main.py.
    """
    logger.debug(
        "Index push requested for project %r: asset_id=%r reset=%s batch_size=%s",
        project_id,
        request.asset_id,
        request.reset,
        request.batch_size,
    )

    await deps.index_service(http_request).require_chunks(project_id, request.asset_id)

    idempotency = deps.idempotency(http_request)

    args = {
        "project_id": project_id,
        "asset_id": request.asset_id,
        "reset": request.reset,
        "batch_size": request.batch_size,
    }
    task_names = index_chain_task_names()
    index_name, build_name = task_names

    # Claimed on the index task alone: it is the expensive half, it is what a
    # duplicate submission would pay for twice, and the build that follows it
    # is not separately submittable.
    existing = await idempotency.claim(index_name, args)

    if existing is not None:
        # Already running with these exact arguments. Indexing the same
        # chunks twice concurrently is wasted embedding spend, not a
        # correctness problem, so this joins the run in progress.
        return JSONResponse(
            status_code=200,
            content={
                "task_id": existing.task_id,
                "project_id": project_id,
                "status": existing.status.value,
                "queued": False,
                "queue": get_settings().CELERY_QUEUE_INDEX,
            },
        )

    try:
        # A chain, not a bare .delay(): building the ANN index is its own task
        # now, and this route is the one path that never went through
        # ingestion_chain. Queued bare it would embed every chunk and leave the
        # collection permanently unindexed — a search that still answers, from
        # an exact scan, so nothing would report it as broken.
        result = index_chain(
            project_id,
            request.asset_id,
            request.reset,
            request.batch_size,
        ).apply_async()
    except CELERY_BROKER_EXCEPTIONS as exc:
        raise CeleryBrokerError("Could not queue vector indexing") from exc

    ids = dict(zip(task_names, (r.id for r in chain_results(result))))

    for name, task_id in ids.items():
        mark_queued(task_id)
        await idempotency.record(
            task_id=task_id,
            task_name=name,
            project_id=project_id,
            args=args,
            asset_id=request.asset_id or "",
        )

    return JSONResponse(
        status_code=202,
        content={
            # Still the index task's id: it is what callers poll, and it is
            # the half that takes the time.
            "task_id": ids.get(index_name) or result.id,
            "build_index_task_id": ids.get(build_name),
            "project_id": project_id,
            "status": "queued",
            "queued": True,
            "queue": get_settings().CELERY_QUEUE_INDEX,
        },
    )


@nlp_router.get("/index/tasks/{task_id}")
async def index_task_status(task_id: str):
    """Read the state and result of a queued indexing task."""
    return get_index_task(task_id)


@nlp_router.get("/index/info/{project_id}")
async def index_info(project_id: str, http_request: Request):
    """What the vector store currently holds for this project."""
    logger.debug("Index info requested for project %r", project_id)

    controller = await deps.nlp_service_for_project(http_request, project_id)

    body = await deps.index_service(http_request).info(
        project_id, controller, deps.default_embedding_client(http_request).model_id
    )

    # 200 even when it isn't indexed: "is this indexed?" is the question being
    # asked, and no is a valid answer, not a missing resource.
    return JSONResponse(status_code=200, content=body)


@nlp_router.post("/index/search/{project_id}")
async def search_index(project_id: str, request: SearchRequest, http_request: Request):
    """Semantic search over a project's indexed chunks."""
    logger.debug("Index search requested for project %r (limit=%d)", project_id, request.limit)

    await deps.index_service(http_request).require_project(project_id)

    controller = await deps.nlp_service_for_project(http_request, project_id)
    hits = await controller.search(project_id, request.text, request.limit)

    return JSONResponse(
        status_code=200,
        content={
            "project_id": project_id,
            "collection": controller.collection_name(project_id),
            "query": request.text,
            "limit": request.limit,
            "hits_found": len(hits),
            "results": hits,
        },
    )


@nlp_router.get("/health")
async def nlp_health(http_request: Request):
    """Live readiness probe for the three backends this pipeline depends on.

    The one place in the project that catches exceptions instead of letting them
    reach the handler in main.py — here, *reporting* a backend's failure is the
    endpoint's entire job, so a dead dependency must produce a body describing
    it rather than a 500 from the shared handler.
    """
    settings = get_settings()
    checks = await deps.index_service(http_request).health(deps.default_embedding_client(http_request))

    healthy = all(check["status"] == "ok" for check in checks.values())

    if not healthy:
        failed = [name for name, check in checks.items() if check["status"] != "ok"]
        logger.warning("Health check failed for: %s", ", ".join(failed))

    # 503 on failure so this works as a container/uptime probe, not just as
    # something to read by eye.
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "application": settings.APPLICATION_NAME,
            "version": settings.APP_VERSION,
            "generation_model": deps.default_generation_model_id(http_request),
            "checks": checks,
        },
    )
