"""service-a HTTP entrypoint (Order Service).

Thin FastAPI layer: telemetry/metrics setup, auto-instrumentation gated by
``OTEL_ENABLED``, and the two endpoints. The business flow lives in
``app.orders``.
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST as OPENMETRICS_CONTENT_TYPE
from prometheus_client.openmetrics.exposition import generate_latest as generate_latest_openmetrics

from app.database import engine
from app.latency import observe_business_request, timer
from app.metrics import configure_metrics
from app.models import CreateOrderRequest
from app.orders import process_order
from app.service import InventoryClient
from app.telemetry import configure_telemetry, is_otel_enabled

# ============================================================
# OpenTelemetry (traces, metrics, logs)
# ============================================================

tracer, meter, logger = configure_telemetry("service-a")
configure_metrics(meter)

# ============================================================
# FastAPI
# ============================================================

app = FastAPI(title="Order Service", version="1.0.0")

# ============================================================
# Automatic instrumentation (only when OTel is enabled)
# ============================================================

if is_otel_enabled():
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine)

# ============================================================
# Service B
# ============================================================

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://service-b:8001")

inventory_client = InventoryClient(INVENTORY_SERVICE_URL)


# ============================================================
# GET /metrics
# ============================================================

# A8 bridge (app/latency.py): the exemplars that link metric bucket -> trace
# only exist in the OpenMetrics exposition, so the endpoint negotiates on the
# Accept header. Clients that do not ask for OpenMetrics (health check, plain
# curl) keep receiving the default text format, exactly as before.
OPENMETRICS_ACCEPT = "application/openmetrics-text"


@app.get("/metrics")
def metrics_endpoint(request: Request):
    if OPENMETRICS_ACCEPT in request.headers.get("accept", ""):
        return Response(
            content=generate_latest_openmetrics(REGISTRY),
            media_type=OPENMETRICS_CONTENT_TYPE,
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# POST /orders
# ============================================================


def _active_trace_id() -> str | None:
    """Return the W3C trace id (32 lowercase hex) of the active span context.

    Read from the OpenTelemetry SDK context — the application never touches
    ``trace_id`` itself (conventions §Observabilidad). ``None`` when no valid
    span context is active (e.g. ``OTEL_ENABLED=false``), so no misleading
    all-zero header is emitted.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


@app.post("/orders")
async def create_order(request: CreateOrderRequest, response: Response):
    started = timer()
    outcome = "error"
    try:
        trace_id = _active_trace_id()
        if trace_id is not None:
            # PLAN §3.A assertion 2: edge-to-edge W3C trace id in the response.
            response.headers["x-trace-id"] = trace_id
        result = await process_order(request, inventory_client, engine=engine)
        outcome = "success"
        return result
    finally:
        # A8 bridge: one observation per request, exemplar = active OTel ids.
        observe_business_request("service-a", outcome, started)
