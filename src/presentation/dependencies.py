"""Where a request is turned into services.

The presentation tier's composition point, and the only place in it that reads
``request.app.db`` and ``request.app.providers``. A route declares what it needs
from here and never learns where a database or a model client comes from, which
is what lets the layering test forbid the routes from reaching for either.

Plain functions over the ``Request`` rather than FastAPI ``Depends``: a few
builders take a chat as well, and the routes call them after loading it.
"""

from fastapi import Request

from application.services import (
    AccessService,
    AuthService,
    ChatService,
    ConversationService,
    DataService,
    FetchQueue,
    IdempotencyService,
    IndexService,
    MemoryService,
    MessageService,
    NLPService,
    SourceService,
    StudioService,
    UserService,
)
from application.tasks import generate_artifact_task
from application.tasks.jobs.ingest.fetch import fetch_url_task
from application.tasks.tracking.status import mark_queued

# --- retrieval, memory and answering ------------------------------------------


def nlp_service(request: Request, chat=None) -> NLPService:
    """Retrieval stack, using the chat's embedding model when it names one."""
    embedding_client = request.app.providers.embedding(
        getattr(chat, "embedding_model", None),
        getattr(chat, "embedding_dimensions", None),
    )
    return NLPService(
        embedding_client=embedding_client,
        vectordb_client=request.app.db.vectors(),
    )


def memory_service(request: Request, chat=None) -> MemoryService:
    """Read-only memory stack: no chat_client, since neither list_facts (a
    plain read) nor search (an embed + vector lookup) generates text.

    Reuses the same embedding client nlp_service() builds rather than
    asking the provider cache a second time — ProviderCache.embedding()
    would just return the same cached instance anyway, but there is no
    reason to look it up twice.
    """
    return MemoryService(
        embedding_client=request.app.providers.embedding(
            getattr(chat, "embedding_model", None),
            getattr(chat, "embedding_dimensions", None),
        ),
        vector_repo=request.app.db.vectors(),
        personal_info_repo=request.app.db.personal_user_info(),
    )


def image_index_service(request: Request):
    """The notebook image index, or None when MULTIMODAL_ENABLED is off."""
    embedder = request.app.providers.image_embedding()
    if embedder is None:
        return None

    from application.services.rag.ImageIndexService import ImageIndexService

    return ImageIndexService(embedding_client=embedder, vectordb_client=request.app.db.vectors())


def caption_client(request: Request):
    """The vision model that captions pictures, or None when none is configured."""
    from shared.utils import get_settings

    settings = get_settings()
    if not settings.MULTIMODAL_ENABLED or not settings.IMAGE_CAPTION_MODEL_ID:
        return None
    return request.app.providers.chatting(settings.IMAGE_CAPTION_MODEL_ID, thinking=False)


async def describe_picture(request: Request, data: bytes, mime: str, lang: str) -> str | None:
    """A caption for a picture attached to a question, or None with no caption model."""
    from application.services.ingest.ImageCaptionService import caption_picture

    captioner = caption_client(request)
    if captioner is None:
        return None
    return await caption_picture(captioner, data, mime, lang)


async def model_has_vision(chat) -> bool:
    """Whether the chat's answer model can be shown pictures. False while multimodal is off."""
    from application.services.llm.vision import has_vision
    from shared.utils import get_settings

    if not get_settings().MULTIMODAL_ENABLED:
        return False
    return await has_vision(getattr(chat, "generation_model", None))


def chat_service(request: Request, chat=None, vision: bool = False) -> ChatService:
    """Answering stack, using the chat's own models when it names them.

    Falls back to the .env defaults per field, so a chat that only overrides
    its chat model still embeds with the configured one.

    Builds a *second* chat client for planning and sub-answers, on the same model
    as generation_client but with thinking=False -- the same reason
    memory_service's extract_and_store call needs one of its own: a
    reasoning model asked for structured output leaves `content` empty and
    puts everything in `reasoning_content`, which fails generate_structured's
    parse on every retry. generation_client itself is left free to reason,
    since the streamed answer is prose, not JSON.

    memory_controller is optional -- ChatService.answer_stream already
    skips memory search when it is None (see DbProvider.personal_user_info's
    own docstring: memory is a Postgres-only feature, deliberately not
    abstract, so a Mongo deployment can still build one of these instead of
    failing to instantiate at all). Every *other* field here can raise on a
    genuinely broken config and that should surface; this one specific
    NotImplementedError means "this backend doesn't have memory", which is
    routine rather than a failure.
    """
    from shared.utils import get_settings

    try:
        memory_controller = memory_service(request, chat)
    except NotImplementedError:
        memory_controller = None

    # Both clients get a window that holds a grounded prompt: see CHAT_NUM_CTX.
    model = getattr(chat, "generation_model", None)
    num_ctx = get_settings().CHAT_NUM_CTX

    return ChatService(
        generation_client=request.app.providers.chatting(model, num_ctx=num_ctx),
        nlp_controller=nlp_service(request, chat),
        memory_controller=memory_controller,
        planner_client=request.app.providers.chatting(model, num_ctx=num_ctx, thinking=False),
        reranker=request.app.providers.reranker(),
        image_index=image_index_service(request),
        vision=vision,
        **guardrails(request, chat),
    )


def guardrails(request: Request, chat=None) -> dict:
    """The input and output guardrails for one answer, or nothing when off.

    Built per request because each carries the chat's language for its refusal
    text. The dispatcher is the app's own when it has one (tests set an
    in-process stand-in on app.guardrail_dispatcher), otherwise the batched
    Celery queues.
    """
    from shared.utils import get_settings

    settings = get_settings()
    if not settings.GUARDRAILS_ENABLED:
        return {}

    from application.guardrails import (
        CeleryGuardrailDispatcher,
        InputGuardrail,
        OutputGuardrail,
    )

    dispatcher = getattr(request.app, "guardrail_dispatcher", None) or CeleryGuardrailDispatcher(
        timeout=settings.GUARDRAIL_TIMEOUT_S
    )
    lang = getattr(chat, "lang", None)
    return {
        "input_guardrail": InputGuardrail(dispatcher, lang=lang),
        "output_guardrail": OutputGuardrail(dispatcher, lang=lang),
    }


async def nlp_service_for_project(request: Request, project_id: str | None = None) -> NLPService:
    """The retrieval stack using the *project's own* embedding model.

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
        chat = await index_service(request).chat_for_project(project_id)

        if chat is not None:
            embedding_client = request.app.providers.embedding(chat.embedding_model, chat.embedding_dimensions)

    return NLPService(
        embedding_client=embedding_client,
        vectordb_client=request.app.db.vectors(),
    )


def default_embedding_client(request: Request):
    """The embedding client .env configures, for the health probe."""
    return request.app.embedding_client


def default_generation_model_id(request: Request) -> str:
    return request.app.generation_client.model_id


# --- the services that only need the database ---------------------------------


def auth(request: Request) -> AuthService:
    """Signup, login, and resolving the session cookie to a user."""
    return AuthService(request.app.db)


def access(request: Request) -> AccessService:
    """Who owns a user, session, chat, project or task id."""
    return AccessService(request.app.db)


def conversations(request: Request) -> ConversationService:
    return ConversationService(request.app.db, lambda chat: nlp_service(request, chat))


def users(request: Request) -> UserService:
    return UserService(request.app.db, nlp_for_chat=lambda chat: nlp_service(request, chat))


def messages(request: Request) -> MessageService:
    return MessageService(request.app.db)


def sources(request: Request) -> SourceService:
    return SourceService(
        request.app.db,
        lambda chat: nlp_service(request, chat),
        fetch_queue=FetchQueue(
            task_name=fetch_url_task.name,
            enqueue=lambda chat_id, url, task_id: fetch_url_task.apply_async(args=[chat_id, url], task_id=task_id),
            on_queued=mark_queued,
        ),
    )


def index_service(request: Request) -> IndexService:
    return IndexService(request.app.db)


def idempotency(request: Request) -> IdempotencyService:
    return IdempotencyService(request.app.db)


def uploads(request: Request) -> DataService:
    """Validates an upload and files it under its project."""
    return DataService(request.app.db)


def studio(request: Request) -> StudioService:
    return StudioService(
        request.app.db,
        task_name=generate_artifact_task.name,
        enqueue=lambda args: generate_artifact_task.apply_async(args=args),
        on_queued=mark_queued,
    )
