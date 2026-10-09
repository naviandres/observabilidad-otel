"""Order processing orchestration for service-a.

Holds the business flow and its custom spans (PLAN 1.D):
``order.validate``, ``inventory.reserve`` (client, in ``service.py``),
``payment.authorize`` (in ``payment.py``) and ``order.persist``
(in ``persistence.py``). Isolating the flow here keeps ``main.py`` a thin
HTTP layer and makes the flow testable offline.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Sequence

from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.metrics import BusinessMetrics, get_metrics
from app.payment import authorize_payment
from app.persistence import persist_order

tracer = trace.get_tracer("service-a")
logger = logging.getLogger("service-a")


def validate_order(items: Sequence[Any], tracer: Optional[Any] = None) -> tuple:
    """Validate an order and emit the ``order.validate`` span.

    Args:
        items: Order items, each exposing ``quantity`` and ``unit_price``.
        tracer: Optional tracer override (tests inject the in-memory tracer).

    Returns:
        ``(item_count, total_amount)`` for the given items.

    Raises:
        ValueError: If the order has no items. The span is marked ``ERROR``
            and the exception is recorded on it before raising.
    """
    active_tracer = tracer or globals()["tracer"]
    item_count = len(items)
    total_amount = sum(item.quantity * item.unit_price for item in items)

    with active_tracer.start_as_current_span("order.validate") as span:
        span.set_attribute("order.item_count", item_count)
        span.set_attribute("order.total_amount", total_amount)

        if item_count == 0:
            error = ValueError("Order must contain items")
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, "Order without items"))
            raise error

    return item_count, total_amount


def _record_order_failure(metrics: Optional[BusinessMetrics]) -> None:
    """Increment ``orders_failed_total`` when metrics are configured."""
    if metrics is not None:
        metrics.orders_failed_total.add(1)


async def process_order(request: Any, inventory_client: Any, engine: Any = None) -> dict:
    """Run the full ``POST /orders`` flow under one root span.

    Args:
        request: Order request exposing ``items`` and ``payment_provider``.
        inventory_client: Client with an async ``reserve(items)`` method.
        engine: Optional SQLAlchemy engine override for persistence (tests).

    Returns:
        The response payload for a successfully created order.

    Raises:
        HTTPException: 400 for an invalid order, 409 for insufficient
            inventory, 402 for a rejected payment and 503 when service-b is
            unreachable.
    """
    metrics = get_metrics()
    order_id = str(uuid.uuid4())

    with tracer.start_as_current_span("order.process") as root_span:
        try:
            item_count, total_amount = validate_order(request.items)
            _ = item_count

            try:
                inventory_result = await inventory_client.reserve(request.items)
            except Exception as exc:
                logger.error("Inventory service unavailable: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="Inventory service unavailable",
                ) from exc

            if not inventory_result.get("reserved", False):
                raise HTTPException(
                    status_code=409,
                    detail=inventory_result.get("reason", "Insufficient inventory"),
                )

            payment_result = await authorize_payment(
                provider=request.payment_provider,
                amount=total_amount,
                order_id=order_id,
            )

            if payment_result != "approved":
                raise HTTPException(status_code=402, detail="Payment rejected")

            persist_order(
                order_id=order_id,
                items=request.items,
                total_amount=total_amount,
                engine=engine,
            )

            if metrics is not None:
                metrics.orders_created_total.add(1)
                metrics.order_total_amount.record(total_amount)

            logger.info("Order completed: %s", order_id)

            return {
                "order_id": order_id,
                "status": "created",
                "total_amount": total_amount,
                "inventory": inventory_result,
                "payment": payment_result,
            }

        except ValueError as exc:
            _record_order_failure(metrics)
            root_span.record_exception(exc)
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        except HTTPException:
            _record_order_failure(metrics)
            if root_span.is_recording():
                root_span.set_status(Status(StatusCode.ERROR, "order failed"))
            raise

        except Exception as exc:
            _record_order_failure(metrics)
            root_span.record_exception(exc)
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
