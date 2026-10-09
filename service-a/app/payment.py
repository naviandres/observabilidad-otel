"""Payment authorization for service-a.

Emits the ``payment.authorize`` span (PLAN 1.D) plus the
``payment_authorization_duration`` histogram and, on rejection, the
``payment_authorization_failures_total`` counter. The only label is
``payment.provider`` (low cardinality).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.metrics import get_metrics

tracer = trace.get_tracer("service-a")
logger = logging.getLogger("service-a")


async def authorize_payment(
    provider: str,
    amount: float,
    order_id: str,
    tracer_: Optional[object] = None,
) -> str:
    """Authorize a payment and emit its span, duration and counters.

    The simulation is deterministic: provider ``"fail"`` always rejects, any
    other provider approves. No randomness keeps the benchmark reproducible.

    Args:
        provider: Payment provider name (also the metric label).
        amount: Order total amount charged.
        order_id: Order identifier, emitted as a span event.
        tracer_: Optional tracer override for tests.

    Returns:
        ``"approved"`` or ``"rejected"``.
    """
    active_tracer = tracer_ or globals()["tracer"]

    with active_tracer.start_as_current_span("payment.authorize") as span:
        span.set_attribute("payment.provider", provider)
        span.set_attribute("payment.amount", amount)

        # order.id como evento del span (nunca como label de métrica)
        span.add_event("order.id", {"order.id": order_id})

        start = time.perf_counter()
        await asyncio.sleep(0.050)  # simulación determinista
        result = "rejected" if provider == "fail" else "approved"
        duration_seconds = time.perf_counter() - start

        span.set_attribute("payment.result", result)

        metrics = get_metrics()
        labels = {"payment.provider": provider}
        if metrics is not None:
            metrics.payment_authorization_duration.record(duration_seconds, labels)

        if result != "approved":
            error = RuntimeError(f"Payment rejected by provider {provider}")
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, "payment rejected"))
            if metrics is not None:
                metrics.payment_authorization_failures_total.add(1, labels)
            logger.warning("Payment rejected: provider=%s", provider)

        return result
