"""service-a must echo the active W3C trace id in the ``x-trace-id`` header.

PLAN §3.A assertion 2. Fully offline: the app is imported with
``OTEL_ENABLED=false`` (no OTLP exporter is created), then instrumented
manually with ``FastAPIInstrumentor`` against the in-memory provider from
``conftest``, with service-b and the database replaced by stubs.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.service import InventoryClient

ORDER_BODY = {
    "items": [{"sku": "LAPTOP-001", "quantity": 1, "unit_price": 2500000}],
    "payment_provider": "mock-payment",
}


@pytest.fixture(scope="module")
def main_module():
    """Import app.main with OTel disabled so no OTLP exporter touches network."""
    os.environ["OTEL_ENABLED"] = "false"
    try:
        import app.main as module

        return module
    finally:
        os.environ.pop("OTEL_ENABLED", None)


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


def _reserved_transport() -> httpx.MockTransport:
    def handler(request):
        return httpx.Response(200, json={"reserved": True})

    return httpx.MockTransport(handler)


@contextmanager
def _stubbed_flow(main_module, monkeypatch) -> Iterator[None]:
    """Replace the service-b client and the DB engine with offline stubs."""
    monkeypatch.setattr(main_module, "engine", _FakeEngine())
    monkeypatch.setattr(
        main_module,
        "inventory_client",
        InventoryClient("http://service-b:8001", transport=_reserved_transport()),
    )
    yield


def test_orders_response_header_equals_injected_trace_id(main_module, span_exporter, monkeypatch):
    """A2: the traceparent injected at the edge comes back in x-trace-id."""
    with _stubbed_flow(main_module, monkeypatch):
        FastAPIInstrumentor.instrument_app(main_module.app)
        try:
            client = TestClient(main_module.app)
            trace_id = secrets.token_hex(16)
            span_id = secrets.token_hex(8)
            response = client.post(
                "/orders",
                json=ORDER_BODY,
                headers={"traceparent": f"00-{trace_id}-{span_id}-01"},
            )
        finally:
            FastAPIInstrumentor.uninstrument_app(main_module.app)

    assert response.status_code == 200
    assert response.headers.get("x-trace-id") == trace_id


def test_orders_header_is_the_active_span_trace_id(main_module, span_exporter, monkeypatch):
    """Without an incoming traceparent the header carries the SDK-generated id."""
    with _stubbed_flow(main_module, monkeypatch):
        FastAPIInstrumentor.instrument_app(main_module.app)
        try:
            client = TestClient(main_module.app)
            response = client.post("/orders", json=ORDER_BODY)
            exported = span_exporter.get_finished_spans()
        finally:
            FastAPIInstrumentor.uninstrument_app(main_module.app)

    assert response.status_code == 200
    server_span = next(s for s in exported if s.name == "POST /orders")
    expected = format(server_span.context.trace_id, "032x")
    assert response.headers.get("x-trace-id") == expected


def test_no_header_when_no_valid_span_context(main_module, monkeypatch):
    """With OTel off (no active span) no misleading all-zero header is sent."""
    with _stubbed_flow(main_module, monkeypatch):
        client = TestClient(main_module.app)
        response = client.post("/orders", json=ORDER_BODY)

    assert response.status_code == 200
    assert "x-trace-id" not in response.headers
