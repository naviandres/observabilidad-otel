"""PLAN 1.E: service-a propagates the W3C ``traceparent`` to service-b.

The trace id is identical end to end. The ``parent_id`` seen by service-b is
the ``span_id`` of the HTTP client span that service-a emitted for the call,
which is itself a child of the ``inventory.reserve`` custom span. Fully
offline: the real httpx transport is instrumented and its connection pool is
replaced by a fake that captures the outgoing headers, so the exercised
injection path is exactly the production one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import SpanKind

from app.service import InventoryClient


class _FakeCoreResponse:
    """Minimal ``httpcore.Response`` stand-in."""

    status = 200
    headers = [(b"content-type", b"application/json")]
    extensions = {}

    def __init__(self):
        self.stream = self._body()

    async def _body(self):
        yield b'{"reserved": true}'


class _FakePool:
    """Fake httpcore pool that captures the outgoing request headers."""

    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def handle_async_request(self, request):
        self._captured["headers"] = {
            key.decode().lower(): value.decode() for key, value in request.headers
        }
        return _FakeCoreResponse()


def test_traceparent_propagated_to_service_b(span_exporter):
    captured: dict = {}
    transport = httpx.AsyncHTTPTransport()
    transport._pool = _FakePool(captured)

    HTTPXClientInstrumentor().instrument()
    try:
        client = InventoryClient("http://service-b:8001", transport=transport)
        items = [SimpleNamespace(sku="LAPTOP-001", quantity=2)]

        asyncio.run(client.reserve(items))

        spans = span_exporter.get_finished_spans()
        reserve_span = next(s for s in spans if s.name == "inventory.reserve")
        client_spans = [s for s in spans if s.kind == SpanKind.CLIENT]
        assert len(client_spans) == 1, "expected exactly one HTTP client span"
        client_span = client_spans[0]

        assert "traceparent" in captured.get("headers", {}), "no traceparent header sent"
        version, trace_id, parent_id, flags = captured["headers"]["traceparent"].split("-")
        assert version == "00"

        # service-b receives the trace of service-a and the client span as parent.
        assert trace_id == format(reserve_span.context.trace_id, "032x")
        assert parent_id == format(client_span.context.span_id, "016x")
        # ... and that client span is itself a child of the custom span.
        assert client_span.context.trace_id == reserve_span.context.trace_id
        assert client_span.parent.span_id == reserve_span.context.span_id
    finally:
        HTTPXClientInstrumentor().uninstrument()
