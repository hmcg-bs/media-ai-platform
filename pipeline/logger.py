"""Structured logging setup. All modules call ``get_logger(__name__)``.

Logs render as machine-parseable JSON (one line per event) so the same setup
works locally and, later, in Cloud Logging.
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging once, at process entry."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            # NB: no add_logger_name — it reads logger.name, which PrintLogger
            # (from PrintLoggerFactory) doesn't have; it crashes loggers resolved
            # after configure_logging (e.g. lazily-imported stages 06/07).
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger tagged with the pipeline name."""
    return structlog.get_logger(name).bind(pipeline="ensemble_extraction")
