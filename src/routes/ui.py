"""Serves the web UI: one Jinja page, assembled from partials.

The templates live in ``web/templates`` rather than ``templates/`` because that
name is already taken by the model-facing prompt locales. Two different kinds
of template, two directories, no ambiguity about which one a file belongs to.
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates  # ty: ignore[unresolved-import]

from utils import get_logger, get_settings

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


@ui_router.get("/")
async def index(request: Request):
    """The chat page.

    ``lang`` may be pinned with ``?lang=ar`` so the very first paint is already
    right-to-left; without it the server's DEFAULT_LANG is used and the client
    then applies whatever the visitor last chose.
    """
    settings = get_settings()

    requested = (request.query_params.get("lang") or "").strip().lower()

    from templates.locales import SUPPORTED_LANGS

    lang = requested if requested in SUPPORTED_LANGS else settings.DEFAULT_LANG

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.APPLICATION_NAME,
            "lang": lang,
            "static_v": STATIC_VERSION,
        },
    )
