import httpx

from opentelemetry import trace

tracer = trace.get_tracer("service-a")


class InventoryClient:

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def reserve(self, items):
        with tracer.start_as_current_span(
                "inventory.reserve"
        ) as span:
            span.set_attribute(
                "inventory.sku_count",
                len(items)
            )

            payload = {
                "items": [
                    {
                        "sku": item.sku,
                        "quantity": item.quantity
                    }
                    for item in items
                ]
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/inventory/reserve",
                    json=payload
                )

                response.raise_for_status()

                return response.json()
