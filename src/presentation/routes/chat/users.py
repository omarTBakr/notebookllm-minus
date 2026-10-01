"""User routes: create, list, rename, get."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps

from ..schemas import CreateUserRequest, RenameUserRequest

users_router = APIRouter()


def _user_json(user) -> dict:
    return {
        "user_id": user.user_id,
        "label": user.label or user.user_id[:8],
        "created_at": user.created_at.isoformat(),
    }


@users_router.post("/users")
async def create_user(request: CreateUserRequest | None = None, http_request: Request = None):
    """Mint a new user, named so the picker is readable."""
    user = await deps.users(http_request).create(request.label if request else None)

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
    users = [_user_json(u) for u in await deps.users(http_request).list_users()]

    return JSONResponse(status_code=200, content={"count": len(users), "users": users})


@users_router.patch("/users/{user_id}")
async def rename_user(user_id: str, request: RenameUserRequest, http_request: Request):
    """Give a profile a name you will recognise in the list."""
    label = await deps.users(http_request).rename(user_id, request.label)

    return JSONResponse(status_code=200, content={"user_id": user_id, "label": label})


@users_router.get("/users/{user_id}")
async def get_user(user_id: str, http_request: Request):
    """Confirm a returning user still exists.

    404 here is routine, not exceptional: the browser holds an id across a
    database wipe, and the UI treats the 404 as "start fresh".
    """
    user = await deps.users(http_request).get(user_id)

    return JSONResponse(status_code=200, content=_user_json(user))


@users_router.delete("/users/{user_id}")
async def delete_user(user_id: str, http_request: Request):
    """Remove a user and everything that belongs to them (see `UserService.delete`)."""
    user, removed = await deps.users(http_request).delete(user_id)

    sessions = removed.pop("sessions")
    return JSONResponse(
        status_code=200,
        content={
            "user_id": user_id,
            "label": user.label,
            "deleted": {**removed, "sessions": sessions},
        },
    )
