"""service-b HTTP entrypoint (Inventory Service).

Thin FastAPI layer: telemetry setup, auto-instrumentation gated by
``OTEL_ENABLED`` and the two endpoints. The business flow and its custom spans
(``inventory.reserve``, ``inventory.query`` and ``payment.gateway_call``,
PLAN 1.D) live in ``app.inventory``.
"""

from fastapi import FastAPI
from fastapi.responses import Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.database import engine
from app.inventory import reserve_inventory
from app.telemetry import configure_telemetry, is_otel_enabled

tracer, meter, logger = configure_telemetry("service-b")

app = FastAPI(title="Inventory Service")

if is_otel_enabled():
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)


# ============================================================
# GET /metrics
# ============================================================


@app.get("/metrics")
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# POST /inventory/reserve
# ============================================================


@app.post("/inventory/reserve")
async def reserve_endpoint(request: dict):
    return await reserve_inventory(request["items"], engine=engine)
