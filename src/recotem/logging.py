"""structlog setup for Recotem.

Call ``configure_logging(log_format)`` once at process start.  The redaction
processor is placed first in the chain to guarantee no sensitive key ever
reaches a renderer.

Format selection:
  - "json"    → JSONRenderer (for containers / log-shipping pipelines)
  - "console" → ConsoleRenderer with colours (for local development / TTY)
  - auto      → "json" when sys.stderr is not a TTY, "console" otherwise

Usage::

    from recotem.logging import configure_logging
    configure_logging("json")
"""

from __future__ import annotations

import logging
import sys

import structlog

from recotem.serving.log_redaction import redact_sensitive_keys

# Re-export so callers can do ``from recotem.logging import get_logger``.
get_logger = structlog.get_logger


def _auto_format() -> str:
    """Return "json" unless stderr is a TTY (interactive terminal)."""
    return "console" if sys.stderr.isatty() else "json"


def configure_logging(log_format: str = "auto") -> None:
    """Configure structlog with redaction and the requested output format.

    Parameters
    ----------
    log_format:
        One of "json", "console", or "auto" (default).  "auto" selects
        "console" when stderr is a TTY and "json" otherwise.

    Notes
    -----
    Calling this multiple times is safe — structlog replaces the previous
    configuration.  The stdlib ``logging`` root logger is configured to
    propagate into structlog so that third-party libraries that use
    ``logging.getLogger()`` are also captured.
    """
    resolved = log_format if log_format in ("json", "console") else _auto_format()

    if resolved == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    shared_processors: list[structlog.types.Processor] = [
        # Redaction MUST be first.
        redact_sensitive_keys,
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
