"""Per-request logging: correlation id, one start line, one completion line."""

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from utils import get_logger, new_request_id, request_id_ctx

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Tag each request with an id and log its outcome.

    The id is taken from an inbound ``X-Request-ID`` header when present (so a
    reverse proxy's id carries through) and is echoed back on the response.
    Every log line emitted while handling the request carries the same id.
    """

    def __init__(self, app, exclude_paths: tuple[str, ...] = ()):
        """*exclude_paths* are matched as prefixes, e.g. ("/static",)."""
        super().__init__(app)
        self.exclude_paths = exclude_paths

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = request_id_ctx.set(request_id)
        # Prefix, not equality: a static mount serves many paths under one
        # root, and listing them all would be a losing game.
        quiet = request.url.path.startswith(self.exclude_paths) if self.exclude_paths else False

        if not quiet:
            # DEBUG, not INFO: the completion line below already reports the
            # request. This one only matters when a request never finishes.
            logger.debug(
                "-> %s %s",
                request.method,
                request.url.path,
                extra={
                    "client": request.client.host if request.client else None,
                    "query": str(request.url.query) or None,
                },
            )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            # Log here so the traceback is tied to the request id, then let the
            # exception continue to Starlette's handler (which returns the 500).
            logger.exception(
                "!! %s %s failed after %.1fms",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            if not quiet:
                logger.log(
                    _level_for(response.status_code),
                    "<- %s %s %s in %.1fms",
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                    extra={
                        "status_code": response.status_code,
                        "duration_ms": round(duration_ms, 1),
                    },
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_ctx.reset(token)


def _level_for(status_code: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO
