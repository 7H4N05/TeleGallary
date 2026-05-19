"""
Structured logger using structlog + rich for beautiful console output.
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import structlog
from rich.console import Console
from rich.logging import RichHandler


_console = Console(stderr=True)
_configured = False


def _local_timestamper(logger, method, event_dict):
    """
    Stamp each log event with the current local wall-clock time.

    structlog's built-in TimeStamper(utc=False) reads time.localtime() but
    formats with strftime which on Windows can still emit UTC if the process
    timezone hasn't been propagated.  Using datetime.now().astimezone() always
    reflects the OS local timezone (e.g. IST) regardless of the TZ env-var.
    """
    event_dict["timestamp"] = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S %Z")
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        level=log_level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_console, rich_tracebacks=True, markup=True)],
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _local_timestamper,
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    _configured = True


def get_logger(name: str) -> structlog.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)

