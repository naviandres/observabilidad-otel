import asyncio

from opentelemetry import trace


tracer = trace.get_tracer("service-a")


async def authorize_payment(
        provider: str,
        amount: float,
        order_id: str
):

    with tracer.start_as_current_span(
            "payment.authorize"
    ) as span:

        span.set_attribute(
            "payment.provider",
            provider
        )

        span.set_attribute(
            "payment.amount",
            amount
        )

        # Simulación determinista
        await asyncio.sleep(0.050)

        result = "approved"

        span.set_attribute(
            "payment.result",
            result
        )

        # order.id como evento del span
        span.add_event(
            "order.id",
            {
                "order.id": order_id
            }
        )

        return result