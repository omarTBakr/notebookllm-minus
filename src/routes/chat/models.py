"""Model catalogue and per-chat model / settings tuning routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controllers import ModelController, for_source
from exceptions import InvalidInputError
from models import ChatModel, ChunkModel, ProjectModel
from utils import (
    default_chat_model,
    default_embedding_model,
    get_settings,
    split_source,
)

from ..schemas import ChatSettingsRequest, SetModelsRequest
from ._helpers import CHAT_CHUNK_OVERLAP, CHAT_CHUNK_SIZE, _nlp_controller

models_router = APIRouter()


@models_router.get("/models")
async def list_models(
    http_request: Request,
    probe_embeddings: bool = True,
    sources: str | None = None,
):
    """Installed models, split by what each can actually do.

    Embedding capability is probed rather than assumed — Ollama's tag list does
    not say, and the probe also reports the vector width a collection built
    with that model needs.

    `sources` narrows the discovery to a comma-separated subset ("local",
    "nvidia", ...). Discovery cost is wildly uneven — the local host answers in
    under a second without embedding probes, while NVIDIA's two-pass entitlement
    check walks eighty models — so the picker asks for the cheap sources first
    and merges the slow ones in as they land, instead of showing nothing until
    the slowest provider has finished.
    """
    wanted = [s.strip() for s in sources.split(",") if s.strip()] if sources is not None else None

    catalogue = await ModelController().catalogue(probe_embeddings=probe_embeddings, sources=wanted)

    return JSONResponse(status_code=200, content=catalogue)


@models_router.get("/models/quick")
async def list_quick_models(http_request: Request):
    """Return configured hosted chat models without provider network calls."""
    controller = ModelController()
    settings = get_settings()
    return JSONResponse(
        status_code=200,
        content={
            "chat": controller.configured_chat_models(),
            "embedding": [],
            "current": {
                "chat": default_chat_model(settings),
                "embedding": default_embedding_model(settings),
                "embedding_dimensions": settings.EMBEDDING_MODEL_SIZE,
            },
        },
    )


@models_router.patch("/chats/{chat_id}/models")
async def set_chat_models(chat_id: str, request: SetModelsRequest, http_request: Request):
    """Point one chat at different models.

    Switching the embedding model **rebuilds this chat's index**: the vector
    width is fixed when a collection is created, so old vectors are unusable by
    the new model. The chunks are already in MongoDB, so the rebuild re-embeds
    them rather than asking for the documents again.
    """
    db = http_request.app.db
    chat_model = ChatModel(db)

    chat = await chat_model.get_chat(chat_id)

    dimensions = None

    if request.embedding_model:
        # Probe on the source the id names, not always the local one — a
        # cloud or NVIDIA embedding model would otherwise be rejected as
        # incapable, since the local Ollama has never heard of it.
        source, tag = split_source(request.embedding_model)
        dimensions = await for_source(source).embedding_dimensions(tag)

        if not dimensions:
            raise InvalidInputError(
                f"{request.embedding_model!r} cannot produce embeddings — "
                "pick one from the embedding list at GET /chat/models"
            )

    await chat_model.set_models(
        chat_id,
        generation_model=request.generation_model,
        embedding_model=request.embedding_model,
        embedding_dimensions=dimensions,
    )

    reindexed = 0

    if request.embedding_model and chat.has_documents:
        chunk_model = ChunkModel(db)
        project = await ProjectModel(db).get_project(chat_id)

        # reset=True drops the old collection so the new one is created at the
        # new width. Without it every insert would be rejected for a dimension
        # mismatch — the failure mode EMBEDDING_MODEL_SIZE exists to prevent.
        result = await _nlp_controller(http_request, await chat_model.get_chat(chat_id)).index_chunks(
            chunk_model=chunk_model,
            project_object_id=project.id,
            project_id=chat_id,
            reset=True,
        )
        reindexed = result["chunks_indexed"]

        from ._helpers import logger

        logger.info(
            "Re-indexed chat %r under %r: %d chunk(s)",
            chat_id,
            request.embedding_model,
            reindexed,
        )

    updated = await chat_model.get_chat(chat_id)
    settings = get_settings()

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "generation_model": updated.generation_model or default_chat_model(settings),
            "embedding_model": updated.embedding_model or default_embedding_model(settings),
            "embedding_dimensions": (updated.embedding_dimensions or settings.EMBEDDING_MODEL_SIZE),
            "reindexed_chunks": reindexed,
        },
    )


@models_router.patch("/chats/{chat_id}/settings")
async def set_chat_settings(chat_id: str, request: ChatSettingsRequest, http_request: Request):
    """Tune one chat: temperature, output length, splitter, web grounding.

    Only the fields sent are written, so each control saves on its own without
    overwriting the others.
    """
    chat_model = ChatModel(http_request.app.db)

    await chat_model.get_chat(chat_id)

    changes = request.model_dump(exclude_none=True)

    await chat_model.set_settings(chat_id, changes)

    chat = await chat_model.get_chat(chat_id)
    settings = get_settings()

    return JSONResponse(
        status_code=200,
        content={
            "chat_id": chat_id,
            "temperature": (
                chat.temperature if chat.temperature is not None else settings.GENERATION_DEFAULT_TEMPERATURE
            ),
            "max_tokens": chat.max_tokens or settings.GENERATION_DEFAULT_MAX_TOKENS,
            "chunk_size": chat.chunk_size or CHAT_CHUNK_SIZE,
            "overlap_size": (chat.overlap_size if chat.overlap_size is not None else CHAT_CHUNK_OVERLAP),
            "web_search": chat.web_search,
            "highlight_color": chat.highlight_color,
            "applied": sorted(changes),
        },
    )
