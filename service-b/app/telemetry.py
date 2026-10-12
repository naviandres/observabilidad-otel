"""OpenTelemetry engine shared by both microservices.

This module is the single configuration point for the three signals
(traces, metrics and logs). The ``OTEL_ENABLED`` environment variable is the
only switch: when it is ``false`` no OTLP exporter is created for any signal
and the returned tracer/meter are no-ops, so no span is recorded. This makes
the A/B benchmark honest: same binary, flag on/off.

``configure_telemetry`` accepts optional ``span_exporter``/``metric_reader``/
``log_exporter`` arguments so tests can wire in-memory exporters without
touching the network.

Logs travel on two paths (PLAN §1 anticipated ``opentelemetry-instrumentation-
logging``; wired directly on the SDK here): stdout keeps the structlog JSON
line for the platform log driver / GKE filelog, and the OTLP ``LoggingHandler``
exports every stdlib record to the collector so the ``awscloudwatchlogs`` /
googlecloud log exporters receive it with the active span's ``trace_id``
(assertion A5/A11 correlation).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import structlog
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

DEFAULT_OTLP_ENDPOINT = "http://otel-collector:4317"


def is_otel_enabled() -> bool:
    """Return ``True`` when the OpenTelemetry engine must be active.

    Reads ``OTEL_ENABLED`` on every call (default ``true``), so the flag can
    be toggled per process without reimporting the module.
    """
    return os.getenv("OTEL_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _build_resource(service_name: str) -> Resource:
    """Build the OTel resource attached to every signal."""
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv(
                "DEPLOYMENT_ENVIRONMENT", "development"
            ),
        }
    )


def _add_trace_context(
    logger: object, method_name: str, event_dict: dict
) -> dict:
    """structlog processor that injects the active span identifiers.

    The values come from the OpenTelemetry SDK span context, so the
    application code never touches ``trace_id``/``span_id`` directly. When no
    span is active the fields are explicitly ``None``.
    """
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    else:
        event_dict["trace_id"] = None
        event_dict["span_id"] = None
    return event_dict


def build_json_formatter() -> structlog.stdlib.ProcessorFormatter:
    """Return the JSON formatter used by every log handler (test friendly)."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
    ]
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )


def configure_logging(service_name: str, level: int = logging.INFO) -> logging.Logger:
    """Route structlog JSON records through stdlib logging.

    structlog renders to JSON; a :class:`logging.StreamHandler` on the root
    logger is the stdout sink (platform log driver / GKE filelog path).
    ``configure_telemetry`` may add a second sink (the OTLP
    ``LoggingHandler``) when OpenTelemetry is enabled — the JSON body stays
    the same, and the OTLP record carries the active span's trace_id.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(build_json_formatter())

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    logger = logging.getLogger(service_name)
    logger.setLevel(level)
    logger.propagate = True
    return logger


def _build_tracer_provider(
    resource: Resource, span_exporter: Optional[SpanExporter]
) -> TracerProvider:
    provider = TracerProvider(resource=resource)
    exporter = span_exporter or OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT),
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _build_meter_provider(
    resource: Resource, metric_reader: Optional[MetricReader]
) -> MeterProvider:
    reader = metric_reader or PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT),
            insecure=True,
        ),
        export_interval_millis=5000,
    )
    return MeterProvider(resource=resource, metric_readers=[reader])


def _build_logger_provider(
    resource: Resource, log_exporter: Optional[LogExporter]
) -> LoggerProvider:
    """LoggerProvider that ships every LogRecord to the collector over OTLP.

    ``BatchLogRecordProcessor`` keeps the hot path cheap (the HTTP request
    never waits on the network). ``set_logger_provider`` makes the provider
    global so ``LoggingHandler`` and tests can resolve it without imports of
    the SDK internals.
    """
    provider = LoggerProvider(resource=resource)
    exporter = log_exporter or OTLPLogExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT),
        insecure=True,
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)
    return provider


class _SkipOtelInternals(logging.Filter):
    """Drop records emitted by the OpenTelemetry stack itself.

    The OTLP exporters log their own transport failures through stdlib
    logging; re-exporting those records would feed more failed exports
    (feedback loop when the collector is unreachable). Application logs
    are untouched — exporter transport noise is not an app signal.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry.")


def configure_telemetry(
    service_name: str,
    *,
    span_exporter: Optional[SpanExporter] = None,
    metric_reader: Optional[MetricReader] = None,
    log_exporter: Optional[LogExporter] = None,
) -> tuple:
    """Configure traces, metrics and logs for one service.

    Args:
        service_name: Value used for ``service.name`` and the tracer/meter name.
        span_exporter: Optional exporter override (tests use in-memory).
        metric_reader: Optional metric reader override (tests use in-memory).
        log_exporter: Optional log exporter override (tests use in-memory).

    Returns:
        ``(tracer, meter, logger)``. When ``OTEL_ENABLED`` is false the tracer
        and meter are no-ops (no exporter is created for any signal).
    """
    logger = configure_logging(service_name)

    if not is_otel_enabled():
        logger.info("OpenTelemetry disabled via OTEL_ENABLED=false")
        return trace.get_tracer(service_name), metrics.get_meter(service_name), logger

    resource = _build_resource(service_name)
    trace.set_tracer_provider(_build_tracer_provider(resource, span_exporter))
    metrics.set_meter_provider(_build_meter_provider(resource, metric_reader))

    # Second log sink: OTLP export with the active span context (A5/A11).
    # stdout keeps the structlog JSON line (platform driver / filelog path).
    otlp_handler = LoggingHandler(
        logger_provider=_build_logger_provider(resource, log_exporter)
    )
    otlp_handler.addFilter(_SkipOtelInternals())
    logging.getLogger().addHandler(otlp_handler)

    return (
        trace.get_tracer(service_name),
        metrics.get_meter(service_name),
        logger,
    )
