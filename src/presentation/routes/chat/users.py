"""The signed-in user's own profile: read, rename, delete.

Every route here is self-only: enforce_ownership answers 404 for any user_id
other than the caller's. Accounts are created at /auth/signup.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps
from presentation.auth import clear_session_cookie, current_user

from ..schemas import RenameUserRequest

users_router = APIRouter()


def _user_json(user) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "label": user.label or user.user_id[:8],
        "created_at": user.created_at.isoformat(),
    }


@users_router.get("/users")
async def list_users(http_request: Request):
    """The signed-in user, in a list: nobody can enumerate other accounts.

    Kept for clients written against the old profile picker. New accounts are
    made with POST /auth/signup; there is no way to mint a user here any more.
    """
    users = [_user_json(current_user(http_request))]

    return JSONResponse(status_code=200, content={"count": len(users), "users": users})


@users_router.patch("/users/{user_id}")
async def rename_user(user_id: str, request: RenameUserRequest, http_request: Request):
    """Change the display name."""
    label = await deps.users(http_request).rename(user_id, request.label)

    return JSONResponse(status_code=200, content={"user_id": user_id, "label": label})


@users_router.get("/users/{user_id}")
async def get_user(user_id: str, http_request: Request):
    """The signed-in user's profile."""
    user = await deps.users(http_request).get(user_id)

    return JSONResponse(status_code=200, content=_user_json(user))


@users_router.delete("/users/{user_id}")
async def delete_user(user_id: str, http_request: Request):
    """Delete the account and everything in it (see `UserService.delete`)."""
    user, removed = await deps.users(http_request).delete(user_id)

    sessions = removed.pop("sessions")
    response = JSONResponse(
        status_code=200,
        content={
            "user_id": user_id,
            "label": user.label,
            "deleted": {**removed, "sessions": sessions},
        },
    )
    # The account is gone, and so is every session of it.
    clear_session_cookie(response)
    return response
