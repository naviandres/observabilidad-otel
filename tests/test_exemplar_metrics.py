"""A8 app-level bridge: native histogram with OTel exemplars.

The collector's Prometheus exporter does not propagate OTLP exemplars
(verified live 2026-10-12; ADR in ``docs/aws-ecs.md``), so each service
observes a native ``prometheus_client`` histogram once per business request
with an exemplar carrying the ACTIVE trace/span ids. Exemplars exist only in
the OpenMetrics exposition — the text format drops them — so ``GET /metrics``
negotiates on the Accept header.

All offline: in-memory tracer + ``TestClient``, no network, no Docker.
"""

from __future__ import annotations

import ast
import os
import re
import secrets
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTOGRAM_NAME = "http_server_duration_milliseconds"
OPENMETRICS_ACCEPT = "application/openmetrics-text; version=1.0.0; charset=utf-8"

ORDER_BODY = {
    "items": [{"sku": "LAPTOP-001", "quantity": 1, "unit_price": 2500000}],
    "payment_provider": "mock-payment",
}
EMPTY_ORDER_BODY = {"items": [], "payment_provider": "mock-payment"}

EXEMPLAR_BUCKET_RE = re.compile(
    rf"^{HISTOGRAM_NAME}_bucket\{{[^}}]*\}} \S+ # \{{[^}}]*trace_id=\"([0-9a-f]{{32}})\"[^}}]*\}}",
    re.M,
)


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


def _stub_flow(main_module, monkeypatch) -> None:
    """Replace the service-b client and the DB engine with offline stubs."""
    from app.service import InventoryClient

    monkeypatch.setattr(main_module, "engine", _FakeEngine())
    monkeypatch.setattr(
        main_module,
        "inventory_client",
        InventoryClient("http://service-b:8001", transport=_reserved_transport()),
    )


def _instrumented_client(main_module, monkeypatch) -> TestClient:
    _stub_flow(main_module, monkeypatch)
    FastAPIInstrumentor.instrument_app(main_module.app)
    return TestClient(main_module.app)


# ---------------------------------------------------------------------------
# Handler -> scrape with exemplar (the A8 bridge, end to end, offline)
# ---------------------------------------------------------------------------


def test_scrape_exposes_exemplar_with_active_trace_id(main_module, span_exporter, monkeypatch):
    """POST /orders under an injected traceparent leaves an inline exemplar."""
    client = _instrumented_client(main_module, monkeypatch)
    try:
        trace_id = secrets.token_hex(16)
        span_id = secrets.token_hex(8)
        order = client.post(
            "/orders",
            json=ORDER_BODY,
            headers={"traceparent": f"00-{trace_id}-{span_id}-01"},
        )
        assert order.status_code == 200
        scrape = client.get("/metrics", headers={"Accept": OPENMETRICS_ACCEPT})
    finally:
        FastAPIInstrumentor.uninstrument_app(main_module.app)

    assert scrape.status_code == 200
    assert "application/openmetrics-text" in scrape.headers["content-type"]
    exemplar_ids = EXEMPLAR_BUCKET_RE.findall(scrape.text)
    assert trace_id in exemplar_ids, (
        "el scrape OpenMetrics no lleva el exemplar con el trace_id inyectado"
    )
    # The exemplar line must also carry a well-formed span_id and the two
    # low-cardinality labels of the contract.
    bucket_lines = [
        line
        for line in scrape.text.splitlines()
        if line.startswith(f"{HISTOGRAM_NAME}_bucket") and " # {" in line
    ]
    assert any(re.search(r'span_id="[0-9a-f]{16}"', line) for line in bucket_lines)
    assert all('service_name="service-a"' in line for line in bucket_lines)
    assert all('outcome="' in line for line in bucket_lines)


def test_metrics_endpoint_without_accept_has_no_exemplars(main_module, span_exporter, monkeypatch):
    """Text exposition (no Accept) keeps working but cannot carry exemplars."""
    client = _instrumented_client(main_module, monkeypatch)
    try:
        trace_id = secrets.token_hex(16)
        client.post(
            "/orders",
            json=ORDER_BODY,
            headers={"traceparent": f"00-{trace_id}-0000000000000001-01"},
        )
        scrape = client.get("/metrics")
    finally:
        FastAPIInstrumentor.uninstrument_app(main_module.app)

    assert scrape.status_code == 200
    assert scrape.headers["content-type"].startswith("text/plain")
    assert f'trace_id="{trace_id}"' not in scrape.text


def test_failed_order_observed_with_error_outcome(main_module, span_exporter, monkeypatch):
    """The finally-observe covers HTTPException paths: outcome="error"."""
    client = _instrumented_client(main_module, monkeypatch)
    try:
        order = client.post("/orders", json=EMPTY_ORDER_BODY)
        assert order.status_code == 400
        scrape = client.get("/metrics", headers={"Accept": OPENMETRICS_ACCEPT})
    finally:
        FastAPIInstrumentor.uninstrument_app(main_module.app)

    assert f'{HISTOGRAM_NAME}_count{{outcome="error",service_name="service-a"}}' in scrape.text


# ---------------------------------------------------------------------------
# Unit: exemplar extraction from the OTel context
# ---------------------------------------------------------------------------


def test_current_span_exemplar_none_without_provider():
    """No provider installed -> invalid context -> no misleading all-zero ids."""
    from app.latency import current_span_exemplar

    assert current_span_exemplar() is None


def test_current_span_exemplar_ids_from_active_span(span_exporter):
    """Inside a span the dict carries 32-hex trace id and 16-hex span id."""
    from app.latency import current_span_exemplar

    provider = trace.get_tracer_provider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("bridge-test") as span:
        exemplar = current_span_exemplar()

    assert exemplar is not None
    assert re.fullmatch(r"[0-9a-f]{32}", exemplar["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", exemplar["span_id"])
    assert exemplar["trace_id"] == format(span.context.trace_id, "032x")
    assert exemplar["span_id"] == format(span.context.span_id, "016x")


# ---------------------------------------------------------------------------
# Contract: parity, labels, cardinality, version pin
# ---------------------------------------------------------------------------


def _latency_source(service: str) -> str:
    return (REPO_ROOT / service / "app" / "latency.py").read_text(encoding="utf-8")


def test_latency_module_is_identical_in_both_services():
    """service-a and service-b ship the same bridge (parity, like telemetry.py)."""
    a = (REPO_ROOT / "service-a" / "app" / "latency.py").read_bytes()
    b = (REPO_ROOT / "service-b" / "app" / "latency.py").read_bytes()
    assert a == b


@pytest.mark.parametrize("service", ["service-a", "service-b"])
def test_histogram_contract_low_cardinality(service):
    """Name, <=3 labels (never trace_id/order.id as labels) and sane buckets."""
    tree = ast.parse(_latency_source(service))
    # HISTOGRAM_NAME is a module constant; resolve it before checking the call.
    consts = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Histogram"
    )
    assert consts[call.args[0].id] == HISTOGRAM_NAME
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    labelnames = [elt.value for elt in kwargs["labelnames"].elts]
    assert len(labelnames) <= 3
    assert set(labelnames) == {"service_name", "outcome"}
    assert "trace_id" not in labelnames and "order_id" not in labelnames
    buckets = [ast.literal_eval(elt) for elt in kwargs["buckets"].elts]
    assert len(buckets) >= 8 and buckets == sorted(buckets)


@pytest.mark.parametrize("service", ["service-a", "service-b"])
def test_handlers_observe_business_request(service):
    """Both request handlers time the flow and observe it in a finally block."""
    source = (REPO_ROOT / service / "app" / "main.py").read_text(encoding="utf-8")
    assert "observe_business_request(" in source
    assert "started = timer()" in source
    assert "finally:" in source


@pytest.mark.parametrize("service", ["service-a", "service-b"])
def test_metrics_endpoint_negotiates_openmetrics(service):
    source = (REPO_ROOT / service / "app" / "main.py").read_text(encoding="utf-8")
    assert "application/openmetrics-text" in source
    assert "generate_latest_openmetrics" in source


@pytest.mark.parametrize("service", ["service-a", "service-b"])
def test_prometheus_client_pin_supports_exemplars(service):
    """observe(..., exemplar=...) needs prometheus_client >= 0.21 (pinned 0.26)."""
    text = (REPO_ROOT / service / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"^prometheus-client==(\d+)\.(\d+)\.\d+$", text, re.M)
    assert match, f"prometheus-client sin pin exacto en {service}/requirements.txt"
    assert (int(match.group(1)), int(match.group(2))) >= (0, 21)
