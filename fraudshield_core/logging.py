"""
fraudshield_core/logging.py
────────────────────────────
Structured JSON logging for FraudShield.

Every log record emits a flat JSON object with:
  timestamp, level, logger, message
  + any extra fields passed as keyword args

Usage:
    from fraudshield_core.logging import get_logger
    log = get_logger(__name__)
    log.info("scored", user_id="u_001", decision="block", score=0.94, latency_ms=23)

Context vars (set by RequestIDMiddleware):
    from fraudshield_core.logging import request_id_var
    request_id_var.set("abc12345")

The logger reads request_id_var automatically on each emit.
"""

import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

from fraudshield_core.config import config

# Per-request context propagated from RequestIDMiddleware
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _ContextFilter(logging.Filter):
    """Inject request_id from ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")
        return True


def _build_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    if config.LOG_FORMAT == "json":
        fmt = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        )
    else:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s  %(message)s  rid=%(request_id)s",
            datefmt="%H:%M:%S",
        )
    handler.setFormatter(fmt)
    handler.addFilter(_ContextFilter())
    return handler


_handler = _build_handler()
_level   = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)


class _KwLogger:
    """
    Structlog-style API over stdlib logging.
    Accepts arbitrary keyword args in info/warning/error/debug and passes them
    as `extra` so pythonjsonlogger includes them in the JSON output.
    """
    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def info(self, msg: str, **kwargs):
        self._logger.info(msg, extra=kwargs)

    def warning(self, msg: str, **kwargs):
        self._logger.warning(msg, extra=kwargs)

    def error(self, msg: str, **kwargs):
        self._logger.error(msg, extra=kwargs)

    def debug(self, msg: str, **kwargs):
        self._logger.debug(msg, extra=kwargs)

    # Expose standard attrs for code that checks isinstance or uses .level
    @property
    def level(self):
        return self._logger.level


def get_logger(name: str) -> _KwLogger:
    """Return a configured JSON logger. Call once per module at import time."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_handler)
    logger.setLevel(_level)
    logger.propagate = False
    return _KwLogger(logger)
