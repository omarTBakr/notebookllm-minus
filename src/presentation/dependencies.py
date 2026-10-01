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
    ChatService,
    ConversationService,
    DataService,
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


def chat_service(request: Request, chat=None) -> ChatService:
    """Answering stack, using the chat's own models when it names them.

    Falls back to the .env defaults per field, so a chat that only overrides
    its chat model still embeds with the configured one.

    Builds a *second* chat client for query decomposition, on the same model
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
    try:
        memory_controller = memory_service(request, chat)
    except NotImplementedError:
        memory_controller = None

    return ChatService(
        generation_client=request.app.providers.chatting(getattr(chat, "generation_model", None)),
        nlp_controller=nlp_service(request, chat),
        memory_controller=memory_controller,
        decompose_client=request.app.providers.chatting(getattr(chat, "generation_model", None), thinking=False),
    )


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


def conversations(request: Request) -> ConversationService:
    return ConversationService(request.app.db, lambda chat: nlp_service(request, chat))


def users(request: Request) -> UserService:
    return UserService(request.app.db, nlp_for_chat=lambda chat: nlp_service(request, chat))


def messages(request: Request) -> MessageService:
    return MessageService(request.app.db)


def sources(request: Request) -> SourceService:
    return SourceService(request.app.db, lambda chat: nlp_service(request, chat))


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
