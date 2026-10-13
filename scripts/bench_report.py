"""Aggregate the overhead benchmark campaign (PLAN §4.C).

Reads the raw artifacts produced by ``scripts/benchmark.ps1`` under ``bench/``
(windows.json, k6 summaries, Prometheus snapshots, cost deltas) and emits:

  * ``k6/baseline.json`` — per-run + mean metrics of variant A (OTel off)
  * ``k6/otel.json``     — per-run + mean metrics of variant B (OTel on)
  * ``docs/evidence/04-overhead.md`` — the full table with the §4.C formulas:

      overhead_latencia_pct = (p99_B - p99_A) / p99_A * 100
      overhead_cpu_pct      = (cpu_B - cpu_A) / cpu_A * 100
      overhead_mem_pct      = (rss_B - rss_A) / rss_A * 100
      overhead_coste_usd    = coste(B) - coste(A)

Stdlib only; runs with any Python 3.11+ (the deliverable venv or the system
interpreter).

Usage:
    python scripts/bench_report.py [--bench-dir bench] [--root .]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Cloud price constants used when the campaign did NOT read real ingest
# (plan B: bytes x price). Sources checked 2026-10-12:
#   AWS CloudWatch Logs us-east-1 — ingestion+indexing $0.50/GB, storage
#   $0.03/GB-month (https://aws.amazon.com/cloudwatch/prices/).
#   GCP Cloud Logging — first 50 GB of ingestion per month are within the
#   free quota; beyond that, storage $0.50/GB-month
#   (https://cloud.google.com/logging/pricing).
CLOUDWATCH_INGEST_USD_PER_GB = 0.50
CLOUD_LOGGING_FREE_GB = 50.0
CLOUD_LOGGING_STORAGE_USD_PER_GB_MONTH = 0.50

VARIANTS = ("A", "B")

# Metric keys extracted from the k6 summary-export JSON.
K6_TREND = "http_req_duration"
K6_COUNT = "http_reqs"
K6_FAIL = "http_req_failed"


def _load_json(path: Path) -> Any:
    # PS 5.1 Set-Content -Encoding UTF8 writes a BOM; utf-8-sig tolerates it.
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _metric_body(summary: dict, name: str) -> dict:
    """Return a metric's stats body in either k6 summary-export shape.

    k6 1.x nests them under ``values``; k6 2.x writes them flat. Tolerate
    both so the report does not depend on the pinned image's format.
    """
    metric = summary["metrics"][name]
    return metric.get("values", metric)


def _k6_percentiles(summary: dict) -> dict:
    """Map the k6 trend stats to the §4.B latency keys."""
    # http_req_duration only exists once requests complete; iteration_duration
    # is the fallback counter for broken runs (visible in the report as such).
    name = K6_TREND if K6_TREND in summary["metrics"] else "iteration_duration"
    values = _metric_body(summary, name)
    return {
        "p50_ms": values.get("med", values.get("p(50)")),
        "p95_ms": values.get("p(95)"),
        "p99_ms": values.get("p(99)"),
        "max_ms": values.get("max"),
        "avg_ms": values.get("avg"),
    }


def _k6_counts(summary: dict) -> dict:
    # k6 only registers http_reqs when at least one request completed; fall
    # back to the generic iterations counter so a broken run is still readable.
    if K6_COUNT in summary["metrics"]:
        reqs = _metric_body(summary, K6_COUNT)
    else:
        reqs = _metric_body(summary, "iterations")
    fail = _metric_body(summary, K6_FAIL)
    count = reqs.get("count", reqs.get("value", reqs.get("total")))
    error_rate = fail.get("rate", fail.get("value"))
    if error_rate is None:
        passes, fails = fail.get("passes"), fail.get("fails")
        if passes is not None and fails is not None:
            total = passes + fails
            error_rate = (fails / total) if total else None
    return {
        "requests": count,
        "req_rate": reqs.get("rate"),
        "error_rate": error_rate,
    }


@dataclass
class RunData:
    """One benchmark run with everything the table needs."""

    name: str
    variant: str
    sampling: str
    steady_seconds: int
    start_utc: str
    end_utc: str
    hostname: str
    image_id: str
    k6_exit_code: int
    cloud_logs: bool = False
    latency: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    prom: dict = field(default_factory=dict)
    cost: Optional[dict] = None

    @property
    def cpu_pct(self) -> Optional[float]:
        ratio = self.prom.get("cpu_nonidle_ratio")
        return ratio * 100.0 if ratio is not None else None

    @property
    def mem_used_bytes(self) -> Optional[float]:
        return self.prom.get("mem_used_bytes")

    @property
    def mem_limit_bytes(self) -> Optional[float]:
        return self.prom.get("mem_limit_bytes")

    @property
    def mem_pct(self) -> Optional[float]:
        used, limit = self.mem_used_bytes, self.mem_limit_bytes
        if used is None or not limit:
            return None
        return used / limit * 100.0


def load_runs(bench_dir: Path) -> list[RunData]:
    windows = _load_json(bench_dir / "windows.json")
    runs: list[RunData] = []
    for w in windows:
        k6_path = bench_dir / "k6" / f"{w['run']}.json"
        if not k6_path.exists():
            raise FileNotFoundError(f"missing k6 summary for run {w['run']}: {k6_path}")
        summary = _load_json(k6_path)
        metrics_path = bench_dir / "metrics" / f"{w['run']}.json"
        prom: dict = {}
        if metrics_path.exists():
            prom = _load_json(metrics_path).get("queries", {})
        cost_path = bench_dir / "cost" / f"{w['run']}.json"
        cost = _load_json(cost_path) if cost_path.exists() else None
        runs.append(
            RunData(
                name=w["run"],
                variant=w["variant"],
                sampling=str(w["sampling"]),
                steady_seconds=int(w["steady_seconds"]),
                start_utc=w["start_utc"],
                end_utc=w["end_utc"],
                hostname=w["hostname"],
                image_id=w["image_id"],
                k6_exit_code=int(w["k6_exit_code"]),
                cloud_logs=bool(w.get("cloud_logs", False)),
                latency=_k6_percentiles(summary),
                counts=_k6_counts(summary),
                prom=prom,
                cost=cost,
            )
        )
    return runs


def _mean(values: list[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


def _stdev(values: list[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return statistics.stdev(present) if len(present) >= 2 else None


def variant_aggregate(runs: list[RunData]) -> dict:
    """Per-run rows + mean/stdev across replicas for one variant."""
    rows = [
        {
            "run": r.name,
            "sampling": r.sampling,
            "steady_seconds": r.steady_seconds,
            "window": {"start_utc": r.start_utc, "end_utc": r.end_utc},
            "hostname": r.hostname,
            "image_id": r.image_id,
            "k6_exit_code": r.k6_exit_code,
            "cloud_logs": r.cloud_logs,
            **r.latency,
            **r.counts,
            "cpu_pct": r.cpu_pct,
            "collector_cpu_cores": r.prom.get("collector_cpu_cores"),
            "mem_used_bytes": r.mem_used_bytes,
            "mem_limit_bytes": r.mem_limit_bytes,
            "mem_pct": r.mem_pct,
            "collector_heap_bytes": r.prom.get("collector_heap_bytes"),
            "pipeline": {
                k: r.prom.get(k)
                for k in (
                    "exporter_queue_size",
                    "send_failed_spans",
                    "refused_spans",
                    "failed_spans",
                    "accepted_spans",
                    "sent_spans",
                    "accepted_log_records",
                    "sent_log_records",
                    "accepted_metric_points",
                    "sent_metric_points",
                    "collector_uptime_s",
                    "series_total",
                )
            },
            "cost": r.cost,
        }
        for r in runs
    ]
    means = {
        "p50_ms": _mean([r.latency.get("p50_ms") for r in runs]),
        "p95_ms": _mean([r.latency.get("p95_ms") for r in runs]),
        "p99_ms": _mean([r.latency.get("p99_ms") for r in runs]),
        "max_ms": _mean([r.latency.get("max_ms") for r in runs]),
        "requests": _mean([r.counts.get("requests") for r in runs]),
        "req_rate": _mean([r.counts.get("req_rate") for r in runs]),
        "error_rate": _mean([r.counts.get("error_rate") for r in runs]),
        "cpu_pct": _mean([r.cpu_pct for r in runs]),
        "collector_cpu_cores": _mean([r.prom.get("collector_cpu_cores") for r in runs]),
        "mem_used_bytes": _mean([r.mem_used_bytes for r in runs]),
        "mem_pct": _mean([r.mem_pct for r in runs]),
        "collector_heap_bytes": _mean([r.prom.get("collector_heap_bytes") for r in runs]),
    }
    stdevs = {
        "p99_ms": _stdev([r.latency.get("p99_ms") for r in runs]),
        "cpu_pct": _stdev([r.cpu_pct for r in runs]),
        "mem_pct": _stdev([r.mem_pct for r in runs]),
    }
    return {"runs": rows, "mean": means, "stdev_between_replicas": stdevs}


def overhead(a: dict, b: dict) -> dict:
    """§4.C formulas on variant means."""
    out: dict = {}
    pairs = {
        "overhead_latencia_pct": ("p99_ms", "p99_ms"),
        "overhead_cpu_pct": ("cpu_pct", "cpu_pct"),
        "overhead_mem_pct": ("mem_used_bytes", "mem_used_bytes"),
    }
    for key, (bk, ak) in pairs.items():
        va, vb = a["mean"].get(ak), b["mean"].get(bk)
        out[key] = ((vb - va) / va * 100.0) if (va not in (None, 0) and vb is not None) else None
    return out


def attribution(runs: list[RunData], baseline_a: dict) -> dict:
    """Split B's overhead between span generation and export (§4.A).

    attrib(1.0)  = full recording + export pipeline
    attrib(0.0)  = context/propagation only (sampler drops every root span)
    The difference isolates generation+export; attrib(0.0) - A isolates the
    instrumentation that survives sampling (context, propagators, metrics
    reader still on).
    """
    s100 = next((r for r in runs if r.sampling == "1.0" and r.variant == "B"), None)
    s000 = next((r for r in runs if r.sampling == "0.0" and r.variant == "B"), None)
    out = {
        "note": (
            "attribucion: p99(s1.0)-p99(s0.0) ~ generacion+export de spans; "
            "p99(s0.0)-p99(A) ~ contexto/propagacion que sobrevive al sampling"
        ),
        "p99_s100_ms": s100.latency.get("p99_ms") if s100 else None,
        "p99_s000_ms": s000.latency.get("p99_ms") if s000 else None,
        "p99_a_ms": baseline_a["mean"].get("p99_ms"),
    }
    if out["p99_s100_ms"] and out["p99_s000_ms"] and out["p99_a_ms"]:
        out["gen_export_pct"] = (out["p99_s100_ms"] - out["p99_s000_ms"]) / out["p99_a_ms"] * 100.0
        out["context_pct"] = (out["p99_s000_ms"] - out["p99_a_ms"]) / out["p99_a_ms"] * 100.0
    return out


def _fmt(v: Optional[float], nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and math.isnan(v):
        return "—"
    return f"{v:,.{nd}f}"


def build_table(runs: list[RunData], a: dict, b: dict, ov: dict, attrib: dict,
                cost: dict) -> str:
    """Markdown table of the campaign (PLAN §4.C format)."""
    lines = [
        "### Tabla de replicas (por run)",
        "",
        "| run | var | sampling | steady s | p50 ms | p95 ms | p99 ms | max ms | reqs | req/s | err % | CPU % | mem used MB | heap MB | queue | refused |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        p = r.prom
        lines.append(
            "| {name} | {var} | {s} | {st} | {p50} | {p95} | {p99} | {mx} | {req} | {rate} | {err} | {cpu} | {mem} | {heap} | {q} | {ref} |".format(
                name=r.name, var=r.variant, s=r.sampling, st=r.steady_seconds,
                p50=_fmt(r.latency.get("p50_ms")), p95=_fmt(r.latency.get("p95_ms")),
                p99=_fmt(r.latency.get("p99_ms")), mx=_fmt(r.latency.get("max_ms")),
                req=_fmt(r.counts.get("requests"), 0), rate=_fmt(r.counts.get("req_rate"), 1),
                err=_fmt((r.counts.get("error_rate") or 0) * 100 if r.counts.get("error_rate") is not None else None),
                cpu=_fmt(r.cpu_pct), mem=_fmt((r.mem_used_bytes or 0) / 2**20 if r.mem_used_bytes else None, 1),
                heap=_fmt((p.get("collector_heap_bytes") or 0) / 2**20 if p.get("collector_heap_bytes") else None, 1),
                q=_fmt(p.get("exporter_queue_size"), 0), ref=_fmt(p.get("refused_spans"), 0),
            )
        )
    lines += [
        "",
        "### Medias por variante y overhead (formulas 4.C)",
        "",
        "| metrica | A (media) | B (media) | overhead % |",
        "|---|---|---|---|",
        f"| p99 latencia (ms) | {_fmt(a['mean'].get('p99_ms'))} | {_fmt(b['mean'].get('p99_ms'))} | {_fmt(ov.get('overhead_latencia_pct'))} |",
        f"| CPU host % | {_fmt(a['mean'].get('cpu_pct'))} | {_fmt(b['mean'].get('cpu_pct'))} | {_fmt(ov.get('overhead_cpu_pct'))} |",
        f"| Memoria usada (MB) | {_fmt((a['mean'].get('mem_used_bytes') or 0)/2**20 if a['mean'].get('mem_used_bytes') else None,1)} | {_fmt((b['mean'].get('mem_used_bytes') or 0)/2**20 if b['mean'].get('mem_used_bytes') else None,1)} | {_fmt(ov.get('overhead_mem_pct'))} |",
        f"| req/s | {_fmt(a['mean'].get('req_rate'),1)} | {_fmt(b['mean'].get('req_rate'),1)} | — |",
        f"| error rate % | {_fmt((a['mean'].get('error_rate') or 0)*100 if a['mean'].get('error_rate') is not None else None)} | {_fmt((b['mean'].get('error_rate') or 0)*100 if b['mean'].get('error_rate') is not None else None)} | — |",
        "",
        f"Desviacion entre replicas: p99 A {_fmt(a['stdev_between_replicas'].get('p99_ms'))} ms, "
        f"B {_fmt(b['stdev_between_replicas'].get('p99_ms'))} ms.",
        "",
        "### Atribucion (sampling 1.0 vs 0.0)",
        "",
        f"- p99 sampling 1.0: {_fmt(attrib.get('p99_s100_ms'))} ms",
        f"- p99 sampling 0.0: {_fmt(attrib.get('p99_s000_ms'))} ms",
        f"- p99 baseline A:   {_fmt(attrib.get('p99_a_ms'))} ms",
        f"- generacion+export de spans: {_fmt(attrib.get('gen_export_pct'))} % del p99 de A",
        f"- contexto/propagacion:       {_fmt(attrib.get('context_pct'))} % del p99 de A",
        "",
        "### Coste incremental por nube (variante B)",
        "",
        "| run | AWS records | AWS ingest est. GB | AWS USD | GCP ingestion bytes | GCP USD |",
        "|---|---|---|---|---|---|",
    ]
    for row in cost["rows"]:
        lines.append(
            f"| {row['run']} | {_fmt(row.get('aws_sent_log_records'),0)} | "
            f"{_fmt(row.get('aws_ingest_est_gb'),3)} | {_fmt(row.get('aws_usd'),4)} | "
            f"{_fmt(row.get('gcp_ingestion_bytes'),0)} | {_fmt(row.get('gcp_usd'),4)} |"
        )
    lines += [
        "",
        f"- Total campana AWS: {_fmt(cost['aws_total_usd'],4)} USD · "
        f"GCP: {_fmt(cost['gcp_total_usd'],4)} USD · "
        f"fuente: {cost['source']}",
        f"- X-Ray / Cloud Trace: **$0** durante la campana — los exporters de "
        f"trazas gestionadas estan apagados (OTEL_TRACES_XRAY / "
        f"OTEL_TRACES_CLOUDTRACE / pipeline->nop, seccion 3.4 C2). "
        f"{'Leido del ingest real' if cost['source']=='real_ingest' else 'Decidido por C2; el run de atribucion 1.0 tampoco exporta a las nubes (trazas->nop)'}",
        f"- overhead por dolar: {_fmt(cost.get('overhead_per_usd_pct_per_usd'))} %p99/USD"
        + (f" (coste total {_fmt(cost['total_usd'],4)} USD)" if cost.get("total_usd") else " (coste ~0 USD: el ingest queda dentro de los free tiers)"),
    ]
    return "\n".join(lines)


def compute_cost(runs: list[RunData]) -> dict:
    """Cost rows from the per-run ingest reads.

    The orchestrator measures the REAL ingest of variant B:
      * AWS: sent_log_records (exporter counter, per-run collector) x the
        sampled average bytes/record (filterLogEvents page) -> ingest bytes;
        CloudWatch bills ingest+indexing at $0.50/GB (us-east-1).
      * GCP: the sum of the 1-minute points of the internal metric
        logging.googleapis.com/billing/bytes_ingested over the run window.
        Ingestion inside the first 50 GB/month is not billed.
    Without a cost file (plan B: campaign run with the gates closed) the row
    only carries the run name and the source stays 'estimated'.
    """
    rows = []
    aws_total_usd = 0.0
    gcp_total_usd = 0.0
    real = any(r.cost and (r.cost.get("aws_ingest_est_bytes") or r.cost.get("gcp_ingestion_bytes_window"))
               for r in runs)
    for r in runs:
        if r.variant != "B":
            continue
        row: dict = {"run": r.name}
        if r.cost:
            est = r.cost.get("aws_ingest_est_bytes")
            if est is not None:
                est_gb = est / 2**30
                row["aws_sent_log_records"] = r.cost.get("aws_sent_log_records")
                row["aws_ingest_est_gb"] = est_gb
                row["aws_usd"] = est_gb * CLOUDWATCH_INGEST_USD_PER_GB
                aws_total_usd += row["aws_usd"]
            gcp_window = r.cost.get("gcp_ingestion_bytes_window")
            gcp_est = r.cost.get("gcp_ingest_est_bytes")
            # Primary: records x sampled entry size (the exporter counter is
            # exact per run). The billing-metric window read is kept as a
            # cross-check when it is larger — its points lag 5-10 min, so a
            # per-run window query undercounts otherwise.
            gcp_bytes = gcp_est
            if (gcp_window or 0) > (gcp_est or 0):
                gcp_bytes = gcp_window
            if gcp_bytes is not None:
                gcp_gb = gcp_bytes / 2**30
                row["gcp_ingestion_bytes"] = gcp_bytes
                row["gcp_usd"] = (
                    0.0 if gcp_gb <= CLOUD_LOGGING_FREE_GB
                    else gcp_gb * CLOUD_LOGGING_STORAGE_USD_PER_GB_MONTH
                )
                gcp_total_usd += row["gcp_usd"]
        rows.append(row)
    return {
        "rows": rows,
        "aws_total_usd": aws_total_usd,
        "gcp_total_usd": gcp_total_usd,
        "total_usd": aws_total_usd + gcp_total_usd,
        "source": "real_ingest" if real else "estimated_bytes_x_price",
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-dir", default="bench", help="raw campaign dir")
    parser.add_argument("--root", default=".", help="deliverable repo root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    bench = Path(args.bench_dir)
    if not bench.is_absolute():
        bench = root / bench

    runs = load_runs(bench)
    a_runs = [r for r in runs if r.variant == "A" and r.sampling == "1.0"]
    b_runs = [r for r in runs if r.variant == "B" and r.sampling == "0.25"]
    if not a_runs or not b_runs:
        raise SystemExit("no A/B runs found in windows.json")

    a = variant_aggregate(a_runs)
    b = variant_aggregate(b_runs)
    ov = overhead(a, b)
    attrib = attribution(runs, a)
    cost = compute_cost(runs)
    if ov.get("overhead_latencia_pct") is not None and cost["total_usd"] > 0:
        cost["overhead_per_usd_pct_per_usd"] = ov["overhead_latencia_pct"] / cost["total_usd"]
    else:
        cost["overhead_per_usd_pct_per_usd"] = None

    out_a = root / "k6" / "baseline.json"
    out_b = root / "k6" / "otel.json"
    out_a.parent.mkdir(parents=True, exist_ok=True)
    out_a.write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
    out_b.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")

    table = build_table(runs, a, b, ov, attrib, cost)
    evidence = root / "docs" / "evidence" / "04-overhead.md"
    if evidence.exists():
        # Keep the hand-written methodology/limitations sections and replace
        # only the generated block between markers.
        text = evidence.read_text(encoding="utf-8")
        start = text.find("<!-- BENCH:TABLE -->")
        end = text.find("<!-- /BENCH:TABLE -->")
        if start != -1 and end != -1:
            text = text[:start] + "<!-- BENCH:TABLE -->\n\n" + table + "\n\n" + text[end:]
            evidence.write_text(text, encoding="utf-8")
        else:
            raise SystemExit("04-overhead.md present but missing BENCH:TABLE markers")
    else:
        raise SystemExit(f"{evidence} not found — write the methodology doc first")

    print(f"wrote {out_a}\nwrote {out_b}\nupdated {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
