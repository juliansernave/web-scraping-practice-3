"""structlog configuration: pretty console renderer for dev, JSON renderer for prod.

Day 2. Every pipeline stage logs with bound context: ``log.bind(target=..., url=...)``.

Why structlog over ``print`` (PLAN.md gap #3): logs become *events with data*, not strings.
``log.info("extract.done", valid=48, invalid=2)`` is one line a human reads in dev (colorized
key=value) and a machine parses in prod (one JSON object per line — grep it, ship it to a log
store, alert on ``invalid > 0``). The event name (``extract.done``) is a stable key; the
context is structured fields. Same call site, two audiences, chosen by one flag.
"""

from __future__ import annotations

import logging

import structlog


def configure_logging(*, json_logs: bool = False, level: int = logging.INFO) -> None:
    """Configure structlog process-wide. Call once at startup (the CLI does this).

    ``json_logs=False`` renders colorized ``key=value`` for a human at a terminal;
    ``json_logs=True`` renders one JSON object per line for log aggregation in prod. The
    *only* difference is the final renderer — every ``log.info(...)`` call site is identical
    across dev and prod.
    """
    # Shared processors run on every event before the renderer. Order matters: context is
    # merged first, then level/timestamp are stamped, then exception info is rendered.
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,  # picks up bind_contextvars() (async-safe)
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,  # turn exc_info=... into a rendered traceback
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, renderer],
        # Drop events below `level` cheaply, before rendering — the documented fast path.
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Thin passthrough so callers import from one place."""
    return structlog.get_logger(name)
