"""Offline tests for the correlation verification framework (feature #23).

No network and no cloud: the script module is loaded from ``scripts/`` and
its two I/O seams (``http_request`` and ``gcloud_access_token``) are replaced
with fakes that replay canned Google REST payloads. Covers traceparent
generation, PASS/FAIL/SKIP classification, the evidence JSON schema, the GCP
response parsing of A10-A12 and the exit-code contract.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vc():
    """Load scripts/verify_correlation.py as a module (not a package)."""
    path = REPO_ROOT / "scripts" / "verify_correlation.py"
    spec = importlib.util.spec_from_file_location("verify_correlation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_correlation"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_polling_delays(vc, monkeypatch):
    """Keep the ingestion-poll loops single-shot and instant in tests."""
    monkeypatch.setattr(vc, "POLL_DEADLINE_S", 0.0)
    monkeypatch.setattr(vc, "POLL_INTERVAL_S", 0.0)


def _echoing_http_request(vc_module):
    """Fake transport: echoes the injected traceparent as x-trace-id (A1/A2)."""

    def fake(method, url, headers=None, body=None, timeout=15.0):
        traceparent = (headers or {}).get("traceparent", "")
        trace_id = traceparent.split("-")[1] if len(traceparent.split("-")) == 4 else "0" * 32
        resp_headers = {"x-trace-id": trace_id, "content-type": "application/json"}
        return 200, resp_headers, {"order_id": "x", "status": "created"}

    return fake


def _run_main(vc_module, monkeypatch, transport, tmp_path, extra_args=()):
    """Run main() with a fake transport and an isolated evidence dir."""
    monkeypatch.setattr(vc_module, "http_request", transport)
    argv = ["--endpoint", "http://fake:8080", "--out-dir", str(tmp_path), *extra_args]
    return vc_module.main(argv), tmp_path / "03-correlation.json"


def _read_payload(json_path: Path) -> dict[str, Any]:
    return json.loads(json_path.read_text(encoding="utf-8"))


def _assertion(payload: dict[str, Any], assertion_id: int) -> dict[str, Any]:
    return next(a for a in payload["aserciones"] if a["id"] == assertion_id)


# ---------------------------------------------------------------------------
# traceparent generation
# ---------------------------------------------------------------------------


def test_generate_traceparent_is_valid_w3c(vc):
    traceparent, trace_id = vc.generate_traceparent()
    assert vc.TRACEPARENT_RE.match(traceparent)
    version, tid, span_id, flags = traceparent.split("-")
    assert version == "00"
    assert flags == "01"
    assert tid == trace_id
    assert vc.HEX32_RE.match(trace_id)
    assert len(span_id) == 16


def test_generate_traceparent_is_unique_per_call(vc):
    assert vc.generate_traceparent()[1] != vc.generate_traceparent()[1]


# ---------------------------------------------------------------------------
# classification: SKIP is not a failure, required FAIL is
# ---------------------------------------------------------------------------


def test_required_failures_ignores_skip_and_pass(vc):
    report = vc.CorrelationReport()
    report.register(1, vc.PASS)
    report.register(3, vc.SKIP, razon=vc.FEATURE6_SKIP_REASON)
    report.register(10, vc.SKIP, razon=vc.GCP_SKIP_REASON)
    assert report.required_failures == []
    report.register(12, vc.FAIL, razon="cero incremento")
    assert [r.id for r in report.required_failures] == [12]
    assert all(r.estado == vc.FAIL for r in report.required_failures)


# ---------------------------------------------------------------------------
# local-pure mode (no --include-gcp): correct SKIPs, exit 0
# ---------------------------------------------------------------------------


def test_local_mode_exits_zero_with_expected_skips(vc, monkeypatch, tmp_path):
    rc, json_path = _run_main(vc, monkeypatch, _echoing_http_request(vc), tmp_path)
    assert rc == 0
    payload = _read_payload(json_path)

    assert _assertion(payload, 1)["estado"] == "PASS"
    assert _assertion(payload, 2)["estado"] == "PASS"
    for assertion_id in range(3, 10):
        row = _assertion(payload, assertion_id)
        assert row["estado"] == "SKIP"
        assert row["razon"] == "feature #6 (AWS)"
    for assertion_id in (10, 11, 12):
        row = _assertion(payload, assertion_id)
        assert row["estado"] == "SKIP"
        assert "--include-gcp" in row["razon"]
    row13 = _assertion(payload, 13)
    assert row13["estado"] == "SKIP"
    assert row13["razon"] == "AWS blocked (#1/#6)"


def test_evidence_json_schema_and_markdown_mirror(vc, monkeypatch, tmp_path):
    rc, json_path = _run_main(vc, monkeypatch, _echoing_http_request(vc), tmp_path)
    assert rc == 0
    payload = _read_payload(json_path)

    assert set(payload) == {"timestamp", "project", "endpoint", "trace_id", "aserciones"}
    assert payload["project"] == vc.DEFAULT_PROJECT
    assert payload["endpoint"] == "http://fake:8080"
    assert vc.HEX32_RE.match(payload["trace_id"])
    assert len(payload["aserciones"]) == 13
    assert [a["id"] for a in payload["aserciones"]] == list(range(1, 14))
    for a in payload["aserciones"]:
        assert set(a) == {"id", "nombre", "estado", "razon", "detalles"}
        assert a["estado"] in {"PASS", "FAIL", "SKIP"}
        assert isinstance(a["detalles"], dict)

    md = (tmp_path / "03-correlation.md").read_text(encoding="utf-8")
    assert "| # | Aserción | Estado | Razón |" in md
    assert payload["trace_id"] in md
    assert md.count("**SKIP**") == 11  # 3-9 (#6) + 10-12 (GCP off) + 13 (AWS blocked)
    assert md.count("**PASS**") == 2


# ---------------------------------------------------------------------------
# exit code contract: required FAIL -> 1
# ---------------------------------------------------------------------------


def test_wrong_response_header_fails_assertion_two(vc, monkeypatch, tmp_path):
    def wrong_header(method, url, headers=None, body=None, timeout=15.0):
        return 200, {"x-trace-id": "f" * 32}, {}

    rc, json_path = _run_main(vc, monkeypatch, wrong_header, tmp_path)
    assert rc == 1
    row = _assertion(_read_payload(json_path), 2)
    assert row["estado"] == "FAIL"
    assert "!= trace inyectado" in row["razon"]


def test_unreachable_endpoint_fails_assertion_one(vc, monkeypatch, tmp_path):
    def unreachable(method, url, headers=None, body=None, timeout=15.0):
        raise urllib.error.URLError("connection refused")

    rc, json_path = _run_main(vc, monkeypatch, unreachable, tmp_path)
    assert rc == 1
    payload = _read_payload(json_path)
    assert _assertion(payload, 1)["estado"] == "FAIL"
    assert _assertion(payload, 2)["estado"] == "FAIL"


# ---------------------------------------------------------------------------
# GCP assertions with canned REST payloads
# ---------------------------------------------------------------------------


def _gcp_transport(trace_spans=None, log_entries=None, metric_types=None, series=None):
    """Fake Google APIs keyed by URL; raises HttpError on unexpected calls."""

    def fake(method, url, headers=None, body=None, timeout=15.0):
        if "cloudtrace.googleapis.com" in url:
            return 200, {}, {"spans": trace_spans if trace_spans is not None else []}
        if "logging.googleapis.com" in url:
            return 200, {}, {"entries": log_entries or []}
        if "metricDescriptors" in url:
            return 200, {}, {
                "metricDescriptors": [{"type": t} for t in (metric_types or [])]
            }
        if "timeSeries" in url:
            return 200, {}, {"timeSeries": series or []}
        # POST /orders echo
        traceparent = (headers or {}).get("traceparent", "")
        tid = traceparent.split("-")[1] if len(traceparent.split("-")) == 4 else "0" * 32
        return 200, {"x-trace-id": tid}, {"status": "created"}

    return fake


def _span(name: str, service: str | None = None) -> dict[str, Any]:
    """Cloud Trace v1 REST span: name + labels.service.name (live shape)."""
    labels = {"service.name": service} if service else {}
    return {"spanId": "1", "name": name, "labels": labels}


def _log_entry(container: str) -> dict[str, Any]:
    return {
        "resource": {
            "type": "k8s_container",
            "labels": {
                "container_name": container,
                "pod_name": f"{container}-abc",
                "namespace_name": "otel-lab",
            },
        },
        "jsonPayload": {"name": container, "trace_id": "tid"},
        "logName": "projects/p/logs/otel-app-logs",
    }


def _counter_series(values: list[int]) -> dict[str, Any]:
    return {
        "metric": {"type": "prometheus.googleapis.com/orders_created_total/counter"},
        "points": [
            {
                "value": {"int64Value": str(v)},
                "interval": {"startTime": f"2026-10-11T00:00:0{i}Z", "endTime": f"2026-10-11T00:00:0{i+1}Z"},
            }
            for i, v in enumerate(values)
        ],
    }


@pytest.fixture
def gcp_enabled(vc, monkeypatch):
    monkeypatch.setattr(vc, "gcloud_access_token", lambda project, timeout=30.0: "fake-token")


def test_gcp_happy_path_all_three_pass(vc, monkeypatch, tmp_path, gcp_enabled):
    transport = _gcp_transport(
        trace_spans=[
            _span("POST /orders", "service-a"),
            _span("order.process", "service-a"),
            _span("POST /inventory/reserve", "service-b"),
        ],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([0, 3])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    payload = _read_payload(json_path)
    assert _assertion(payload, 10)["estado"] == "PASS"
    assert _assertion(payload, 11)["estado"] == "PASS"
    assert _assertion(payload, 12)["estado"] == "PASS"
    assert _assertion(payload, 12)["detalles"]["incremento_total"] == 3.0
    assert _assertion(payload, 13)["estado"] == "SKIP"


def test_cloud_trace_attribution_prefers_service_name_label(vc, monkeypatch, tmp_path, gcp_enabled):
    """Live shape: span names are HTTP routes; service.name labels do the work."""
    transport = _gcp_transport(
        trace_spans=[
            _span("POST /orders", "service-a"),
            _span("connect", "service-b"),
        ],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([1])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    row = _assertion(_read_payload(json_path), 10)
    assert row["detalles"]["spans_service_a"] == ["POST /orders"]
    assert row["detalles"]["spans_service_b"] == ["connect"]


def test_cloud_trace_without_service_b_spans_fails(vc, monkeypatch, tmp_path, gcp_enabled):
    transport = _gcp_transport(
        trace_spans=[_span("order.process")],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([1])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    row = _assertion(_read_payload(json_path), 10)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["spans_service_b"] == []


def test_cloud_trace_not_found_fails(vc, monkeypatch, tmp_path, gcp_enabled):
    def not_found(method, url, headers=None, body=None, timeout=15.0):
        if "cloudtrace.googleapis.com" in url:
            raise vc.HttpError(404, '{"code":404}')
        return _gcp_transport(
            log_entries=[_log_entry("service-a"), _log_entry("service-b")],
            metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
            series=[_counter_series([1])],
        )(method, url, headers, body, timeout)

    rc, json_path = _run_main(vc, monkeypatch, not_found, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    assert _assertion(_read_payload(json_path), 10)["estado"] == "FAIL"


def test_cloud_logging_single_service_fails(vc, monkeypatch, tmp_path, gcp_enabled):
    transport = _gcp_transport(
        trace_spans=[_span("order.process"), _span("inventory.query")],
        log_entries=[_log_entry("service-a")],
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([1])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    row = _assertion(_read_payload(json_path), 11)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["servicios"] == ["service-a"]


def test_cloud_logging_text_payload_logger_attribution(vc, monkeypatch, tmp_path, gcp_enabled):
    """Live filelog shape: structlog JSON stays in textPayload, no k8s labels."""
    text_a = '{"event": "Order completed: x", "logger": "service-a", "trace_id": "t"}'
    text_b = '{"event": "Inventory successfully reserved", "logger": "service-b"}'
    entries = [
        {
            "logName": "projects/p/logs/otel-app-logs",
            "resource": {"type": "k8s_cluster", "labels": {"cluster_name": "otel-lab-std"}},
            "textPayload": "2026-10-11T18:35:46Z stderr F " + text_a,
        },
        {
            "logName": "projects/p/logs/otel-app-logs",
            "resource": {"type": "k8s_cluster", "labels": {"cluster_name": "otel-lab-std"}},
            "textPayload": "2026-10-11T18:35:46Z stderr F " + text_b,
        },
        # Collector self-log echoing the payload must NOT attribute.
        {
            "logName": "projects/p/logs/stderr",
            "resource": {"type": "k8s_cluster", "labels": {"cluster_name": "otel-lab-std"}},
            "textPayload": "error helper/transformer.go Failed to process entry",
        },
    ]
    transport = _gcp_transport(
        trace_spans=[_span("order.process"), _span("inventory.query")],
        log_entries=entries,
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([1])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    row = _assertion(_read_payload(json_path), 11)
    assert row["detalles"]["servicios"] == ["service-a", "service-b"]


def test_cloud_logging_uses_resource_names_body(vc, monkeypatch, tmp_path, gcp_enabled):
    """entries:list takes `resourceNames`; `projectNames` is a 400 regression."""
    bodies: list[dict[str, Any]] = []

    def spy(method, url, headers=None, body=None, timeout=15.0):
        if "logging.googleapis.com" in url and body is not None:
            bodies.append(body)
        return _gcp_transport(
            trace_spans=[_span("order.process"), _span("inventory.query")],
            log_entries=[_log_entry("service-a"), _log_entry("service-b")],
            metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
            series=[_counter_series([1])],
        )(method, url, headers, body, timeout)

    rc, _json = _run_main(vc, monkeypatch, spy, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    assert bodies and all("resourceNames" in b and "projectNames" not in b for b in bodies)


def test_cloud_logging_falls_back_to_json_payload_filter(vc, monkeypatch, tmp_path, gcp_enabled):
    calls: list[str] = []

    def only_fallback(method, url, headers=None, body=None, timeout=15.0):
        if "logging.googleapis.com" in url:
            used = body["filter"]
            calls.append(used)
            if used.startswith("trace:"):
                return 200, {}, {"entries": []}
            return 200, {}, {"entries": [_log_entry("service-a"), _log_entry("service-b")]}
        return _gcp_transport(
            trace_spans=[_span("order.process"), _span("inventory.query")],
            metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
            series=[_counter_series([1])],
        )(method, url, headers, body, timeout)

    rc, json_path = _run_main(vc, monkeypatch, only_fallback, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    assert any(f.startswith("jsonPayload.trace_id=") for f in calls)
    assert _assertion(_read_payload(json_path), 11)["estado"] == "PASS"


def test_cloud_monitoring_zero_increment_fails(vc, monkeypatch, tmp_path, gcp_enabled):
    transport = _gcp_transport(
        trace_spans=[_span("order.process"), _span("inventory.query")],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
        series=[_counter_series([5, 5])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    row = _assertion(_read_payload(json_path), 12)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["incremento_total"] == 0.0


def test_cloud_monitoring_uses_real_descriptor_when_name_differs(
    vc, monkeypatch, tmp_path, gcp_enabled
):
    """If GMP created a different type (suffix drift), the discovered one wins."""
    transport = _gcp_transport(
        trace_spans=[_span("order.process"), _span("inventory.query")],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=["prometheus.googleapis.com/orders_created_total_total/counter"],
        series=[_counter_series([0, 2])],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 0
    row = _assertion(_read_payload(json_path), 12)
    assert row["detalles"]["metric_type"] == (
        "prometheus.googleapis.com/orders_created_total_total/counter"
    )


def test_cloud_monitoring_missing_descriptor_fails(vc, monkeypatch, tmp_path, gcp_enabled):
    transport = _gcp_transport(
        trace_spans=[_span("order.process"), _span("inventory.query")],
        log_entries=[_log_entry("service-a"), _log_entry("service-b")],
        metric_types=[],
    )
    rc, json_path = _run_main(vc, monkeypatch, transport, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    row = _assertion(_read_payload(json_path), 12)
    assert row["estado"] == "FAIL"
    assert "descriptor" in row["razon"]


def test_gcp_api_error_becomes_fail_row(vc, monkeypatch, tmp_path, gcp_enabled):
    def forbidden(method, url, headers=None, body=None, timeout=15.0):
        if "cloudtrace.googleapis.com" in url:
            raise vc.HttpError(403, "permission denied")
        return _gcp_transport(
            log_entries=[_log_entry("service-a"), _log_entry("service-b")],
            metric_types=["prometheus.googleapis.com/orders_created_total/counter"],
            series=[_counter_series([1])],
        )(method, url, headers, body, timeout)

    rc, json_path = _run_main(vc, monkeypatch, forbidden, tmp_path, extra_args=["--include-gcp"])
    assert rc == 1
    row = _assertion(_read_payload(json_path), 10)
    assert row["estado"] == "FAIL"
    assert "403" in row["razon"]


def test_gcp_auth_error_fails_all_three(vc, monkeypatch, tmp_path):
    def broken_token(project, timeout=30.0):
        raise vc.AuthError("no ADC")

    monkeypatch.setattr(vc, "gcloud_access_token", broken_token)
    rc, json_path = _run_main(
        vc, monkeypatch, _echoing_http_request(vc), tmp_path, extra_args=["--include-gcp"]
    )
    assert rc == 1
    payload = _read_payload(json_path)
    for assertion_id in (10, 11, 12):
        assert _assertion(payload, assertion_id)["estado"] == "FAIL"


# ---------------------------------------------------------------------------
# gcloud binary resolution (Windows .cmd shim, WinError 2 regression)
# ---------------------------------------------------------------------------


def test_gcloud_token_resolves_shim_with_which(vc, monkeypatch):
    """The token call must go through shutil.which, never the bare 'gcloud'."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: f"C:\\gcloud\\{name}.cmd")
    captured: dict[str, object] = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ya29.fake-token\n", stderr="")

    monkeypatch.setattr(vc.subprocess, "run", fake_run)
    assert vc.gcloud_access_token("otel-lab-obs-2610") == "ya29.fake-token"
    assert captured["cmd"][0] == "C:\\gcloud\\gcloud.cmd"
    assert captured["cmd"][1:3] == ["auth", "print-access-token"]
    # print-access-token ignores --format and prints nothing when it is
    # present (live regression 2026-10-11): the call must stay bare.
    assert not any(str(a).startswith("--format") for a in captured["cmd"])


def test_gcloud_token_empty_stdout_raises_auth_error(vc, monkeypatch):
    """A silent gcloud (no ADC) must not become a 'Bearer ' 401 downstream."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: "C:\\gcloud\\gcloud.cmd")
    monkeypatch.setattr(
        vc.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(vc.AuthError, match="vacio"):
        vc.gcloud_access_token("p")


def test_gcloud_token_falls_back_to_gcloud_cmd(vc, monkeypatch):
    """which('gcloud') may miss on Windows while which('gcloud.cmd') hits."""
    seen: list[str] = []

    def only_cmd(name):
        seen.append(name)
        return "C:\\gcloud\\gcloud.cmd" if name == "gcloud.cmd" else None

    monkeypatch.setattr(vc.shutil, "which", only_cmd)
    monkeypatch.setattr(
        vc.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="tok", stderr=""),
    )
    assert vc.gcloud_access_token("p") == "tok"
    assert seen == ["gcloud", "gcloud.cmd"]


def test_gcloud_token_missing_binary_raises_auth_error(vc, monkeypatch):
    """Without the CLI in PATH the failure is an AuthError, not WinError 2."""
    monkeypatch.setattr(vc.shutil, "which", lambda name: None)
    with pytest.raises(vc.AuthError, match="gcloud no encontrado"):
        vc.gcloud_access_token("p")


# ---------------------------------------------------------------------------
# A13 cross-cloud gate
# ---------------------------------------------------------------------------


def test_include_aws_without_cli_data_skips(vc, monkeypatch, tmp_path):
    def no_cli(cmd, capture_output, text, timeout, check):
        raise OSError("aws not found")

    monkeypatch.setattr(vc.subprocess, "run", no_cli)
    rc, json_path = _run_main(
        vc, monkeypatch, _echoing_http_request(vc), tmp_path, extra_args=["--include-aws"]
    )
    assert rc == 0
    row = _assertion(_read_payload(json_path), 13)
    assert row["estado"] == "SKIP"
    assert "AWS blocked (#1/#6)" in row["razon"]
