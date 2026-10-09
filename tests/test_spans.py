"""PLAN 1.E: the four custom spans exist with their attributes and the failure
path is marked ``ERROR``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry.trace import StatusCode

from app import metrics as metrics_module
from app import orders, payment, persistence
from app.service import InventoryClient

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeConnection:
    def execute(self, *args, **kwargs):
        return SimpleNamespace(fetchone=lambda: None)


class _FakeEngine:
    """SQLAlchemy engine stand-in that never touches a database."""

    def begin(self):
        return self

    def __enter__(self):
        return _FakeConnection()

    def __exit__(self, *exc):
        return False


class _BrokenEngine:
    def begin(self):
        raise RuntimeError("database unavailable")


def _inventory_client():
    def handler(request):
        return httpx.Response(200, json={"reserved": True})

    return InventoryClient("http://service-b:8001", transport=httpx.MockTransport(handler))


class _FakeRequest:
    def __init__(self, items, payment_provider="mock-payment"):
        self.items = items
        self.payment_provider = payment_provider


def _spans_by_name(exporter):
    return {span.name: span for span in exporter.get_finished_spans()}


def _metric_points(reader, name):
    points = []
    data = reader.get_metrics_data()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def test_custom_spans_happy_path(span_exporter, metric_reader):
    metrics_module.configure_metrics()
    request = _FakeRequest([SimpleNamespace(sku="LAPTOP-001", quantity=2, unit_price=2500000)])

    result = asyncio.run(
        orders.process_order(request, _inventory_client(), engine=_FakeEngine())
    )

    spans = _spans_by_name(span_exporter)
    for name in (
        "order.process",
        "order.validate",
        "inventory.reserve",
        "payment.authorize",
        "order.persist",
    ):
        assert name in spans, f"missing custom span: {name}"

    validate = spans["order.validate"]
    assert validate.attributes["order.item_count"] == 1
    assert validate.attributes["order.total_amount"] == 5000000

    assert spans["inventory.reserve"].attributes["inventory.sku_count"] == 1

    authorize = spans["payment.authorize"]
    assert authorize.attributes["payment.provider"] == "mock-payment"
    assert authorize.attributes["payment.amount"] == 5000000
    assert authorize.attributes["payment.result"] == "approved"
    assert any(event.name == "order.id" for event in authorize.events)

    assert spans["order.persist"].attributes["db.system"] == "postgresql"
    assert result["status"] == "created"


def test_validate_failure_marks_span_error(span_exporter):
    with pytest.raises(ValueError):
        orders.validate_order([])

    span = _spans_by_name(span_exporter)["order.validate"]
    assert span.status.status_code == StatusCode.ERROR


def test_payment_failure_marks_span_error_and_counter(span_exporter, metric_reader):
    metrics_module.configure_metrics()

    result = asyncio.run(
        payment.authorize_payment(provider="fail", amount=100, order_id="order-1")
    )

    assert result == "rejected"
    span = _spans_by_name(span_exporter)["payment.authorize"]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["payment.result"] == "rejected"

    total = sum(p.value for p in _metric_points(metric_reader, "payment_authorization_failures_total"))
    assert total == 1


def test_persist_failure_marks_span_error_and_counter(span_exporter, metric_reader):
    metrics_module.configure_metrics()
    item = SimpleNamespace(sku="LAPTOP-001", quantity=1, unit_price=100)

    with pytest.raises(RuntimeError):
        persistence.persist_order("order-1", [item], 100, engine=_BrokenEngine())

    span = _spans_by_name(span_exporter)["order.persist"]
    assert span.status.status_code == StatusCode.ERROR

    total = sum(p.value for p in _metric_points(metric_reader, "db_query_errors_total"))
    assert total == 1


def test_inventory_client_span_attribute(span_exporter):
    import httpx

    def handler(request):
        return httpx.Response(200, json={"reserved": True})

    client = InventoryClient("http://service-b:8001", transport=httpx.MockTransport(handler))
    items = [SimpleNamespace(sku="A", quantity=1), SimpleNamespace(sku="B", quantity=3)]

    asyncio.run(client.reserve(items))

    span = _spans_by_name(span_exporter)["inventory.reserve"]
    assert span.attributes["inventory.sku_count"] == 2


# ---------------------------------------------------------------------------
# service-b custom spans (PLAN 1.D): inventory.query and payment.gateway_call
# ---------------------------------------------------------------------------


class _FakeInventoryConnection:
    """Connection stand-in that always reports enough stock."""

    def execute(self, *args, **kwargs):
        return SimpleNamespace(fetchone=lambda: (10,))


class _FakeInventoryEngine:
    """SQLAlchemy engine stand-in that never touches a database."""

    def begin(self):
        return self

    def __enter__(self):
        return _FakeInventoryConnection()

    def __exit__(self, *exc):
        return False


def _load_service_b_inventory():
    """Load service-b's flow by path (both services use the ``app`` package).

    Loading it here, after the in-memory provider is installed, gives the
    module a concrete tracer instead of a stale ``ProxyTracer``.
    """
    path = REPO_ROOT / "service-b" / "app" / "inventory.py"
    spec = importlib.util.spec_from_file_location("service_b_inventory", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["service_b_inventory"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("service_b_inventory", None)
    return module


def test_service_b_custom_spans(span_exporter):
    inventory = _load_service_b_inventory()

    result = asyncio.run(
        inventory.reserve_inventory(
            [{"sku": "LAPTOP-001", "quantity": 2}],
            engine=_FakeInventoryEngine(),
        )
    )

    spans = _spans_by_name(span_exporter)
    for name in ("inventory.reserve", "inventory.query", "payment.gateway_call"):
        assert name in spans, f"missing service-b custom span: {name}"

    assert spans["inventory.reserve"].attributes["inventory.sku_count"] == 1
    assert spans["inventory.query"].attributes["db.system"] == "postgresql"

    gateway = spans["payment.gateway_call"]
    assert gateway.attributes["payment.provider"] == "simulated"
    assert gateway.attributes["payment.result"] == "approved"
    assert gateway.status.status_code == StatusCode.UNSET

    assert result == {"reserved": True}
