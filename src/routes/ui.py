"""Serves the web UI: one Jinja page, assembled from partials.

The templates live in ``web/templates`` rather than ``templates/`` because that
name is already taken by the model-facing prompt locales. Two different kinds
of template, two directories, no ambiguity about which one a file belongs to.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates  # ty: ignore[unresolved-import]

from utils import get_logger, get_settings

logger = get_logger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

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
        context={"app_name": settings.APPLICATION_NAME, "lang": lang},
    )
