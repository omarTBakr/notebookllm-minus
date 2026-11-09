"""Accounts: sign up, sign in, sign out, and the signed-in user.

Public on purpose: these are the routes a browser without a session can
reach. Everything else under /chat, /data, /process and /nlp sits behind
presentation/auth.py.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps
from presentation.auth import clear_session_cookie, require_user, set_session_cookie

from .schemas import ChangePasswordRequest, LoginRequest, SignupRequest

auth_router = APIRouter(prefix="/auth", tags=["auth"])


def user_json(user) -> dict:
    """What a client may see of a user. Never the password hash."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "label": user.label or (user.email or "").split("@", 1)[0],
        "created_at": user.created_at.isoformat(),
    }


@auth_router.post("/signup", status_code=201)
async def signup(request: SignupRequest, http_request: Request):
    signed_in = await deps.auth(http_request).signup(
        request.email, request.password, request.display_name, request.claim_user_id
    )
    response = JSONResponse(status_code=201, content={"user": user_json(signed_in.user)})
    set_session_cookie(response, signed_in.token)
    return response


@auth_router.post("/login")
async def login(request: LoginRequest, http_request: Request):
    signed_in = await deps.auth(http_request).login(request.email, request.password)
    response = JSONResponse(status_code=200, content={"user": user_json(signed_in.user)})
    set_session_cookie(response, signed_in.token)
    return response


@auth_router.post("/logout")
async def logout(http_request: Request):
    """End this browser's session. Fine to call when already signed out."""
    from shared.utils import get_settings

    await deps.auth(http_request).logout(http_request.cookies.get(get_settings().AUTH_COOKIE_NAME))
    response = JSONResponse(status_code=200, content={"signed_out": True})
    clear_session_cookie(response)
    return response


@auth_router.get("/me")
async def me(user=Depends(require_user)):
    return JSONResponse(status_code=200, content={"user": user_json(user)})


@auth_router.post("/logout-all")
async def logout_all(http_request: Request, user=Depends(require_user)):
    """End every session of this account, this browser's included."""
    ended = await deps.auth(http_request).logout_everywhere(user.user_id)
    response = JSONResponse(status_code=200, content={"signed_out": True, "sessions_ended": ended})
    clear_session_cookie(response)
    return response


@auth_router.post("/password")
async def change_password(request: ChangePasswordRequest, http_request: Request, user=Depends(require_user)):
    """Change the password; every other signed-in browser is signed out."""
    await deps.auth(http_request).change_password(
        user, request.current_password, request.new_password, keep_token=http_request.state.auth_token
    )
    return JSONResponse(status_code=200, content={"changed": True})
