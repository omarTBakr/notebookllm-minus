from enum import Enum


class LogLevel(str, Enum):
    """Threshold below which records are dropped.

    The names are the ones `logging` itself uses, because that is where the
    value ends up — logging_config.py hands it straight to dictConfig.
    """

    CRITICAL = "CRITICAL"
    ERROR    = "ERROR"
    WARNING  = "WARNING"
    INFO     = "INFO"
    DEBUG    = "DEBUG"
    NOTSET   = "NOTSET"


class LogFormat(str, Enum):
    """How a record is rendered."""

    TEXT = "text"  # human-readable, for a terminal
    JSON = "json"  # structured, for a log aggregator
