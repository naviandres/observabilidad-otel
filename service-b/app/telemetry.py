import logging
import json
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
#from opentelemetry.sdk.trace.export import ConsoleSpanExporter


class TraceIdFilter(logging.Filter):

    def filter(self, record):
        span = trace.get_current_span()
        context = span.get_span_context()

        if context.is_valid:
            record.trace_id = format(context.trace_id, "032x")
            record.span_id = format(context.span_id, "016x")
        else:
            record.trace_id = None
            record.span_id = None

        return True


class JsonFormatter(logging.Formatter):

    def format(self, record):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None)
        }

        return json.dumps(log)


def configure_telemetry(service_name: str):
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": "development"
    })

    # TRACES
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(
        endpoint="http://otel-collector:4317",
        insecure=True
    )
    # exporter = ConsoleSpanExporter()

    provider.add_span_processor(
        BatchSpanProcessor(exporter)
    )

    trace.set_tracer_provider(provider)

    # METRICS (Configurado con OTLPMetricExporter para enviar al Colector)
    metric_exporter = OTLPMetricExporter(
        endpoint="http://otel-collector:4317",
        insecure=True
    )
    
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=5000  # Intervalo de exportación de métricas en milisegundos
    )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            metric_reader
        ]
    )

    metrics.set_meter_provider(
        meter_provider
    )

    # Logger
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()

        handler.setFormatter(
            JsonFormatter()
        )

        handler.addFilter(
            TraceIdFilter()
        )

        logger.addHandler(handler)

    tracer = trace.get_tracer(service_name)
    meter = metrics.get_meter(service_name)

    return tracer, meter, logger