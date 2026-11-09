"""The login layer in front of every data route.

Two router-level dependencies, applied to /chat, /data, /process and /nlp:

* `require_user` turns the session cookie into the signed-in user, stored on
  ``request.state.user``, or answers 401.
* `enforce_ownership` takes the route's path parameters (user_id, session_id,
  chat_id, project_id, task_id) and has AccessService check that the user owns
  every one, or answers 404.

Applied to the routers rather than to each route, so a route added later is
covered without anyone remembering to add a check. tests/api/test_isolation.py
walks app.routes to prove it.

The cookie, not a bearer header: PDF embeds, downloads and <embed src> are
plain same-origin GETs the browser makes on its own, and only a cookie rides
along with those.
"""

from fastapi import Request, Response

from presentation import dependencies as deps
from shared.exceptions import AuthenticationError
from shared.utils import get_settings

#: The path parameters that name something a user owns.
OWNED_PARAMS = ("user_id", "session_id", "chat_id", "project_id", "task_id")


async def require_user(request: Request):
    """The signed-in user, or 401."""
    token = request.cookies.get(get_settings().AUTH_COOKIE_NAME)
    resolved = await deps.auth(request).resolve(token)

    if resolved is None:
        raise AuthenticationError("Sign in to continue")

    request.state.user, request.state.auth_session = resolved
    request.state.auth_token = token
    return request.state.user


async def enforce_ownership(request: Request) -> None:
    """404 unless the signed-in user owns every id in the path."""
    params = {name: str(value) for name, value in request.path_params.items() if name in OWNED_PARAMS}

    if params:
        await deps.access(request).check(request.state.user.user_id, params)


def current_user(request: Request):
    """The user `require_user` attached. Only valid on a protected router."""
    return request.state.user


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        token,
        max_age=settings.AUTH_SESSION_DAYS * 86400,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.AUTH_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
    )
