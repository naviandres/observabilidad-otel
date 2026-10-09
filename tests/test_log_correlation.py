"""PLAN 1.E: every log line emitted inside an active span carries
``trace_id``/``span_id`` matching that span, injected by the OTel-aware
structlog processor (never by application code).
"""

from __future__ import annotations

import io
import json
import logging

from opentelemetry import trace

from app import telemetry


def test_log_line_carries_active_span_ids(span_exporter):
    telemetry.configure_logging("service-a")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(telemetry.build_json_formatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    logger = logging.getLogger("service-a")

    tracer = trace.get_tracer("service-a")
    with tracer.start_as_current_span("order.process") as span:
        logger.info("Order completed")
        span_context = span.get_span_context()

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "expected at least one JSON log line"

    payload = json.loads(lines[-1])
    assert payload["trace_id"] == format(span_context.trace_id, "032x")
    assert payload["span_id"] == format(span_context.span_id, "016x")
    assert payload["event"] == "Order completed"
