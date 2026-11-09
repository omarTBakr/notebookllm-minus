"""Serves the web UI: the login page, and the app page assembled from partials.

The templates live in ``web/templates`` — HTML, rendered per request — as
opposed to ``application/prompts/``, the model-facing prompt locales. Two
different kinds of template, two clearly-named places, no ambiguity about
which one a file belongs to.
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates  # ty: ignore[unresolved-import]

from presentation import dependencies as deps
from shared.utils import get_logger, get_settings

logger = get_logger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _static_version() -> str:
    """A short stamp that changes whenever any asset under static/ changes.

    Appended to every asset URL as ?v=... so the browser cannot pair fresh HTML
    with a cached copy of the previous stylesheet. That pairing is what made
    every icon render as a 300x150 black rectangle: the markup referenced a
    `.ico` class that only existed in CSS the browser had already cached.

    Computed once at import — assets do not change while the process runs.
    """
    newest = 0.0

    for path in STATIC_DIR.rglob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)

    return hashlib.sha1(str(newest).encode()).hexdigest()[:8]


STATIC_VERSION = _static_version()


class RevalidatingStaticFiles(StaticFiles):
    """Serve assets, but make the browser check before reusing them.

    ?v= on the stylesheet URLs covers everything the template references, but
    an ES module's `import "./api.js"` is a static specifier — it cannot carry
    the stamp, so a changed sub-module could still be served from cache while
    its versioned entry point was fresh.

    `no-cache` does not mean "do not store": it means revalidate first. With
    the ETag StaticFiles already sends, an unchanged asset costs one 304 and
    a changed one is fetched. That closes the gap for every asset at once.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


ui_router = APIRouter(tags=["ui"])


def _lang(request: Request) -> str:
    """``?lang=ar`` pins the first paint to right-to-left; otherwise DEFAULT_LANG,
    and the client then applies whatever the visitor last chose."""
    from application.prompts.locales import SUPPORTED_LANGS

    requested = (request.query_params.get("lang") or "").strip().lower()
    return requested if requested in SUPPORTED_LANGS else get_settings().DEFAULT_LANG


async def _signed_in(request: Request) -> bool:
    token = request.cookies.get(get_settings().AUTH_COOKIE_NAME)
    return await deps.auth(request).resolve(token) is not None


def _page(request: Request, name: str):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "app_name": get_settings().APPLICATION_NAME,
            "lang": _lang(request),
            "static_v": STATIC_VERSION,
        },
    )


def _with_query(path: str, request: Request) -> str:
    query = request.url.query
    return f"{path}?{query}" if query else path


@ui_router.get("/")
async def index(request: Request):
    """The app, for a signed-in browser; everyone else is sent to /login."""
    if not await _signed_in(request):
        return RedirectResponse(_with_query("/login", request), status_code=303)

    return _page(request, "index.html")


@ui_router.get("/login")
async def login_page(request: Request):
    """Sign in / sign up. A browser that is already signed in goes to the app."""
    if await _signed_in(request):
        return RedirectResponse(_with_query("/", request), status_code=303)

    return _page(request, "login.html")
