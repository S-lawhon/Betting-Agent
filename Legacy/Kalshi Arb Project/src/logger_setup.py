"""
src/logger_setup.py
───────────────────
Structured JSON logging via the standard-library ``logging`` module.

Uses structlog when available; falls back to a lightweight stdlib-only
JSON formatter so the project works in restricted environments.

Usage:
    from logger_setup import get_logger
    log = get_logger(__name__)
    log.info("startup_ok", environment="demo")

All log records are emitted as JSON to stdout.
"""

import json
import logging
import os
import sys
import time
from typing import Any

# ── Try to use structlog; fall back to stdlib ─────────────────────────────────
try:
    import structlog as _structlog
    _STRUCTLOG_AVAILABLE = True
except ImportError:
    _structlog = None  # type: ignore[assignment]
    _STRUCTLOG_AVAILABLE = False


# ── Stdlib fallback ───────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # Extra fields attached via log.info("msg", key=val) pattern
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            }:
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _StdlibBoundLogger:
    """
    Minimal structlog-compatible wrapper around a stdlib Logger.

    Supports: log.info("event", key=value, ...)
    """

    def __init__(self, logger: logging.Logger, context: dict[str, Any]) -> None:
        self._logger = logger
        self._context = context

    def bind(self, **kw: Any) -> "_StdlibBoundLogger":
        return _StdlibBoundLogger(self._logger, {**self._context, **kw})

    def _log(self, level: int, event: str, **kw: Any) -> None:
        extra = {**self._context, **kw}
        self._logger.log(level, event, extra=extra)

    def debug(self, event: str, **kw: Any) -> None:
        self._log(logging.DEBUG, event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._log(logging.INFO, event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._log(logging.WARNING, event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._log(logging.ERROR, event, **kw)

    def critical(self, event: str, **kw: Any) -> None:
        self._log(logging.CRITICAL, event, **kw)

    # Make isinstance checks against structlog.BoundLoggerBase succeed if
    # structlog is installed, but degrade gracefully if it isn't.
    @property
    def _proxy_to(self):  # noqa: ANN201
        return self._logger


# ── Public API ────────────────────────────────────────────────────────────────

def configure_logging(log_level: str = "INFO") -> None:
    """
    Call once at application startup (from main.py).

    Parameters
    ----------
    log_level:
        One of DEBUG | INFO | WARNING | ERROR.
        Overridden by the LOG_LEVEL environment variable.
    """
    effective_level = os.environ.get("LOG_LEVEL", log_level).upper()
    numeric_level = getattr(logging, effective_level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.setLevel(numeric_level)
    # Avoid duplicate handlers when called more than once (e.g. in tests)
    if not root.handlers:
        root.addHandler(handler)
    else:
        root.handlers = [handler]

    if _STRUCTLOG_AVAILABLE:
        _structlog.configure(
            processors=[
                _structlog.stdlib.add_log_level,
                # Note: add_logger_name is intentionally omitted — PrintLogger
                # has no .name attribute on older structlog versions (Python 3.9).
                # The logger name is bound explicitly in get_logger() instead.
                _structlog.processors.TimeStamper(fmt="iso", utc=True),
                _structlog.processors.StackInfoRenderer(),
                _structlog.processors.format_exc_info,
                _structlog.processors.JSONRenderer(),
            ],
            wrapper_class=_structlog.make_filtering_bound_logger(numeric_level),
            context_class=dict,
            logger_factory=_structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str, **initial_context: Any) -> Any:
    """
    Return a bound logger pre-seeded with ``name`` and any extra context.

    Returns a structlog BoundLogger when structlog is available, otherwise
    a stdlib-backed drop-in with the same API.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module.
    **initial_context:
        Extra fields attached to every message from this logger.
    """
    if _STRUCTLOG_AVAILABLE:
        # Bind 'logger' explicitly so the name appears in every log line
        # without relying on add_logger_name (which needs logger.name).
        return _structlog.get_logger(name).bind(logger=name, **initial_context)
    return _StdlibBoundLogger(logging.getLogger(name), initial_context)
