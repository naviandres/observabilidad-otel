"""Order persistence for service-a.

Emits the ``order.persist`` span with the mandatory ``db.system`` attribute
(PLAN 1.D). On a database error the span is marked ``ERROR``, the exception is
recorded and ``db_query_errors_total`` is incremented with the low-cardinality
``db.system`` label.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import text

from app.database import engine as default_engine
from app.metrics import get_metrics

tracer = trace.get_tracer("service-a")
logger = logging.getLogger("service-a")


def persist_order(
    order_id: str,
    items: Sequence[Any],
    total_amount: float,
    engine: Optional[Any] = None,
) -> None:
    """Persist an order and its items inside a single transaction.

    Args:
        order_id: Order identifier.
        items: Items exposing ``sku``, ``quantity`` and ``unit_price``.
        total_amount: Order total.
        engine: Optional SQLAlchemy engine override (tests inject a stub).

    Raises:
        Exception: Propagates any database error after marking the span
            ``ERROR`` and incrementing ``db_query_errors_total``.
    """
    active_engine = engine or default_engine

    with tracer.start_as_current_span("order.persist") as span:
        span.set_attribute("db.system", "postgresql")

        try:
            with active_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO orders (id, total_amount)
                        VALUES (:id, :total)
                        """
                    ),
                    {"id": order_id, "total": total_amount},
                )

                for item in items:
                    connection.execute(
                        text(
                            """
                            INSERT INTO order_items
                                (order_id, sku, quantity, unit_price)
                            VALUES
                                (:order_id, :sku, :quantity, :unit_price)
                            """
                        ),
                        {
                            "order_id": order_id,
                            "sku": item.sku,
                            "quantity": item.quantity,
                            "unit_price": item.unit_price,
                        },
                    )
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            metrics = get_metrics()
            if metrics is not None:
                metrics.db_query_errors_total.add(1, {"db.system": "postgresql"})
            logger.error("Failed to persist order %s: %s", order_id, exc)
            raise
