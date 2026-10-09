"""HTTP client for service-b's inventory API.

Emits the client-side ``inventory.reserve`` span (PLAN 1.D) with the
``inventory.sku_count`` attribute. A ``transport`` can be injected so tests
exercise the client against a mock transport without touching the network.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import httpx
from opentelemetry import trace

tracer = trace.get_tracer("service-a")


class InventoryClient:
    """Async client for service-b's ``/inventory/reserve`` endpoint."""

    def __init__(self, base_url: str, transport: Optional[Any] = None):
        self.base_url = base_url
        self._transport = transport

    async def reserve(self, items: Sequence[Any]) -> dict:
        """Reserve stock for the given items.

        Args:
            items: Items exposing ``sku`` and ``quantity``.

        Returns:
            The parsed JSON response from service-b.
        """
        with tracer.start_as_current_span("inventory.reserve") as span:
            span.set_attribute("inventory.sku_count", len(items))

            payload = {
                "items": [
                    {"sku": item.sku, "quantity": item.quantity} for item in items
                ]
            }

            async with httpx.AsyncClient(transport=self._transport) as client:
                response = await client.post(
                    f"{self.base_url}/inventory/reserve",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
