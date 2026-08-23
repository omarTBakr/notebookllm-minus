"""User routes: create, list, rename, get."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models import User, UserModel

from ..schemas import CreateUserRequest, RenameUserRequest
from ._helpers import _new_id, logger

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
