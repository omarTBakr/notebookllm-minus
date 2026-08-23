from time import perf_counter

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import NLPController
from enums import EmbeddingInputType, ProcessStatus
from exceptions import ProjectNotFoundError
from models import ChunkModel, ProjectModel
from utils import get_logger, get_settings

from .schemas import PushRequest, SearchRequest

logger = get_logger(__name__)

nlp_router = APIRouter(prefix="/nlp", tags=["nlp"])


def _controller(request: Request) -> NLPController:
    """Build the controller from the clients the lifespan already opened."""
    return NLPController(
        embedding_client=request.app.embedding_client,
        vectordb_client=request.app.db.vectors(),
    )


@nlp_router.post("/index/push/{project_id}")
async def push_index(project_id: str, request: PushRequest, http_request: Request):
    """Embed a project's chunks and push them into the vector store.

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

    project_model = ProjectModel(http_request.app.db)
    chunk_model = ChunkModel(http_request.app.db)

    # get_project raises ProjectNotFoundError (404) itself. Unlike /process,
    # this route must not upsert: indexing a project that was never ingested
    # would create an empty collection and report success.
    project = await project_model.get_project(project_id)

    # Nothing to index is not a success — the same call one step earlier in the
    # pipeline (/process) treats an empty project the same way. Counted over
    # the same scope the push will walk, so asking for an asset that was never
    # processed is a 404 rather than a 200 reporting zero work done.
    chunks_found = await chunk_model.count_project_chunks(project.id, request.asset_id)
    if not chunks_found:
        scope = (
            f"asset {request.asset_id!r} of project {project_id!r}"
            if request.asset_id
            else f"Project {project_id!r}"
        )
        raise ProjectNotFoundError(
            f"{scope} has no chunks to index — run /process/{project_id} first"
        )

    controller = _controller(http_request)
    result = await controller.index_chunks(
        chunk_model=chunk_model,
        project_object_id=project.id,
        project_id=project_id,
        asset_id=request.asset_id,
        reset=request.reset,
        batch_size=request.batch_size,
    )

    return JSONResponse(
        status_code=200,
        content={
            "project_id": project_id,
            "project_db_id": str(project.id),
            "collection": result["collection"],
            "asset_id": request.asset_id,
            "reset": request.reset,
            "status": ProcessStatus.PROCESSING_SUCCESS.value,
            # chunks_found counts the whole project; chunks_indexed counts what
            # this call actually wrote, which is narrower when asset_id is set.
            "chunks_found": chunks_found,
            "chunks_indexed": result["chunks_indexed"],
            # Points removed before re-adding, when scoped to one asset.
            "points_cleared": result["points_cleared"],
            "batches": result["batches"],
            "embedding_model": http_request.app.embedding_client.model_id,
            "vector_size": result["vector_size"],
        },
    )


@nlp_router.get("/index/info/{project_id}")
async def index_info(project_id: str, http_request: Request):
    """What the vector store currently holds for this project."""
    logger.debug("Index info requested for project %r", project_id)

    project_model = ProjectModel(http_request.app.db)
    project = await project_model.get_project(project_id)

    chunk_model = ChunkModel(http_request.app.db)
    controller = _controller(http_request)

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
    logger.debug(
        "Index search requested for project %r (limit=%d)", project_id, request.limit
    )

    project_model = ProjectModel(http_request.app.db)
    await project_model.get_project(project_id)

    controller = _controller(http_request)
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
        checks["db"] = {"status": "error", "backend": settings.DOCUMENT_DB_BACKEND, "error": str(exc)}

    # --- Embedding model ---
    # A real inference call, not a config echo: this is what catches "ollama
    # serve isn't running" and "the model was never pulled".
    started = perf_counter()
    try:
        vectors = await http_request.app.embedding_client.embed(
            ["ping"], EmbeddingInputType.QUERY
        )
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
