"""Central logging configuration for NotebookLLM-minus.

Everything in the project logs through the stdlib ``logging`` module; this
module owns the one-time setup of handlers/formatters and exposes helpers the
rest of the code uses:

    from utils import get_logger
    logger = get_logger(__name__)

``setup_logging()`` must run before the first log call. ``main.py`` calls it at
import time, which is *after* uvicorn installs its own config, so ours wins.
"""

import json
import logging
import logging.config
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a circular import at runtime (config imports nothing here)
    from .config import Settings

# Correlation id for the in-flight request. The middleware sets it; the filter
# below copies it onto every record so unrelated libraries' logs are tagged too.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes the stdlib puts on every LogRecord. Anything outside this set was
# passed by the caller via `extra={...}` and is worth emitting in JSON mode.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName", "request_id"}

_configured = False


def new_request_id() -> str:
    """Short, log-friendly correlation id."""
    return uuid.uuid4().hex[:12]


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id (``-`` outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line — for shipping to a log aggregator."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Anything handed in through `extra={...}`.
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }
        if extras:
            payload["extra"] = {k: _jsonable(v) for k, v in extras.items()}

        return json.dumps(payload, ensure_ascii=False, default=str)


def _jsonable(value):
    """Best-effort conversion so one odd `extra` value can't break the handler."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


def _build_config(settings: "Settings", log_file: Path | None) -> dict:
    formatter = "json" if settings.LOG_FORMAT.lower() == "json" else "text"

    handlers: dict[str, dict] = {}
    if settings.LOG_TO_CONSOLE:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "level": settings.LOG_LEVEL,
            "formatter": formatter,
            "filters": ["request_id"],
            "stream": "ext://sys.stdout",
        }
    if log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": settings.LOG_LEVEL,
            "formatter": formatter,
            "filters": ["request_id"],
            "filename": str(log_file),
            "maxBytes": settings.LOG_MAX_BYTES,
            "backupCount": settings.LOG_BACKUP_COUNT,
            "encoding": "utf-8",
            "delay": True,
        }

    active = list(handlers)

    return {
        "version": 1,
        # Loggers created at import time (module-level `get_logger(__name__)`)
        # must keep working after this config is applied.
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": RequestIdFilter},
        },
        "formatters": {
            "text": {
                "format": (
                    "%(asctime)s | %(levelname)-8s | %(request_id)s | "
                    "%(name)s:%(lineno)d | %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json": {
                "()": JsonFormatter,
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": handlers,
        "root": {"level": settings.LOG_LEVEL, "handlers": active},
        "loggers": {
            # Let uvicorn's records flow into our handlers instead of its own,
            # so every line in the process shares one format.
            "uvicorn": {"handlers": [], "propagate": True, "level": settings.LOG_LEVEL},
            "uvicorn.error": {"handlers": [], "propagate": True},
            # Our middleware already logs each request with timing + status.
            "uvicorn.access": {"handlers": [], "propagate": False},
            # Chatty third parties.
            "pymongo": {"level": "WARNING"},
            "multipart": {"level": "WARNING"},
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }


def setup_logging(settings: "Settings | None" = None, *, force: bool = False) -> None:
    """Install handlers/formatters. Safe to call more than once (later calls no-op)."""
    global _configured
    if _configured and not force:
        return

    if settings is None:
        from .config import get_settings

        settings = get_settings()

    log_file: Path | None = None
    if settings.LOG_TO_FILE:
        log_file = settings.log_file_path
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # An unwritable log directory must not take the application down;
            # fall back to console-only and say so once configured.
            log_file = None

    logging.config.dictConfig(_build_config(settings, log_file))
    _configured = True

    logger = logging.getLogger(__name__)
    if settings.LOG_TO_FILE and log_file is None:
        logger.warning(
            "Could not create log directory %s — logging to console only",
            settings.log_file_path.parent,
        )
    logger.debug(
        "Logging configured (level=%s, format=%s, file=%s)",
        settings.LOG_LEVEL,
        settings.LOG_FORMAT,
        log_file or "disabled",
    )


def get_logger(name: str) -> logging.Logger:
    """Module-level logger. Pass ``__name__`` from the calling module."""
    return logging.getLogger(name)
