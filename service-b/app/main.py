"""service-b HTTP entrypoint (Inventory Service).

Thin FastAPI layer: telemetry setup, auto-instrumentation gated by
``OTEL_ENABLED`` and the two endpoints. The business flow and its custom spans
(``inventory.reserve``, ``inventory.query`` and ``payment.gateway_call``,
PLAN 1.D) live in ``app.inventory``.
"""

from fastapi import FastAPI, Request
from fastapi.responses import Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST as OPENMETRICS_CONTENT_TYPE
from prometheus_client.openmetrics.exposition import generate_latest as generate_latest_openmetrics

from app.database import engine
from app.inventory import reserve_inventory
from app.latency import observe_business_request, timer
from app.telemetry import configure_telemetry, is_otel_enabled

tracer, meter, logger = configure_telemetry("service-b")

app = FastAPI(title="Inventory Service")

if is_otel_enabled():
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)


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
# POST /inventory/reserve
# ============================================================


@app.post("/inventory/reserve")
async def reserve_endpoint(request: dict):
    started = timer()
    outcome = "error"
    try:
        result = await reserve_inventory(request["items"], engine=engine)
        outcome = "success"
        return result
    finally:
        # A8 bridge: one observation per request with service-b's own span ids.
        observe_business_request("service-b", outcome, started)
