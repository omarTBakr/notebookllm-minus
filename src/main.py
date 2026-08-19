from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient

from exceptions import NotebookLLMError
from factories import LLMChattingFactory, LLMEmbeddingFactory, VectorDBFactory
from middleware import RequestLoggingMiddleware
from models import AssetModel, ChunkModel, ProjectModel
from routes import base_router, data_router, nlp_router, process_router
from utils import get_logger, get_settings, setup_logging

SETTINGS = get_settings()

# Install handlers before anything logs. Runs after uvicorn's own logging setup
# (uvicorn configures logging, then imports this module), so this config wins.
setup_logging(SETTINGS)

logger = get_logger(__name__)


# create a lifespan context manager to load the database on startup and close it on shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: connect to MongoDB
    logger.info("Starting %s v%s", SETTINGS.APPLICATION_NAME, SETTINGS.APP_VERSION)
    app.client = AsyncIOMotorClient(SETTINGS.MONGO_URI)
    app.db = app.client[SETTINGS.MONGO_DB_NAME]

    # motor connects lazily, so ping to surface a bad URI/credentials at startup
    # instead of on the first request that touches the database.
    try:
        await app.client.admin.command("ping")
        logger.info("Connected to MongoDB (database=%s)", SETTINGS.MONGO_DB_NAME)
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc, exc_info=True)

    # Create indexes once at startup — idempotent, safe to re-run.
    project_model = ProjectModel(app.db)
    await project_model.create_index([("project_id", 1)], unique=True)

    chunk_model = ChunkModel(app.db)
    await chunk_model.create_index([("project_id", 1), ("created_at", -1)])
    # Backs the per-asset skip check and the per-asset reset delete, both of
    # which run once per asset on every /process call.
    await chunk_model.create_index([("project_id", 1), ("asset_id", 1)])
    # Backs iter_project_chunks' (asset_id, chunk_order) sort. Without it Mongo
    # sorts the matched documents in memory — chunk_content included — and a
    # project over ~32 MB of text fails the blocking-sort limit outright, i.e.
    # exactly the large projects /nlp/index/push exists to handle.
    await chunk_model.create_index(
        [("project_id", 1), ("asset_id", 1), ("chunk_order", 1)]
    )

    asset_model = AssetModel(app.db)
    await asset_model.create_index([("project_id", 1), ("created_at", -1)])
    logger.info("Database indexes ensured")

    # startup: build the configured providers. Constructed once here rather
    # than per request — each one owns an HTTP connection pool worth keeping
    # open. A missing API key or an unknown backend name raises here, so the
    # app refuses to start rather than failing on the first question a user
    # asks.
    app.generation_client = LLMChattingFactory(SETTINGS).create()
    app.embedding_client = LLMEmbeddingFactory(SETTINGS).create()
    app.vectordb_client = VectorDBFactory(SETTINGS).create()
    await app.vectordb_client.connect()
    logger.info(
        "Providers ready (generation=%s, embedding=%s, vectordb=%s)",
        SETTINGS.GENERATION_BACKEND,
        SETTINGS.EMBEDDING_BACKEND,
        SETTINGS.VECTOR_DB_BACKEND,
    )

    yield

    # shutdown: release the provider pools, then the MongoDB connection.
    # Each close is isolated: disconnect() raises VectorDBError on any failure,
    # and letting that propagate would skip every close after it — turning one
    # unclosable pool into four leaked ones and a clean SIGTERM into an error.
    logger.info("Shutting down %s", SETTINGS.APPLICATION_NAME)

    for name, close in (
        ("vector database", app.vectordb_client.disconnect),
        ("embedding client", app.embedding_client.aclose),
        ("generation client", app.generation_client.aclose),
    ):
        try:
            await close()
        except Exception as exc:
            # Shutdown is the one place a failure has nowhere left to propagate
            # to, so it is logged here rather than raised.
            logger.warning("Could not close the %s cleanly: %s", name, exc)

    app.client.close()


app = FastAPI(title="NotebookLLM-minus", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(NotebookLLMError)
async def domain_error_handler(request: Request, exc: NotebookLLMError) -> JSONResponse:
    """The one place domain errors become HTTP responses — and get logged.

    Lower layers raise and stay silent about failures; this is the single
    record of each one, so a failure is never logged twice or lost.
    """
    if exc.status_code >= 500:
        # Server-side fault: keep the traceback, including the chained __cause__.
        logger.exception("%s: %s", type(exc).__name__, exc)
    else:
        # Caller's mistake: a stack trace would be noise.
        logger.warning("%s: %s", type(exc).__name__, exc)

    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything we didn't anticipate.

    RequestLoggingMiddleware has already logged the traceback against this
    request's id, so this only shapes the response — logging here would
    duplicate it (and would also stop uvicorn from logging its own copy).
    """
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# add the nested routers
app.include_router(base_router)
app.include_router(data_router)
app.include_router(process_router)
app.include_router(nlp_router)
