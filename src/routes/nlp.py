from time import perf_counter

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import IdempotencyController, NLPController
from enums import EmbeddingInputType
from exceptions import (
    CELERY_BROKER_EXCEPTIONS,
    CeleryBrokerError,
    ChatNotFoundError,
    ProjectNotFoundError,
)
from models import ChatModel, ChunkModel, ProjectModel
from tasks.index import get_index_task
from tasks.status import mark_queued
from tasks.workflows import chain_results, index_chain, index_chain_task_names
from utils import get_logger, get_settings

from .schemas import PushRequest, SearchRequest

logger = get_logger(__name__)

nlp_router = APIRouter(prefix="/nlp", tags=["nlp"])


async def _controller(request: Request, project_id: str | None = None) -> NLPController:
    """Build the controller, using the *project's own* embedding model.

    A chat_id is a project_id in this application, and a chat may name an
    embedding model different from the one in .env — the UI's model picker
    writes it. Its vectors were then written at that model's width.

    Building this from ``app.embedding_client`` regardless, as it used to,
    embedded the query with the default model and searched a collection built
    with another: at best a silent quality loss, at worst
    ``different vector dimensions 4096 and 768`` from pgvector, which is what
    /nlp/index/search returned for every chat using the picker.

    Falls back to the app default when the project is not a chat — /process
    and /data create projects that never had one.
    """
    embedding_client = request.app.embedding_client

    if project_id is not None:
        try:
            chat = await ChatModel(request.app.db).get_chat(project_id)
        except ChatNotFoundError:
            chat = None

        if chat is not None:
            embedding_client = request.app.providers.embedding(chat.embedding_model, chat.embedding_dimensions)

    return NLPController(
        embedding_client=embedding_client,
        vectordb_client=request.app.db.vectors(),
    )


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

    # Validate existence before publishing, but do not hold the request while
    # a provider embeds every chunk.
    project = await ProjectModel(http_request.app.db).get_project(project_id)
    chunks_found = await ChunkModel(http_request.app.db).count_project_chunks(project.id, request.asset_id)
    if not chunks_found:
        scope = (
            f"asset {request.asset_id!r} of project {project_id!r}" if request.asset_id else f"Project {project_id!r}"
        )
        raise ProjectNotFoundError(f"{scope} has no chunks to index - run /process/{project_id} first")

    idempotency = IdempotencyController(http_request.app.db)

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

    project_model = ProjectModel(http_request.app.db)
    project = await project_model.get_project(project_id)

    chunk_model = ChunkModel(http_request.app.db)
    controller = await _controller(http_request, project_id)

    index = await controller.get_index_info(project_id)

    # Chunks in Mongo vs points in Qdrant: the gap between the two is what tells
    # you the index is stale, which is the reason to call this endpoint at all.
    chunks_in_db = await chunk_model.count_project_chunks(project.id)

    body = {
        "project_id": project_id,
        "collection": index["collection"],
        "indexed": index["exists"],
        "chunks_in_db": chunks_in_db,
        "embedding_model": http_request.app.embedding_client.model_id,
    }

    if index["exists"]:
        info = index["info"]
        vectors = (info.get("config", {}).get("params", {}) or {}).get("vectors", {}) or {}
        body.update(
            {
                "points_count": info.get("points_count"),
                "vector_size": vectors.get("size"),
                "distance": vectors.get("distance"),
                "status": info.get("status"),
            }
        )

    # 200 even when it isn't indexed: "is this indexed?" is the question being
    # asked, and no is a valid answer, not a missing resource.
    return JSONResponse(status_code=200, content=body)


@nlp_router.post("/index/search/{project_id}")
async def search_index(project_id: str, request: SearchRequest, http_request: Request):
    """Semantic search over a project's indexed chunks."""
    logger.debug("Index search requested for project %r (limit=%d)", project_id, request.limit)

    project_model = ProjectModel(http_request.app.db)
    await project_model.get_project(project_id)

    controller = await _controller(http_request, project_id)
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
    # get_settings() is lru_cached, so this is a dict lookup, not an .env reparse.
    settings = get_settings()
    checks: dict[str, dict] = {}

    # --- DB ---
    started = perf_counter()
    try:
        # ask the vector layer for a collection list — a cheap round-trip that
        # exercises both the document store and the vector store connections.
        await http_request.app.db.vectors().list_collections()
        checks["db"] = {
            "status": "ok",
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "backend": settings.DOCUMENT_DB_BACKEND,
        }
    except Exception as exc:
        checks["db"] = {
            "status": "error",
            "backend": settings.DOCUMENT_DB_BACKEND,
            "error": str(exc),
        }

    # --- Embedding model ---
    # A real inference call, not a config echo: this is what catches "ollama
    # serve isn't running" and "the model was never pulled".
    started = perf_counter()
    try:
        vectors = await http_request.app.embedding_client.embed(["ping"], EmbeddingInputType.QUERY)
        checks["embedding"] = {
            "status": "ok",
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "provider": settings.EMBEDDING_BACKEND,
            "model": http_request.app.embedding_client.model_id,
            "dimensions": len(vectors[0]),
        }
    except Exception as exc:
        checks["embedding"] = {
            "status": "error",
            "provider": settings.EMBEDDING_BACKEND,
            "model": http_request.app.embedding_client.model_id,
            "error": str(exc),
        }

    # --- Vector store ---
    started = perf_counter()
    try:
        collections = await http_request.app.db.vectors().list_collections()
        checks["vectordb"] = {
            "status": "ok",
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "backend": settings.DOCUMENT_DB_BACKEND,
            "collections": len(collections),
        }
    except Exception as exc:
        checks["vectordb"] = {
            "status": "error",
            "backend": settings.DOCUMENT_DB_BACKEND,
            "error": str(exc),
        }

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
            "generation_model": http_request.app.generation_client.model_id,
            "checks": checks,
        },
    )
