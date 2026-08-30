"""User routes: create, list, rename, get."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from exceptions import ProjectNotFoundError
from models import (
    AssetModel,
    ChatModel,
    ChunkModel,
    MessageModel,
    ProjectModel,
    SessionModel,
    User,
    UserModel,
)

from ..schemas import CreateUserRequest, RenameUserRequest
from ._helpers import _new_id, _nlp_controller, logger

users_router = APIRouter()


@users_router.post("/users")
async def create_user(request: CreateUserRequest | None = None, http_request: Request = None):
    """Mint a new user, named so the picker is readable.

    A blank label would make every entry in the list look identical, so one is
    generated from the count when the caller does not supply a name.
    """
    user_model = UserModel(http_request.app.db)

    label = (request.label if request else "") or ""

    if not label.strip():
        label = f"User {await user_model.count_users() + 1}"

    user = User(user_id=_new_id(), label=label.strip())

    await user_model.create_user(user)

    logger.debug("Created user %r (%s)", user.user_id, user.label)

    return JSONResponse(
        status_code=200,
        content={
            "user_id": user.user_id,
            "label": user.label,
            "created_at": user.created_at.isoformat(),
        },
    )


@users_router.get("/users")
async def list_users(http_request: Request):
    """Every profile on this install — the "who am I" picker, not a login."""

    users = [
        {
            "user_id": u.user_id,
            "label": u.label or u.user_id[:8],
            "created_at": u.created_at.isoformat(),
        }
        async for u in UserModel(http_request.app.db).iter_users()
    ]

    return JSONResponse(status_code=200, content={"count": len(users), "users": users})


@users_router.patch("/users/{user_id}")
async def rename_user(user_id: str, request: RenameUserRequest, http_request: Request):
    """Give a profile a name you will recognise in the list."""

    user_model = UserModel(http_request.app.db)

    await user_model.get_user(user_id)
    await user_model.rename(user_id, request.label.strip())

    return JSONResponse(
        status_code=200, content={"user_id": user_id, "label": request.label.strip()}
    )


@users_router.get("/users/{user_id}")
async def get_user(user_id: str, http_request: Request):
    """Confirm a returning user still exists.

    404 here is routine, not exceptional: the browser holds an id across a
    database wipe, and the UI treats the 404 as "start fresh".
    """
    user = await UserModel(http_request.app.db).get_user(user_id)

    return JSONResponse(
        status_code=200,
        content={
            "user_id": user.user_id,
            "label": user.label or user.user_id[:8],
            "created_at": user.created_at.isoformat(),
        },
    )


@users_router.delete("/users/{user_id}")
async def delete_user(user_id: str, http_request: Request):
    """Remove a user and everything that belongs to them.

    Nothing in either store cascades on its own — there are no foreign keys on
    Postgres and no such concept on Mongo — so the whole tree is walked here:

        user -> sessions
             -> chats -> messages
                      -> project -> assets
                                 -> chunks
                                 -> vector collection

    Derived-first throughout, and the owning row last at every level. A failure
    part-way leaves the user still listed with less under them, which is
    re-runnable; the reverse would leave orphans that nothing lists and nothing
    can reach to clean up.

    A chat_id *is* a project_id, which is what lets one loop clear a notebook's
    documents, chunks and vectors together.
    """
    db = http_request.app.db

    # 404s if there is no such user, before anything is deleted.
    user = await UserModel(db).get_user(user_id)

    chat_model = ChatModel(db)
    project_model = ProjectModel(db)
    removed = {"chats": 0, "messages": 0, "assets": 0, "chunks": 0, "collections": 0}

    # Collected before the loop: iterating a cursor while deleting out from
    # under it is not something either driver promises to survive.
    chats = [c async for c in chat_model.iter_user_chats(user_id)]

    for chat in chats:
        chat_id = chat.chat_id

        # --- vectors ---
        collection = _nlp_controller(http_request, chat).collection_name(chat_id)
        if await db.vectors().collection_exists(collection):
            if await db.vectors().delete_collection(collection):
                removed["collections"] += 1

        # --- chunks and assets, via the project the chat's documents sit in ---
        # A notebook nobody uploaded to has no project row, and that is normal
        # rather than an error: there is simply nothing under it to remove.
        try:
            project = await project_model.get_project(chat_id)
        except ProjectNotFoundError:
            project = None

        if project is not None:
            # Chunks key on the project's row id, not its business id.
            removed["chunks"] += await ChunkModel(db).count_project_chunks(project.id)
            await ChunkModel(db).delete_chunks_for_project(project.id)

            removed["assets"] += len(
                [a async for a in AssetModel(db).iter_assets_for_projects([chat_id])]
            )
            await AssetModel(db).delete_assets_for_project(chat_id)
            await project_model.delete_project(chat_id)

        # --- the conversation, then the chat itself ---
        removed["messages"] += await MessageModel(db).delete_messages_for_chat(chat_id)
        if await chat_model.delete_chat(chat_id):
            removed["chats"] += 1

    sessions_removed = await SessionModel(db).delete_sessions_for_user(user_id)
    await UserModel(db).delete_user(user_id)

    logger.info(
        "Deleted user %r (%s): %d chat(s), %d session(s), %d asset(s), %d chunk(s), "
        "%d message(s), %d vector collection(s)",
        user_id,
        user.label,
        removed["chats"],
        sessions_removed,
        removed["assets"],
        removed["chunks"],
        removed["messages"],
        removed["collections"],
    )

    return JSONResponse(
        status_code=200,
        content={
            "user_id": user_id,
            "label": user.label,
            "deleted": {**removed, "sessions": sessions_removed},
        },
    )
