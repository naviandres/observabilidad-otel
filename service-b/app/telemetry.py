import json
import logging
from datetime import datetime, timezone

from opentelemetry import trace

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

#from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
#prueba local
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


class TraceIdFilter(logging.Filter):

    def filter(self, record):

        span = trace.get_current_span()
        context = span.get_span_context()

        if context.is_valid:

            record.trace_id = format(
                context.trace_id,
                "032x"
            )

            record.span_id = format(
                context.span_id,
                "016x"
            )

        else:

            record.trace_id = None
            record.span_id = None

        return True


class JsonFormatter(logging.Formatter):

    def format(self, record):
        log = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),

            "level": record.levelname,

            "service": record.name,

            "message": record.getMessage(),

            "trace_id": getattr(
                record,
                "trace_id",
                None
            ),

            "span_id": getattr(
                record,
                "span_id",
                None
            )
        }

        return json.dumps(log)


def configure_telemetry(service_name: str):
    resource = Resource.create({

        "service.name": service_name,

        "service.version": "1.0.0",

        "deployment.environment": "development"
    })

    provider = TracerProvider(
        resource=resource
    )

   # exporter = OTLPSpanExporter(
    #    endpoint="http://otel-collector:4317",
     #   insecure=True
    #)

    exporter = ConsoleSpanExporter()


    processor = BatchSpanProcessor(
        exporter
    )

    provider.add_span_processor(
        processor
    )

    trace.set_tracer_provider(
        provider
    )

    # --------------------------------------------------
    # Logger
    # --------------------------------------------------

    logger = logging.getLogger(
        service_name
    )

    logger.setLevel(
        logging.INFO
    )

    if not logger.handlers:
        handler = logging.StreamHandler()

        handler.setFormatter(
            JsonFormatter()
        )

        handler.addFilter(
            TraceIdFilter()
        )

        logger.addHandler(
            handler
        )

    tracer = trace.get_tracer(
        service_name
    )

    return tracer, logger
