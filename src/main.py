import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from exceptions import NotebookLLMError
from factories import DbFactory, ProviderCache
from middleware import RequestLoggingMiddleware
from routes import (
    STATIC_DIR,
    RevalidatingStaticFiles,
    base_router,
    chat_router,
    data_router,
    nlp_router,
    process_router,
    ui_router,
)
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

    # Connect the document database provider (Mongo, Postgres, …) — the choice
    # is made by DOCUMENT_DB_BACKEND in .env; everything else is agnostic.
    app.db = DbFactory(SETTINGS).create()
    await app.db.connect()
    logger.info("DB connected (backend=%s)", SETTINGS.DOCUMENT_DB_BACKEND)

    # Create indexes once at startup — idempotent, safe to re-run.
    await app.db.setup_indexes()
    logger.info("Database indexes ensured")

    # startup: build the configured providers. Constructed once here rather
    # than per request — each one owns an HTTP connection pool worth keeping
    # open. A missing API key or an unknown backend name raises here, so the
    # app refuses to start rather than failing on the first question a user
    # asks.
    # One cache for every model a chat might name, rather than a single client
    # pinned to .env. The defaults below are just its first two entries.
    app.providers = ProviderCache(SETTINGS)
    app.generation_client = app.providers.chatting()
    app.embedding_client = app.providers.embedding()
    logger.info(
        "Providers ready (generation=%s, embedding=%s)",
        SETTINGS.GENERATION_BACKEND,
        SETTINGS.EMBEDDING_BACKEND,
    )

    # Warm the model probe cache in the background. Each probe is a real embed
    # call, so doing it lazily on the first request left the settings dropdowns
    # empty for the best part of twenty seconds.
    async def warm_models() -> None:
        try:
            from controllers import ModelController

            catalogue = await ModelController().catalogue()
            logger.info(
                "Model catalogue warm (%d chat, %d embedding)",
                len(catalogue["chat"]),
                len(catalogue["embedding"]),
            )
        except Exception as exc:
            # Not fatal: the route will probe on demand if this failed.
            logger.warning("Could not warm the model catalogue: %s", exc)

    app.warm_task = asyncio.create_task(warm_models())

    yield

    # shutdown: release the provider pools, then the MongoDB connection.
    # Each close is isolated: disconnect() raises VectorDBError on any failure,
    # and letting that propagate would skip every close after it — turning one
    # unclosable pool into four leaked ones and a clean SIGTERM into an error.
    logger.info("Shutting down %s", SETTINGS.APPLICATION_NAME)

    for name, close in (
        ("database", app.db.disconnect),
        ("provider clients", app.providers.aclose_all),
    ):
        try:
            await close()
        except Exception as exc:
            logger.warning("Could not close the %s cleanly: %s", name, exc)


app = FastAPI(title="NotebookLLM-minus", lifespan=lifespan)

# /static is excluded by prefix: a page load fetches many assets and each one
# would otherwise get its own INFO line, burying the requests that matter.
app.add_middleware(RequestLoggingMiddleware, exclude_paths=("/static",))


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
app.include_router(chat_router)

# The UI last: its "/" route must not shadow an API prefix, and StaticFiles
# serves the css/js the Jinja page links to.
app.mount("/static", RevalidatingStaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(ui_router)
