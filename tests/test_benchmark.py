"""Verificacion OFFLINE de la campana de benchmark (Fase 4, PLAN 4).

Determinista: no levanta el stack ni toca ninguna nube. Valida la forma del
script k6 (stages 50->100, warmup 1 min + steady 5 min, check 200, sin think
time), que el orquestador PowerShell parsea sin errores, que los nombres de
env de las variantes coinciden con los que lee realmente el motor OTel
(telemetry.py), que el collector en modo bench deja las trazas en un sink
no-op (3.4 C2) con los exporters de logs cloud gateados por env, y que el
agregador aplica las formulas 4.C sobre summaries sinteticos.

La evidencia numerica real de la campana vive en docs/evidence/04-overhead.md
y los artefactos crudos bajo bench/.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
K6_DIR = ROOT / "k6"
ORDERS_JS = K6_DIR / "orders.js"
CONFIG_JS = K6_DIR / "config.js"
ORCHESTRATOR = ROOT / "scripts" / "benchmark.ps1"
REPORTER = ROOT / "scripts" / "bench_report.py"
BENCH_COMPOSE = ROOT / "deploy" / "docker-compose.bench.yml"
BENCH_CLOUD_COMPOSE = ROOT / "deploy" / "docker-compose.bench-cloudlogs.yml"
BENCH_COLLECTOR = ROOT / "deploy" / "collector" / "otel-collector-bench.yaml"
TELEMETRY = ROOT / "service-a" / "app" / "telemetry.py"
CONTRIB_IMAGE = "otel/opentelemetry-collector-contrib:0.162.0"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# k6 scripts (criterio 1)
# ---------------------------------------------------------------------------


def test_k6_scripts_exist() -> None:
    assert ORDERS_JS.is_file() and CONFIG_JS.is_file()


def test_k6_stages_ramp_50_then_hold_100() -> None:
    cfg = _read(CONFIG_JS)
    # 30 s ramp to 50 VU, 30 s ramp to 100 VU (1 min warmup), then the steady
    # hold at 100 VU whose default duration is 300 s (5 min).
    assert re.search(r"duration:\s*'30s',\s*target:\s*50", cfg), "missing 50-VU ramp"
    assert re.search(r"duration:\s*'30s',\s*target:\s*100", cfg), "missing 100-VU ramp"
    assert re.search(r"STEADY_SECONDS \|\| '300'", cfg), "steady default must be 300 s"
    assert re.search(r"STEADY_SECONDS\}s[`\']", cfg), "missing 100-VU hold stage"


def test_k6_scenario_posts_orders_with_200_check_and_no_think_time() -> None:
    orders = _read(ORDERS_JS)
    assert "http.post(TARGET_URL" in orders
    assert "status is 200" in orders
    # "thought off": no sleeps anywhere in the scenario.
    assert not re.search(r"\bsleep\s*\(", orders), "think time must be off"
    # Catalogue comes from the seed SKUs (init-scripts/01-init.sh), not an
    # invented one; the deviation (3 SKUs vs nominal 200) is documented.
    seed = _read(ROOT / "init-scripts" / "01-init.sh")
    for sku in re.findall(r"'([A-Z]+-\d+)'", seed):
        assert sku in _read(CONFIG_JS), f"seed SKU {sku} missing from k6 catalogue"


def test_k6_runs_via_docker_image_pinned_never_latest() -> None:
    orch = _read(ORCHESTRATOR)
    assert "grafana/k6:" in orch and "grafana/k6:latest" not in orch
    assert "--summary-export=" in orch
    # The load scripts are mounted into the container: no host binary.
    assert "/scripts/orders.js" in orch


# ---------------------------------------------------------------------------
# Orchestrator (criterios 2 y 3)
# ---------------------------------------------------------------------------


def test_benchmark_ps1_parses_without_errors() -> None:
    if shutil.which("powershell") is None:
        pytest.skip("powershell not available")
    script = (
        "$tok=$null;$err=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{ORCHESTRATOR}',[ref]$tok,[ref]$err)|Out-Null;"
        "$err.Count; $err | ForEach-Object { $_.Message }"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines and lines[0].strip() == "0", f"PS parse errors: {lines}"


def test_abba_order_and_three_replicas_per_variant() -> None:
    orch = _read(ORCHESTRATOR)
    switch_at = orch.index("switch ($Mode)")
    full = orch[switch_at: orch.index("'quick' {", switch_at)]
    names = re.findall(r"Name = '(A\d|B\d)'", full)
    assert names == ["A1", "B1", "B2", "A2", "A3", "B3"], "ABBA order broken"
    assert sum(1 for n in names if n.startswith("A")) == 3
    assert sum(1 for n in names if n.startswith("B")) == 3


def test_variant_env_names_match_telemetry_engine() -> None:
    compose = _read(BENCH_COMPOSE)
    telemetry = _read(TELEMETRY)
    for env_name in ("OTEL_ENABLED", "OTEL_TRACES_SAMPLER", "OTEL_TRACES_SAMPLER_ARG"):
        assert env_name in compose, f"{env_name} not wired in the bench overlay"
        assert env_name in telemetry, f"{env_name} not read by telemetry.py"
    # Variant B: sampling 0.25 default; traces xray/cloudtrace OFF are the
    # collector-side gates (3.4 C2), asserted in the collector tests below.
    assert "OTEL_BENCH_SAMPLING:-0.25" in compose


def test_orchestrator_warms_up_and_cooldowns_and_records_hostname() -> None:
    orch = _read(ORCHESTRATOR)
    assert "$WarmupSeconds = 60" in orch
    assert "$CooldownSeconds = 120" in orch
    assert "$CostSettleSeconds = 180" in orch
    assert "hostname" in orch and "$env:COMPUTERNAME" in orch
    assert "start_utc" in orch and "end_utc" in orch
    # jaeger/grafana are never started; only the five measured services are.
    assert re.search(r"up', '-d', '--no-build', 'postgres', 'otel-collector',", orch)
    assert "'jaeger'" not in orch and "'grafana'" not in orch


def test_cost_reads_use_real_ingest_anchors() -> None:
    orch = _read(ORCHESTRATOR)
    # AWS: exporter sent_log_records x FilterLogEvents byte sample (storedBytes
    # lags); GCP: billing/bytes_ingested over v3 + gcloud logging read sample.
    assert "awscloudwatchlogs/bench" in orch
    assert "billing/bytes_ingested" in orch
    assert "monitoring.googleapis.com/v3" in orch
    assert "filter-log-events" in orch
    assert "logging', 'read'" in orch


def test_prometheus_snapshot_queries_cover_plan_4b() -> None:
    orch = _read(ORCHESTRATOR)
    for q in (
        "cpu_nonidle_ratio", "collector_cpu_cores", "mem_used_bytes", "mem_limit_bytes",
        "exporter_queue_size", "send_failed_spans", "refused_spans",
        "collector_uptime_s", "accepted_spans", "sent_spans", "series_total",
        "collector_heap_bytes", "aws_sent_log_records", "gcp_sent_log_records",
    ):
        assert q in orch, f"prom snapshot missing {q}"
    assert "/api/v1/query" in orch


# ---------------------------------------------------------------------------
# Collector bench mode: traces OFF (3.4 C2), logs cloud-gated (criterio 5)
# ---------------------------------------------------------------------------


def test_bench_collector_traces_default_to_nop_sink() -> None:
    cfg = _read(BENCH_COLLECTOR)
    assert re.search(r"OTEL_BENCH_TRACES_EXPORTER:-nop", cfg)
    assert "\n  nop:" in cfg, "nop sink must be declared"
    # Managed trace exporters must not appear in the bench config at all.
    assert "awsxray" not in cfg and "googlecloud:\n" not in cfg


def test_bench_collector_cloud_log_exporters_are_env_gated() -> None:
    cfg = _read(BENCH_COLLECTOR)
    assert "awscloudwatchlogs/bench" in cfg
    assert "googlecloud/bench" in cfg
    # Default (gate closed) routes logs to the nop aliases, never to a cloud.
    assert re.search(r"OTEL_BENCH_AWS_LOGS_EXPORTER:-nop/aws", cfg)
    assert re.search(r"OTEL_BENCH_GCP_LOGS_EXPORTER:-nop/gcp", cfg)
    # Per-service bench log groups exist for the storedBytes read.
    assert "/otel/labs/bench/service-a" in cfg and "/otel/labs/bench/service-b" in cfg


def test_bench_cloudlogs_overlay_mounts_adc_readonly_without_secrets() -> None:
    overlay = _read(BENCH_CLOUD_COMPOSE)
    assert ":/google/adc.json:ro" in overlay
    assert "BENCH_ADC_FILE" in overlay
    # No credential material in any versioned bench file.
    for path in (BENCH_COMPOSE, BENCH_CLOUD_COMPOSE, BENCH_COLLECTOR):
        text = _read(path)
        assert "AKIA" not in text and "BEGIN PRIVATE KEY" not in text


def test_bench_collector_validates_with_contrib_binary() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    inspect = subprocess.run(
        ["docker", "image", "inspect", CONTRIB_IMAGE], capture_output=True, timeout=30
    )
    if inspect.returncode != 0:
        pytest.skip(f"contrib image not available: {CONTRIB_IMAGE}")
    proc = subprocess.run(
        [
            "docker", "run", "--rm",
            "-e", "GCP_PROJECT_ID=otel-lab-test",
            "-e", "AWS_REGION=us-east-1",
            "-v", f"{ROOT / 'deploy' / 'collector'}:/cfg",
            CONTRIB_IMAGE,
            "validate", "--config=/cfg/otel-collector-bench.yaml",
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"contrib validate failed:\n{proc.stdout}\n{proc.stderr}"


# ---------------------------------------------------------------------------
# Sampler wired from env (basis of the sampling 0.25 / 1.0 / 0.0 runs)
# ---------------------------------------------------------------------------


def _sampler_from_env(monkeypatch, name=None, arg=None):
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
    if name is not None:
        monkeypatch.setenv("OTEL_TRACES_SAMPLER", name)
    if arg is not None:
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", arg)
    from app.telemetry import _build_sampler

    return _build_sampler()


def test_sampler_unset_keeps_sdk_default(monkeypatch) -> None:
    assert _sampler_from_env(monkeypatch) is None


def test_sampler_parentbased_traceidratio_built_from_env(monkeypatch) -> None:
    from opentelemetry.sdk.trace import sampling

    sampler = _sampler_from_env(monkeypatch, "parentbased_traceidratio", "0.25")
    assert isinstance(sampler, sampling.ParentBased)


@pytest.mark.parametrize("arg", ["1.0", "0.0"])
def test_sampler_attribution_args(monkeypatch, arg) -> None:
    sampler = _sampler_from_env(monkeypatch, "parentbased_traceidratio", arg)
    assert sampler is not None


def test_sampler_rejects_bad_values(monkeypatch) -> None:
    with pytest.raises(ValueError):
        _sampler_from_env(monkeypatch, "parentbased_traceidratio", "1.5")
    with pytest.raises(ValueError):
        _sampler_from_env(monkeypatch, "jaeger_remote", None)


# ---------------------------------------------------------------------------
# Aggregator (criterios 4 y 6)
# ---------------------------------------------------------------------------


def _write_fixture_bench(tmp_path: Path) -> Path:
    bench = tmp_path / "bench"
    (bench / "k6").mkdir(parents=True)
    (bench / "metrics").mkdir()
    (bench / "cost").mkdir()

    def summary(p99: float, reqs: int, err_rate: float) -> dict:
        return {
            "metrics": {
                "http_req_duration": {
                    "avg": p99 / 3, "min": 1.0, "med": p99 / 4,
                    "max": p99 * 1.2, "p(90)": p99 / 2, "p(95)": p99 * 0.8, "p(99)": p99,
                },
                "http_reqs": {"rate": reqs / 360.0, "count": reqs},
                "http_req_failed": {"rate": err_rate},
            }
        }

    windows = []
    specs = [
        ("A1", "A", "1.0", 100.0, 90000, 0.001, 10.0),
        ("A2", "A", "1.0", 102.0, 91000, 0.001, 10.5),
        ("B1", "B", "0.25", 130.0, 80000, 0.002, 14.0),
        ("B2", "B", "0.25", 132.0, 79000, 0.002, 14.2),
        ("attrib-s100", "B", "1.0", 160.0, 70000, 0.002, 16.0),
        ("attrib-s000", "B", "0.0", 110.0, 88000, 0.001, 11.0),
    ]
    for name, variant, sampling, p99, reqs, err, cpu in specs:
        (bench / "k6" / f"{name}.json").write_text(json.dumps(summary(p99, reqs, err)))
        (bench / "metrics" / f"{name}.json").write_text(json.dumps({
            "run": name,
            "queries": {
                "cpu_nonidle_ratio": cpu / 100.0,
                "collector_cpu_cores": 0.3,
                "mem_used_bytes": 2.0 * 2**30,
                "mem_limit_bytes": 16.0 * 2**30,
                "collector_heap_bytes": 50 * 2**20,
                "exporter_queue_size": 0,
                "refused_spans": 0,
                "accepted_spans": 5000,
                "sent_spans": 5000,
                "series_total": 1234,
            },
        }))
        if variant == "B":
            (bench / "cost" / f"{name}.json").write_text(json.dumps({
                "run": name,
                "aws_sent_log_records": 12000,
                "aws_bytes_per_record_sample": 400.0,
                "aws_ingest_est_bytes": 4_800_000,
                "gcp_sent_log_records": 12000,
                "gcp_bytes_per_record_sample": 500.0,
                "gcp_ingest_est_bytes": 6_000_000,
                "gcp_ingestion_bytes_window": 6_000_000,
            }))
        windows.append({
            "run": name, "variant": variant, "sampling": sampling,
            "steady_seconds": 300, "hostname": "TESTBOX", "image_id": "sha256:x",
            "k6_exit_code": 0, "cloud_logs": variant == "B",
            "start_utc": "2026-10-12T00:00:00Z", "end_utc": "2026-10-12T00:07:00Z",
        })
    (bench / "windows.json").write_text(json.dumps(windows))
    ev = tmp_path / "docs" / "evidence"
    ev.mkdir(parents=True)
    (ev / "04-overhead.md").write_text(
        "# Overhead\n\n<!-- BENCH:TABLE -->\n\n<!-- /BENCH:TABLE -->\n", encoding="utf-8"
    )
    return bench


def test_bench_report_produces_aggregates_and_formulas(tmp_path: Path) -> None:
    bench = _write_fixture_bench(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPORTER), "--bench-dir", str(bench), "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    baseline = json.loads((tmp_path / "k6" / "baseline.json").read_text())
    otel = json.loads((tmp_path / "k6" / "otel.json").read_text())
    assert len(baseline["runs"]) == 2 and len(otel["runs"]) == 2
    # Means + stdev between replicas are present (criterio 6).
    assert baseline["mean"]["p99_ms"] == pytest.approx(101.0)
    assert baseline["stdev_between_replicas"]["p99_ms"] is not None

    evidence = (tmp_path / "docs" / "evidence" / "04-overhead.md").read_text()
    # Formulas 4.C applied: (131 - 101)/101*100 = 29.70 %.
    assert "overhead" in evidence.lower()
    assert "29.70" in evidence, "latency overhead formula not applied"
    assert "Atribucion" in evidence or "atribucion" in evidence.lower()
    # Cost rows: real ingest reads (records x sampled bytes + GCP metric) and
    # the per-cloud USD appear.
    assert "12,000" in evidence
    assert "6,000,000" in evidence
    assert "X-Ray / Cloud Trace" in evidence


def test_bench_report_attribution_rows(tmp_path: Path) -> None:
    bench = _write_fixture_bench(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPORTER), "--bench-dir", str(bench), "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    evidence = (tmp_path / "docs" / "evidence" / "04-overhead.md").read_text()
    # s1.0=160, s0.0=110, A=101 -> gen+export=(160-110)/101*100=49.50 %,
    # context=(110-101)/101*100=8.91 %.
    assert "49.50" in evidence
    assert "8.91" in evidence
