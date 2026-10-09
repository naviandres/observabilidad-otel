"""service-a HTTP entrypoint (Order Service).

Thin FastAPI layer: telemetry/metrics setup, auto-instrumentation gated by
``OTEL_ENABLED``, and the two endpoints. The business flow lives in
``app.orders``.
"""

import os

from fastapi import FastAPI
from fastapi.responses import Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.database import engine
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


@app.get("/metrics")
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# POST /orders
# ============================================================


@app.post("/orders")
async def create_order(request: CreateOrderRequest):
    return await process_order(request, inventory_client, engine=engine)
