"""App-level latency bridge with OTel exemplars (PLAN §3.A assertion 8).

Why this module exists (full ADR in ``docs/aws-ecs.md``): the collector's
Prometheus exporter (ADOT v0.46 / contrib 0.162) does NOT propagate OTLP
exemplars into its scrape (verified live 2026-10-12). A8 is therefore closed
at the application level: this native ``prometheus_client`` histogram is
observed once per business request with an exemplar carrying the ACTIVE
OpenTelemetry trace/span ids, and ``GET /metrics`` serves it in OpenMetrics —
the only exposition format that can carry exemplars (the Prometheus text
format silently drops them).

This histogram is the exemplar-to-trace bridge ONLY. The OTel business
metrics keep flowing over OTLP for A6/A7/A12; nothing here replaces them.

Labels stay deliberately low-cardinality (``service_name``, ``outcome``):
``trace_id``/``span_id`` live in the EXEMPLAR, never in a label — that is what
makes metric-to-trace correlation cost nothing on the cardinality axis
(verified by ``tests/test_cardinality.py`` and the A9 series limit).
"""

from __future__ import annotations

import time
from typing import Optional

from opentelemetry import trace
from prometheus_client import Histogram

HISTOGRAM_NAME = "http_server_duration_milliseconds"

#: One histogram per service, same name and label contract in both (parity
#: verified byte-for-byte by ``tests/test_exemplar_metrics.py``). 11 buckets
#: from 5 ms to 10 s cover the simulated flow's observed latencies.
HISTOGRAM = Histogram(
    HISTOGRAM_NAME,
    "End-to-end duration of the business HTTP request in milliseconds",
    labelnames=("service_name", "outcome"),
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
)


def timer() -> float:
    """Start a monotonic stopwatch for one business request."""
    return time.perf_counter()


def current_span_exemplar() -> Optional[dict[str, str]]:
    """Return the active span's ids as a ``prometheus_client`` exemplar dict.

    ``{"trace_id": <32 lowercase hex>, "span_id": <16 lowercase hex>}`` when a
    valid span context is active; ``None`` otherwise (e.g.
    ``OTEL_ENABLED=false``), so no misleading all-zero exemplar is emitted.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def observe_business_request(service_name: str, outcome: str, started: float) -> None:
    """Observe the milliseconds elapsed since ``timer()`` on the bridge histogram.

    Args:
        service_name: Value of the ``service_name`` label.
        outcome: Value of the ``outcome`` label (``success`` or ``error``);
            two values only, low cardinality by design.
        started: Value returned by :func:`timer` at request entry.
    """
    duration_ms = (time.perf_counter() - started) * 1000.0
    child = HISTOGRAM.labels(service_name, outcome)
    exemplar = current_span_exemplar()
    if exemplar is not None:
        child.observe(duration_ms, exemplar=exemplar)
    else:
        child.observe(duration_ms)
