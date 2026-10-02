"""Structured logging and Prometheus metrics shared by the API and the worker."""

from __future__ import annotations

import logging
import sys

import structlog
from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("pcbis_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("pcbis_http_request_seconds", "HTTP request latency", ["method", "route"])
INSPECTIONS_SUBMITTED = Counter("pcbis_inspections_submitted_total", "Inspections accepted by the API")


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if fmt == "json" else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level.upper()]),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")
