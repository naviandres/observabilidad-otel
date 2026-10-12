"""Offline tests for the AWS side of the correlation framework (A3-A9 + A13).

No network and no cloud: the boto3 clients are replaced with hand-written
fakes that replay canned X-Ray / CloudWatch Logs / CloudWatch responses, and
the Prometheus scrape goes through the same ``http_request`` seam as the GCP
tests. Covers the W3C-to-X-Ray id conversion, the structural parent_id check
(R-03), the log-event parsing, the ``GetMetricStatistics``-only guard (R-05),
the cardinality limits and the full dual-flag run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SEG_A_ID = "a1a2a3a4a5a6a7a8"
CLIENT_SPAN_ID = "c1c2c3c4c5c6c7c8"
SEG_B_ID = "b1b2b3b4b5b6b7b8"
SUB_A_ID = "d1d2d3d4d5d6d7d8"


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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_w3c_to_xray_trace_id_shape(vc):
    converted = vc.w3c_to_xray_trace_id("0123456789abcdef0123456789abcdef")
    assert converted == "1-01234567-89abcdef0123456789abcdef"


def test_w3c_to_xray_trace_id_rejects_non_hex(vc):
    with pytest.raises(ValueError):
        vc.w3c_to_xray_trace_id("not-a-hex-id")


def test_parse_trace_segments_reads_json_strings(vc):
    doc = {"name": "service-a", "id": SEG_A_ID}
    resp = {"Traces": [{"Id": "1-x-y", "TraceSegments": [json.dumps(doc)]}]}
    assert vc.parse_trace_segments(resp) == [doc]


def test_service_segment_ids_covers_inline_and_child_docs(vc):
    docs = [
        {
            "name": "service-a",
            "id": SEG_A_ID,
            "subsegments": [{"name": "inventory.reserve", "id": CLIENT_SPAN_ID}],
        },
        {"name": "POST /inventory/reserve", "id": SUB_A_ID, "parent_id": SEG_A_ID},
        {"name": "service-b", "id": SEG_B_ID, "parent_id": CLIENT_SPAN_ID},
    ]
    owned = vc.service_segment_ids(docs)
    assert owned["service-a"] == {SEG_A_ID, CLIENT_SPAN_ID, SUB_A_ID}
    assert owned["service-b"] == {SEG_B_ID}


def _happy_xray_docs() -> list[dict[str, Any]]:
    """service-a segment with the client subsegment + linked service-b segment."""
    return [
        {
            "name": "service-a",
            "id": SEG_A_ID,
            "origin": "AWS::ECS::Container",
            "subsegments": [
                {"name": "localhost:8001", "id": CLIENT_SPAN_ID, "type": "remote"},
                {"name": "order.process", "id": SUB_A_ID},
            ],
            "metadata": {
                "default": {"cloud.provider": "aws", "deployment.environment": "lab"}
            },
        },
        {
            "name": "service-b",
            "id": SEG_B_ID,
            "parent_id": CLIENT_SPAN_ID,
            "metadata": {
                "default": {
                    "db.system": "postgresql",
                    "cloud.provider": "aws",
                    "deployment.environment": "lab",
                }
            },
        },
    ]


def test_xray_parent_link_ok_by_structure(vc):
    ok, details = vc.xray_parent_link_ok(_happy_xray_docs())
    assert ok is True
    assert details["enlace_parent_id_correcto"] is True
    assert details["parent_ids_service_b"] == [CLIENT_SPAN_ID]


def test_xray_parent_link_fails_when_parent_mismatches(vc):
    docs = _happy_xray_docs()
    docs[1]["parent_id"] = "ffffffffffffffff"
    ok, details = vc.xray_parent_link_ok(docs)
    assert ok is False
    assert details["enlace_parent_id_correcto"] is False


def test_xray_parent_link_fails_without_service_b(vc):
    docs = [d for d in _happy_xray_docs() if d.get("name") != "service-b"]
    ok, _details = vc.xray_parent_link_ok(docs)
    assert ok is False


def test_find_attribute_searches_nested_metadata(vc):
    docs = _happy_xray_docs()
    assert str(vc.find_attribute(docs[1], "db.system")).lower() == "postgresql"
    assert vc.find_attribute(docs[0], "cloud.provider") == "aws"
    assert vc.find_attribute({"a": {"b": 1}}, "missing") is None


def test_log_event_trace_id_from_json_and_text(vc):
    tid = "ab" * 16
    assert vc.log_event_trace_id(json.dumps({"event": "x", "trace_id": tid})) == tid
    assert vc.log_event_trace_id(f'prefix "trace_id": "{tid}" suffix') == tid
    assert vc.log_event_trace_id("no trace here") is None


def test_counter_increment_last_minus_first(vc):
    points = [
        {"Timestamp": "2026-10-12T00:01:00Z", "Sum": 5.0},
        {"Timestamp": "2026-10-12T00:00:00Z", "Sum": 2.0},
    ]
    assert vc.counter_increment(points) == 3.0
    assert vc.counter_increment([{"Timestamp": "t", "Sum": 4.0}]) == 4.0
    assert vc.counter_increment([]) == 0.0


def test_parse_prometheus_scrape_counts_and_exemplars(vc):
    tid = "cd" * 16
    text = "\n".join(
        [
            "# HELP http_server_duration_milliseconds_bucket histogram",
            "# TYPE http_server_duration_milliseconds_bucket histogram",
            f'http_server_duration_milliseconds_bucket{{le="100"}} 3 # {{trace_id="{tid}"}} 1.0',
            'http_server_duration_milliseconds_count 3',
            "otelcol_process_uptime 12.0",
            "",
        ]
    )
    series, exemplars = vc.parse_prometheus_scrape(text)
    assert series == 3  # HELP/TYPE and blank lines are not series
    assert exemplars == {"http_server_duration_milliseconds_bucket": [tid]}


def test_parse_prometheus_scrape_ignores_non_latency_exemplars(vc):
    tid = "ee" * 16
    text = f'orders_created_total 1 # {{trace_id="{tid}"}} 1.0'
    series, exemplars = vc.parse_prometheus_scrape(text)
    assert series == 1
    assert exemplars == {}


def test_dimensions_to_attributes_maps_servicename(vc):
    dims = [
        {"Name": "ServiceName", "Value": "service-a"},
        {"Name": "cloud.provider", "Value": "aws"},
    ]
    attrs = vc.dimensions_to_attributes(dims)
    assert attrs["service.name"] == "service-a"
    assert attrs["cloud.provider"] == "aws"


# ---------------------------------------------------------------------------
# Fake boto3 clients (hand-written, no unittest.mock, no network)
# ---------------------------------------------------------------------------


class FakeXRay:
    def __init__(self, summaries: list[str], traces: dict[str, list[dict[str, Any]]]) -> None:
        self.summaries = summaries
        self.traces = traces
        self.calls: list[str] = []

    def get_trace_summaries(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("get_trace_summaries")
        return {"TraceSummaries": [{"TraceId": t} for t in self.summaries]}

    def batch_get_traces(self, TraceIds: list[str]) -> dict[str, Any]:
        self.calls.append("batch_get_traces")
        out = []
        for trace_id in TraceIds:
            docs = self.traces.get(trace_id, [])
            if docs:
                out.append({"Id": trace_id, "TraceSegments": [json.dumps(d) for d in docs]})
        return {"Traces": out}


class FakeLogs:
    def __init__(self, events_by_group: dict[str, list[str]]) -> None:
        self.events_by_group = events_by_group
        self.group_calls: list[str] = []

    def filter_log_events(self, logGroupName: str, **kwargs: Any) -> dict[str, Any]:
        self.group_calls.append(logGroupName)
        messages = self.events_by_group.get(logGroupName, [])
        return {"events": [{"message": m, "timestamp": 0} for m in messages]}


class FakeCloudWatch:
    """Only the two allowed reads exist; GetMetricData raises loudly (R-05)."""

    def __init__(self, metrics: list[dict[str, Any]], datapoints: list[dict[str, Any]]) -> None:
        self.metrics = metrics
        self.datapoints = datapoints
        self.calls: list[str] = []

    def list_metrics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("list_metrics")
        return {"Metrics": self.metrics}

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("get_metric_statistics")
        assert kwargs.get("Statistics") == ["Sum"], "A6 debe pedir Sum"
        assert kwargs.get("Period") == 300, "A6 usa el periodo documentado (300 s)"
        return {"Datapoints": self.datapoints}

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("get_metric_data")
        raise AssertionError("GetMetricData esta prohibido (R-05, se cobra siempre)")


# ---------------------------------------------------------------------------
# End-to-end AWS mode with injected fakes
# ---------------------------------------------------------------------------

FIXED_TRACE_ID = "ef" * 16
FIXED_TRACEPARENT = f"00-{FIXED_TRACE_ID}-{'12' * 8}-01"
CONVERTED = f"1-{FIXED_TRACE_ID[:8]}-{FIXED_TRACE_ID[8:]}"


def _log_message(service: str) -> str:
    return json.dumps(
        {
            "event": "business event",
            "logger": service,
            "trace_id": FIXED_TRACE_ID,
            "cloud.provider": "aws",
            "deployment.environment": "lab",
        }
    )


def _counter_metrics() -> list[dict[str, Any]]:
    return [
        {
            "Namespace": "OtelLab",
            "MetricName": "orders_created_total",
            "Dimensions": [
                {"Name": "ServiceName", "Value": "service-a"},
                {"Name": "cloud.provider", "Value": "aws"},
                {"Name": "deployment.environment", "Value": "lab"},
            ],
        },
        {"Namespace": "OtelLab", "MetricName": "orders_failed_total", "Dimensions": []},
    ]


def _scrape_text() -> str:
    return "\n".join(
        [
            "# TYPE http_server_duration_milliseconds_bucket histogram",
            f'http_server_duration_milliseconds_bucket{{le="100"}} 3 '
            f'# {{trace_id="{FIXED_TRACE_ID}"}} 1.0',
            "http_server_duration_milliseconds_count 3",
        ]
    )


def _wire_aws(vc, monkeypatch, xray=None, logs=None, cloudwatch=None):
    """Inject the fixed trace id + fake clients; return the created fakes."""
    monkeypatch.setattr(vc, "generate_traceparent", lambda: (FIXED_TRACEPARENT, FIXED_TRACE_ID))
    xray = xray or FakeXRay([CONVERTED], {CONVERTED: _happy_xray_docs()})
    logs = logs or FakeLogs(
        {
            "/otel/labs/ecs-cluster/service-a": [_log_message("service-a")],
            "/otel/labs/ecs-cluster/service-b": [_log_message("service-b")],
        }
    )
    cloudwatch = cloudwatch or FakeCloudWatch(
        _counter_metrics(), [{"Timestamp": "2026-10-12T00:00:00Z", "Sum": 1.0}]
    )
    clients = {"xray": xray, "logs": logs, "cloudwatch": cloudwatch}
    monkeypatch.setattr(vc, "make_aws_client", lambda service, profile, region: clients[service])
    return xray, logs, cloudwatch


def _transport_with_scrape(vc):
    """Echo + Prometheus scrape transport (the A8/A9 local read)."""

    def fake(method, url, headers=None, body=None, timeout=15.0):
        if "8889" in url:
            return 200, {"content-type": "text/plain"}, _scrape_text()
        return 200, {"x-trace-id": FIXED_TRACE_ID}, {"status": "created"}

    return fake


def _run_aws_main(vc, monkeypatch, tmp_path, extra_args=(), transport=None):
    monkeypatch.setattr(vc, "http_request", transport or _transport_with_scrape(vc))
    argv = ["--endpoint", "http://fake:8080", "--out-dir", str(tmp_path), *extra_args]
    return vc.main(argv), tmp_path / "03-correlation.json"


def _assertion(payload: dict[str, Any], assertion_id: int) -> dict[str, Any]:
    return next(a for a in payload["aserciones"] if a["id"] == assertion_id)


def test_aws_mode_happy_path_all_pass(vc, monkeypatch, tmp_path):
    _wire_aws(vc, monkeypatch)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 0
    payload = _read(json_path)
    for assertion_id in range(3, 10):
        row = _assertion(payload, assertion_id)
        assert row["estado"] == "PASS", f"A{assertion_id}: {row['razon']}"


def test_aws_mode_never_calls_get_metric_data(vc, monkeypatch, tmp_path):
    """R-05 guard: the AWS block only uses the free-tier reads it declares."""
    _xray, _logs, cloudwatch = _wire_aws(vc, monkeypatch)
    _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert "get_metric_statistics" in cloudwatch.calls
    assert "list_metrics" in cloudwatch.calls
    assert "get_metric_data" not in cloudwatch.calls


def test_aws_mode_uses_exporter_log_groups_not_the_driver(vc, monkeypatch, tmp_path):
    _xray, logs, _cw = _wire_aws(vc, monkeypatch)
    _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert "/otel/labs/ecs-cluster/service-a" in logs.group_calls
    assert "/otel/labs/ecs-cluster/service-b" in logs.group_calls
    assert not any("ecs-platform" in g for g in logs.group_calls)


def _read(json_path: Path) -> dict[str, Any]:
    return json.loads(json_path.read_text(encoding="utf-8"))


def test_aws_mode_missing_service_b_segment_fails_a3(vc, monkeypatch, tmp_path):
    docs = [d for d in _happy_xray_docs() if d.get("name") != "service-b"]
    xray = FakeXRay([CONVERTED], {CONVERTED: docs})
    _wire_aws(vc, monkeypatch, xray=xray)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 1
    row = _assertion(_read(json_path), 3)
    assert row["estado"] == "FAIL"
    assert "parent_id" in row["nombre"] or row["detalles"]["segmentos_service_b"] == 0


def test_aws_mode_log_group_without_trace_fails_a5(vc, monkeypatch, tmp_path):
    logs = FakeLogs(
        {
            "/otel/labs/ecs-cluster/service-a": [_log_message("service-a")],
            "/otel/labs/ecs-cluster/service-b": ['{"event": "sin trace"}'],
        }
    )
    _wire_aws(vc, monkeypatch, logs=logs)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 1
    row = _assertion(_read(json_path), 5)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["servicios"] == ["service-a"]


def test_aws_mode_zero_counter_increment_fails_a6(vc, monkeypatch, tmp_path):
    cloudwatch = FakeCloudWatch(
        _counter_metrics(),
        [
            {"Timestamp": "2026-10-12T00:00:00Z", "Sum": 7.0},
            {"Timestamp": "2026-10-12T00:01:00Z", "Sum": 7.0},
        ],
    )
    _wire_aws(vc, monkeypatch, cloudwatch=cloudwatch)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 1
    row = _assertion(_read(json_path), 6)
    assert row["estado"] == "FAIL"
    assert "no incremento" in row["razon"]


def test_aws_mode_resource_gap_fails_a7(vc, monkeypatch, tmp_path):
    """deployment.environment missing from the EMF dimensions is a FAIL, not a gap."""
    metrics = [dict(_counter_metrics()[0], Dimensions=[{"Name": "ServiceName", "Value": "service-a"}])]
    metrics.append(_counter_metrics()[1])
    cloudwatch = FakeCloudWatch(
        metrics, [{"Timestamp": "2026-10-12T00:00:00Z", "Sum": 1.0}]
    )
    _wire_aws(vc, monkeypatch, cloudwatch=cloudwatch)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 1
    row = _assertion(_read(json_path), 7)
    assert row["estado"] == "FAIL"
    assert "deployment.environment ausente en metrica" in row["razon"]


def test_aws_mode_no_exemplars_fails_a8(vc, monkeypatch, tmp_path):
    def transport(method, url, headers=None, body=None, timeout=15.0):
        if "8889" in url:
            return 200, {}, 'http_server_duration_milliseconds_bucket{le="1"} 1\n'
        return 200, {"x-trace-id": FIXED_TRACE_ID}, {}

    _wire_aws(vc, monkeypatch)
    rc, json_path = _run_aws_main(
        vc, monkeypatch, tmp_path, extra_args=["--include-aws"], transport=transport
    )
    assert rc == 1
    row = _assertion(_read(json_path), 8)
    assert row["estado"] == "FAIL"
    assert "exemplars" in row["razon"]


def test_aws_mode_scrape_down_fails_a8_and_a9(vc, monkeypatch, tmp_path):
    def transport(method, url, headers=None, body=None, timeout=15.0):
        if "8889" in url:
            raise urllib.error.URLError("tunnel closed")
        return 200, {"x-trace-id": FIXED_TRACE_ID}, {}

    _wire_aws(vc, monkeypatch)
    rc, json_path = _run_aws_main(
        vc, monkeypatch, tmp_path, extra_args=["--include-aws"], transport=transport
    )
    assert rc == 1
    payload = _read(json_path)
    assert _assertion(payload, 8)["estado"] == "FAIL"
    assert _assertion(payload, 9)["estado"] == "FAIL"
    assert "Prometheus" in _assertion(payload, 9)["razon"]


def test_aws_mode_cardinality_over_custom_metrics_fails_a9(vc, monkeypatch, tmp_path):
    metrics = [
        {"Namespace": "OtelLab", "MetricName": f"orders_created_total_{i}", "Dimensions": []}
        for i in range(11)
    ]
    metrics[0] = _counter_metrics()[0]
    cloudwatch = FakeCloudWatch(
        metrics, [{"Timestamp": "2026-10-12T00:00:00Z", "Sum": 1.0}]
    )
    _wire_aws(vc, monkeypatch, cloudwatch=cloudwatch)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 1
    row = _assertion(_read(json_path), 9)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["custom_metrics"] == 11


def test_aws_mode_series_over_limit_fails_a9(vc, monkeypatch, tmp_path):
    lines = [f'otelcol_test_series{{i="{i}"}} 1' for i in range(1501)]
    lines.append(
        f'http_server_duration_milliseconds_bucket{{le="1"}} 1 # {{trace_id="{FIXED_TRACE_ID}"}} 1.0'
    )

    def transport(method, url, headers=None, body=None, timeout=15.0):
        if "8889" in url:
            return 200, {}, "\n".join(lines)
        return 200, {"x-trace-id": FIXED_TRACE_ID}, {}

    _wire_aws(vc, monkeypatch)
    rc, json_path = _run_aws_main(
        vc, monkeypatch, tmp_path, extra_args=["--include-aws"], transport=transport
    )
    assert rc == 1
    row = _assertion(_read(json_path), 9)
    assert row["estado"] == "FAIL"
    assert row["detalles"]["prometheus_series"] == 1502


def test_aws_mode_structural_fallback_when_id_not_listed(vc, monkeypatch, tmp_path):
    """R-03: if the converted id is not in the summaries, scan by structure."""
    scanned = "1-99999999-000000000000000000000000"
    xray = FakeXRay([scanned], {scanned: _happy_xray_docs(), CONVERTED: _happy_xray_docs()})
    _wire_aws(vc, monkeypatch, xray=xray)
    rc, json_path = _run_aws_main(vc, monkeypatch, tmp_path, extra_args=["--include-aws"])
    assert rc == 0
    assert _assertion(_read(json_path), 3)["estado"] == "PASS"


# ---------------------------------------------------------------------------
# A13 dual-flag aggregation with fakes on both sides
# ---------------------------------------------------------------------------


def test_cross_cloud_pass_requires_both_sides(vc, monkeypatch, tmp_path):
    _wire_aws(vc, monkeypatch)
    monkeypatch.setattr(vc, "generate_traceparent", lambda: (FIXED_TRACEPARENT, FIXED_TRACE_ID))
    monkeypatch.setattr(vc, "gcloud_access_token", lambda project, timeout=30.0: "fake-token")

    def transport(method, url, headers=None, body=None, timeout=15.0):
        if "8889" in url:
            return 200, {}, _scrape_text()
        if "cloudtrace.googleapis.com" in url:
            return 200, {}, {"spans": [{"name": "POST /inventory/reserve",
                                        "labels": {"service.name": "service-b"}}]}
        if "logging.googleapis.com" in url:
            return 200, {}, {"entries": []}
        if "metricDescriptors" in url:
            return 200, {}, {"metricDescriptors": [
                {"type": "prometheus.googleapis.com/orders_created_total/counter"}]}
        if "timeSeries" in url:
            return 200, {}, {"timeSeries": [{
                "points": [{"value": {"int64Value": "1"},
                            "interval": {"endTime": "2026-10-12T00:00:01Z"}}]}]}
        return 200, {"x-trace-id": FIXED_TRACE_ID}, {"status": "created"}

    monkeypatch.setattr(vc, "http_request", transport)
    argv = [
        "--endpoint", "http://fake:8080", "--out-dir", str(tmp_path),
        "--include-aws", "--include-gcp",
    ]
    rc = vc.main(argv)
    payload = _read(tmp_path / "03-correlation.json")
    row13 = _assertion(payload, 13)
    # A10/A11 fail by design here (only service-b reaches Cloud Trace and the
    # log filters stay empty); A13 only needs ONE signal per cloud.
    assert row13["estado"] == "PASS", row13
    assert row13["detalles"]["aws"] == ["xray", "cloudwatch_logs"]
    assert row13["detalles"]["gcp"] == "cloud_trace"
    assert _assertion(payload, 10)["estado"] == "FAIL"
    assert rc == 1
