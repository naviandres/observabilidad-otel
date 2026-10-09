"""Business metrics for service-a (PLAN 1.D).

The only labels used are low-cardinality ones (``payment.provider``,
``db.system``). ``order.id``, ``trace_id`` and ``user`` must never become a
metric label (verified by ``tests/test_cardinality.py``).

Instruments are created from a meter once, at startup, via
:func:`configure_metrics`; the rest of the code reads them through
:func:`get_metrics` so there is a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram

METER_NAME = "service-a"


@dataclass(frozen=True)
class BusinessMetrics:
    """Container for the service-a business metric instruments."""

    orders_created_total: Counter
    orders_failed_total: Counter
    payment_authorization_failures_total: Counter
    db_query_errors_total: Counter
    order_total_amount: Histogram
    payment_authorization_duration: Histogram


_metrics: Optional[BusinessMetrics] = None


def configure_metrics(meter: Optional[object] = None) -> BusinessMetrics:
    """Create the business instruments and store them as the singletons.

    Args:
        meter: Meter to bind the instruments to. Defaults to the global meter
            from the current ``MeterProvider``.

    Returns:
        The freshly created :class:`BusinessMetrics`.
    """
    global _metrics
    active_meter = meter or metrics.get_meter(METER_NAME)
    _metrics = BusinessMetrics(
        orders_created_total=active_meter.create_counter(
            name="orders_created_total",
            unit="1",
            description="Number of orders successfully created",
        ),
        orders_failed_total=active_meter.create_counter(
            name="orders_failed_total",
            unit="1",
            description="Number of orders that failed to complete",
        ),
        payment_authorization_failures_total=active_meter.create_counter(
            name="payment_authorization_failures_total",
            unit="1",
            description="Number of payment authorizations rejected or failed",
        ),
        db_query_errors_total=active_meter.create_counter(
            name="db_query_errors_total",
            unit="1",
            description="Number of database errors while persisting orders",
        ),
        order_total_amount=active_meter.create_histogram(
            name="order_total_amount",
            unit="1",
            description="Distribution of order total amounts",
        ),
        payment_authorization_duration=active_meter.create_histogram(
            name="payment_authorization_duration",
            unit="s",
            description="Duration of the payment authorization call, in seconds",
        ),
    )
    return _metrics


def get_metrics() -> Optional[BusinessMetrics]:
    """Return the configured instruments, or ``None`` before startup."""
    return _metrics
