"""PLAN 1.E: the business metric cardinality stays within budget and no label
ever contains a high-cardinality value (``order.id``, ``trace_id``, ``user``).
"""

from __future__ import annotations

from app import metrics as metrics_module

BUSINESS_METRICS = {
    "orders_created_total",
    "orders_failed_total",
    "payment_authorization_failures_total",
    "db_query_errors_total",
    "order_total_amount",
    "payment_authorization_duration",
}

FORBIDDEN_LABELS = {"order.id", "trace_id", "user"}

MAX_SERIES = 10


def test_business_metric_count_and_labels(metric_reader):
    instruments = metrics_module.configure_metrics()

    instruments.orders_created_total.add(1)
    instruments.orders_failed_total.add(1)
    instruments.payment_authorization_failures_total.add(1, {"payment.provider": "pse"})
    instruments.db_query_errors_total.add(1, {"db.system": "postgresql"})
    instruments.order_total_amount.record(2500000)
    instruments.payment_authorization_duration.record(0.05, {"payment.provider": "pse"})

    names = set()
    label_keys = set()

    data = metric_reader.get_metrics_data()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                names.add(metric.name)
                for point in metric.data.data_points:
                    attributes = getattr(point, "attributes", None) or {}
                    label_keys.update(attributes.keys())

    assert BUSINESS_METRICS.issubset(names)
    assert len(names) <= MAX_SERIES
    assert label_keys.isdisjoint(FORBIDDEN_LABELS)
