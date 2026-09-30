import uuid

from fastapi import FastAPI, HTTPException

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from app.telemetry import configure_telemetry
from app.models import CreateOrderRequest
from app.service import InventoryClient
from app.payment import authorize_payment
from app.database import engine
from app.persistence import persist_order

# ============================================================
# OpenTelemetry
# ============================================================

tracer, logger = configure_telemetry("service-a")

# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="Order Service",
    version="1.0.0"
)

# ============================================================
# OpenTelemetry instrumentation
# ============================================================

FastAPIInstrumentor.instrument_app(app)

HTTPXClientInstrumentor().instrument()

SQLAlchemyInstrumentor().instrument(
    engine=engine
)

# ============================================================
# Service B
# ============================================================

inventory_client = InventoryClient(
    "http://localhost:8001"
)


# ============================================================
# POST /orders
# ============================================================

@app.post("/orders")
async def create_order(
        request: CreateOrderRequest
):
    # Span principal de procesamiento
    with tracer.start_as_current_span(
            "order.process"
    ) as root_span:

        # ----------------------------------------------------
        # Generar ID de orden
        # ----------------------------------------------------

        order_id = str(uuid.uuid4())

        # ----------------------------------------------------
        # Calcular información de la orden
        # ----------------------------------------------------

        item_count = len(request.items)

        total_amount = sum(
            item.quantity * item.unit_price
            for item in request.items
        )

        # ----------------------------------------------------
        # 1. VALIDAR ORDEN
        # ----------------------------------------------------

        with tracer.start_as_current_span(
                "order.validate"
        ) as span:

            span.set_attribute(
                "order.item_count",
                item_count
            )

            span.set_attribute(
                "order.total_amount",
                total_amount
            )

            if item_count == 0:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR,
                        "Order without items"
                    )
                )

                raise HTTPException(
                    status_code=400,
                    detail="Order must contain items"
                )

        logger.info(
            "Order validated: %s",
            order_id
        )

        # ----------------------------------------------------
        # 2. RESERVAR INVENTARIO
        # ----------------------------------------------------

        try:

            inventory_result = (
                await inventory_client.reserve(
                    request.items
                )
            )

        except Exception as exception:

            logger.error(
                "Inventory service unavailable: %s",
                exception
            )

            raise HTTPException(
                status_code=503,
                detail="Inventory service unavailable"
            )

        if not inventory_result.get("reserved", False):
            raise HTTPException(
                status_code=409,
                detail=inventory_result.get(
                    "reason",
                    "Insufficient inventory"
                )
            )

        # ----------------------------------------------------
        # 3. AUTORIZAR PAGO
        # ----------------------------------------------------

        payment_result = await authorize_payment(
            provider=request.payment_provider,
            amount=total_amount,
            order_id=order_id
        )

        if payment_result != "approved":
            raise HTTPException(
                status_code=402,
                detail="Payment rejected"
            )

        # ----------------------------------------------------
        # 4. PERSISTIR ORDEN
        # ----------------------------------------------------

        persist_order(
            order_id=order_id,
            items=request.items,
            total_amount=total_amount
        )

        # ----------------------------------------------------
        # LOG FINAL
        # ----------------------------------------------------

        logger.info(
            "Order completed: %s",
            order_id
        )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return {
            "order_id": order_id,
            "status": "created",
            "total_amount": total_amount,
            "inventory": inventory_result,
            "payment": payment_result
        }
