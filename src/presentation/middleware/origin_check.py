"""Refuse state-changing requests sent from another site (CSRF defence).

The session cookie is SameSite=Lax, which already keeps it off cross-site
POSTs in current browsers. This is the second line: a POST, PUT, PATCH or
DELETE whose Origin (or, failing that, Referer) names a different host than
the one it was sent to is refused with 403 before it reaches a route.

Hostnames are compared without ports: nginx forwards ``Host: $host``, which
drops the port the browser used, so localhost:8081 arrives as "localhost".
A request with neither header (curl, a script, a server-side client) is let
through: CSRF needs a browser, and browsers always send Origin on these.
"""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _hostname(value: str | None) -> str | None:
    if not value or value == "null":
        return None
    return (urlsplit(value).hostname or "").lower() or None


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in UNSAFE_METHODS:
            source = request.headers.get("origin") or request.headers.get("referer")
            if source is not None:
                expected = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]
                if _hostname(source) != expected.lower():
                    return JSONResponse(status_code=403, content={"detail": "Cross-site request refused"})

        return await call_next(request)
