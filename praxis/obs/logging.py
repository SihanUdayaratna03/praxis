"""Structured logging, configured once at process start.

JSON by default. An agent pipeline's log is data -- "which agent abstained, on
which span, at what confidence" is a query, and it should not require a regex
over prose to answer. The console renderer exists only for a human watching a
local run.

Timestamps are UTC and ISO-8601. Everything in Praxis that carries a time is
timezone-aware; logs are not an exception.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import Processor

from praxis.config.settings import LogFormat, Settings

_RUN_ID_KEY = "run_id"


def _shared_processors() -> list[Processor]:
    """Processors applied to every event regardless of output format."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def configure_logging(settings: Settings) -> None:
    """Install the logging configuration for this process.

    Idempotent: calling it twice replaces the configuration rather than
    stacking a second copy of every processor onto it.
    """
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    processors: list[Processor] = [
        *_shared_processors(),
        structlog.processors.format_exc_info,
        renderer,
    ]

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO),
        force=True,
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_run(run_id: str, **fields: Any) -> None:
    """Attach a run identifier to every subsequent event in this context.

    Every log line produced by a pipeline run carries the same run_id, so a
    single run can be extracted from an interleaved log without guessing at
    boundaries. Uses contextvars, so it survives across await points and does
    not leak between concurrent runs in the orchestrator.
    """
    structlog.contextvars.bind_contextvars(**{_RUN_ID_KEY: run_id, **fields})


def clear_run() -> None:
    """Drop everything bound by `bind_run` for this context."""
    structlog.contextvars.clear_contextvars()
