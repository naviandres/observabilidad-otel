"""Inventory business flow for service-b (PLAN 1.D).

Emits the custom spans ``inventory.reserve``, ``inventory.query`` and
``payment.gateway_call``. The gateway call is **simulated** with a deterministic
latency and never reaches an external SaaS. Isolating the flow here keeps
``main.py`` a thin HTTP layer and makes the spans testable offline.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from opentelemetry import trace
from sqlalchemy import text

tracer = trace.get_tracer("service-b")
logger = logging.getLogger("service-b")

QUERY_LATENCY_SECONDS = 0.020
GATEWAY_LATENCY_SECONDS = 0.030


async def reserve_inventory(
    items: Sequence[Any],
    engine: Any,
    tracer_: Optional[Any] = None,
) -> dict:
    """Reserve stock for the given items, emitting service-b's custom spans.

    Args:
        items: Items as mappings exposing ``sku`` and ``quantity``.
        engine: SQLAlchemy engine for the inventory database.
        tracer_: Optional tracer override (tests inject the in-memory tracer).

    Returns:
        ``{"reserved": True}`` on success, or
        ``{"reserved": False, "reason": ...}`` when a SKU is missing or the
        stock is insufficient.
    """
    active_tracer = tracer_ or globals()["tracer"]

    with active_tracer.start_as_current_span("inventory.reserve") as reserve_span:
        reserve_span.set_attribute("inventory.sku_count", len(items))

        with active_tracer.start_as_current_span("inventory.query") as query_span:
            query_span.set_attribute("db.system", "postgresql")
            await asyncio.sleep(QUERY_LATENCY_SECONDS)

            with engine.begin() as connection:
                for item in items:
                    result = connection.execute(
                        text(
                            """
                            SELECT quantity
                            FROM inventory
                            WHERE sku = :sku
                            FOR UPDATE
                            """
                        ),
                        {"sku": item["sku"]},
                    )

                    row = result.fetchone()

                    if row is None:
                        return {
                            "reserved": False,
                            "reason": f"SKU {item['sku']} not found",
                        }

                    current_quantity = row[0]

                    if current_quantity < item["quantity"]:
                        return {
                            "reserved": False,
                            "reason": f"Insufficient stock for {item['sku']}",
                        }

                for item in items:
                    connection.execute(
                        text(
                            """
                            UPDATE inventory
                            SET quantity = quantity - :quantity
                            WHERE sku = :sku
                            """
                        ),
                        {
                            "sku": item["sku"],
                            "quantity": item["quantity"],
                        },
                    )

        with active_tracer.start_as_current_span(
            "payment.gateway_call"
        ) as gateway_span:
            gateway_span.set_attribute("payment.provider", "simulated")
            await asyncio.sleep(GATEWAY_LATENCY_SECONDS)
            gateway_span.set_attribute("payment.result", "approved")

        logger.info("Inventory successfully reserved")

        return {"reserved": True}
