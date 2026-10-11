"""Cross-signal correlation verification framework (PLAN §3.A, feature #23).

The script injects a known W3C ``traceparent`` into ``POST /orders`` and then
asserts that the same ``trace_id`` shows up across the three observability
signals, in each cloud it was asked to check:

* ``--include-gcp``  -> Cloud Trace (A10), Cloud Logging (A11), Cloud
  Monitoring via Google Managed Prometheus (A12).
* ``--include-aws``  -> X-Ray cross-cloud check (A13). AWS is currently
  sandboxed (#1/#6 blocked), so A13 SKIPs with an explicit reason until AWS
  data exists. Assertions 3-9 belong to feature #6 and are registered as SKIP
  with reason "feature #6 (AWS)".

Design constraints (PLAN §3.A): cost ≈ $0 — only read APIs inside free
quotas, authentication via the ADC token of ``gcloud auth print-access-token``
and plain ``urllib`` (no extra SDKs in requirements.txt).

Outputs ``docs/evidence/03-correlation.json`` plus a mirrored Markdown table
``03-correlation.md``. Exit code is non-zero when a REQUIRED assertion ends in
FAIL; SKIP is never a failure.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess  # noqa: S404  (gcloud/aws CLIs are the documented auth path)
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

DEFAULT_PROJECT = "otel-lab-obs-2610"
DEFAULT_ENDPOINT = "http://localhost:8080"
DEFAULT_WINDOW_SECONDS = 120

# Order payload for A1 (models.py defaults; SKU seeded by init-scripts).
ORDER_PAYLOAD: dict[str, Any] = {
    "items": [{"sku": "LAPTOP-001", "quantity": 1, "unit_price": 2500000}],
    "payment_provider": "mock-payment",
}

# Span display names emitted by the OTel SDK per service (PLAN 1.D). The sets
# are disjoint on purpose: "inventory.reserve" is emitted by BOTH services
# (client span in service-a, server span in service-b), so it cannot be used
# to attribute a trace to one service.
SERVICE_A_SPANS = frozenset(
    {"order.process", "order.validate", "payment.authorize", "order.persist"}
)
SERVICE_B_SPANS = frozenset({"inventory.query", "payment.gateway_call"})

CLOUDTRACE_URL = "https://cloudtrace.googleapis.com/v2/projects/{project}/traces/{trace_id}"
LOGGING_LIST_URL = "https://logging.googleapis.com/v2/entries:list"
DESCRIPTORS_URL = "https://monitoring.googleapis.com/v3/projects/{project}/metricDescriptors"
TIMESERIES_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"

# Expected GMP metric type for the business counter (metrics.py instrument is
# already named orders_created_total). The real descriptor is discovered with
# a starts_with filter and this exact type is preferred when present.
GMP_ORDERS_COUNTER = "prometheus.googleapis.com/orders_created_total/counter"

HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")

# Managed backends are eventually consistent: retry reads a few times before
# declaring a signal missing (each retry is a free read API call).
READ_RETRIES = 3
READ_RETRY_DELAY_S = 2.0

# Catalog of the 13 PLAN §3.A assertions: (id, nombre, obligatoria).
ASSERTION_CATALOG: list[tuple[int, str, bool]] = [
    (1, "Inyección de traceparent W3C en POST /orders", True),
    (2, "Header x-trace-id de la respuesta == trace inyectado", True),
    (3, "X-Ray: segmentos de service-a y service-b con parent_id correcto", True),
    (4, "X-Ray: atributo db.system=postgresql en segmento de service-b", True),
    (5, "CloudWatch Logs: ≥1 entrada con trace_id por servicio", True),
    (6, "CloudWatch Metrics: orders_created_total incrementado en la ventana", True),
    (7, "Consistencia de resource entre traza, log y métrica por nube", True),
    (8, "Prometheus: exemplars con trace_id de traza existente", True),
    (9, "Límites de cardinalidad (CloudWatch/Cloud Monitoring/Prometheus)", True),
    (10, "Cloud Trace: traza W3C con spans de service-a y service-b", True),
    (11, "Cloud Logging: ≥1 entrada con trace_id por servicio", True),
    (12, "Cloud Monitoring: orders_created_total incrementado en la ventana", True),
    (13, "Cruzada GCP↔AWS: mismo trace_id en señales de ambas nubes", True),
]

AWS_SKIP_REASON = "AWS blocked (#1/#6)"
FEATURE6_SKIP_REASON = "feature #6 (AWS)"
GCP_SKIP_REASON = "GCP no incluido (ejecutar con --include-gcp)"


class HttpError(RuntimeError):
    """Non-2xx HTTP answer from any REST API used by the script."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class AuthError(RuntimeError):
    """The ADC access token could not be obtained."""


# Exceptions that mean "the backend call itself failed" (vs. a valid answer
# that disproves an assertion): they become a FAIL row with the API reason.
API_ERRORS = (HttpError, AuthError, urllib.error.URLError, OSError)


@dataclass
class AssertionResult:
    """One row of the correlation report."""

    id: int
    nombre: str
    obligatoria: bool
    estado: str = SKIP
    razon: str = ""
    detalles: dict[str, Any] = field(default_factory=dict)

    def finish(self, estado: str, razon: str = "", detalles: dict[str, Any] | None = None) -> None:
        """Record the final state of the assertion."""
        self.estado = estado
        self.razon = razon
        self.detalles = detalles or {}


@dataclass
class CorrelationReport:
    """Registry of the 13 assertion results for one run."""

    results: list[AssertionResult] = field(default_factory=list)

    def register(
        self,
        assertion_id: int,
        estado: str,
        razon: str = "",
        detalles: dict[str, Any] | None = None,
    ) -> AssertionResult:
        """Finish a catalog assertion by id and return it."""
        meta = next(m for m in ASSERTION_CATALOG if m[0] == assertion_id)
        result = AssertionResult(id=meta[0], nombre=meta[1], obligatoria=meta[2])
        result.finish(estado, razon, detalles)
        self.results.append(result)
        return result

    @property
    def required_failures(self) -> list[AssertionResult]:
        """Required assertions that ended in FAIL (SKIP is not a failure)."""
        return [r for r in self.results if r.obligatoria and r.estado == FAIL]

    def to_payload(self, timestamp: str, project: str, endpoint: str, trace_id: str) -> dict[str, Any]:
        """Serialize the run to the evidence JSON schema."""
        return {
            "timestamp": timestamp,
            "project": project,
            "endpoint": endpoint,
            "trace_id": trace_id,
            "aserciones": [
                {
                    "id": r.id,
                    "nombre": r.nombre,
                    "estado": r.estado,
                    "razon": r.razon,
                    "detalles": r.detalles,
                }
                for r in sorted(self.results, key=lambda r: r.id)
            ],
        }


def generate_traceparent() -> tuple[str, str]:
    """Build a valid W3C traceparent (version 00, sampled flags 01).

    Returns:
        ``(traceparent_header, trace_id)`` with the trace id as 32 lowercase
        hex characters.
    """
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01", trace_id


def http_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, str], Any]:
    """Perform one JSON-over-HTTP request with urllib (no third-party SDKs).

    Returns:
        ``(status, lowercased_response_headers, parsed_body)``; the body is
        parsed as JSON when possible, otherwise returned as text.

    Raises:
        HttpError: on any non-2xx answer.
        urllib.error.URLError: on transport failures (DNS, refused, timeout).
    """
    data: bytes | None = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, method=method)
    for key, value in request_headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            return response.status, resp_headers, _maybe_json(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise HttpError(exc.code, raw) from exc


def _maybe_json(raw: str) -> Any:
    """Parse ``raw`` as JSON, falling back to the plain text."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def gcloud_access_token(project: str, timeout: float = 30.0) -> str:
    """Return an OAuth access token from the gcloud ADC (no SDK dependency).

    Raises:
        AuthError: when the gcloud CLI is missing or the token cannot be
            printed (no ADC configured).
    """
    cmd = [
        "gcloud",
        "auth",
        "print-access-token",
        f"--project={project}",
        "--format=value(access_token)",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"gcloud no disponible: {exc}") from exc
    if proc.returncode != 0:
        raise AuthError(f"gcloud auth print-access-token fallo: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _span_display_name(span: dict[str, Any]) -> str:
    """Extract the OTel span name from a Cloud Trace v2 span object."""
    display = span.get("displayName")
    if isinstance(display, dict):
        return str(display.get("value", ""))
    return str(span.get("name", ""))


def _retry_read(fn: Any, retry_on_empty: Any = None) -> Any:
    """Call ``fn`` up to READ_RETRIES times while it yields "empty" answers.

    Managed backends (Cloud Trace/Logging) ingest with a delay, so a first
    empty read is not conclusive. An exception (auth/HTTP) is final: it
    propagates on the first attempt.
    """
    last: Any = None
    for attempt in range(READ_RETRIES):
        last = fn()
        if retry_on_empty is None or not retry_on_empty(last):
            return last
        if attempt < READ_RETRIES - 1:
            time.sleep(READ_RETRY_DELAY_S)
    return last


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def check_traceparent_injection(
    report: CorrelationReport, endpoint: str, traceparent: str
) -> dict[str, str] | None:
    """A1: inject the traceparent via POST /orders and read the response.

    Returns the lowercased response headers when the endpoint answered (A1
    PASS — any HTTP answer counts, error statuses included, because the
    assertion is about W3C injection, not about the order outcome), or
    ``None`` when it was unreachable (A1 FAIL).
    """
    url = f"{endpoint.rstrip('/')}/orders"
    headers = {"traceparent": traceparent}
    try:
        status, resp_headers, _body = http_request("POST", url, headers=headers, body=ORDER_PAYLOAD)
    except HttpError as exc:
        # The service answered with an error status: the injection worked.
        report.register(
            1,
            PASS,
            detalles={"url": url, "status": exc.status, "respuesta": exc.body[:200]},
        )
        return {}
    except urllib.error.URLError as exc:
        report.register(1, FAIL, razon=f"endpoint no alcanzable: {exc.reason}", detalles={"url": url})
        return None

    report.register(1, PASS, detalles={"url": url, "status": status})
    return resp_headers


def check_response_trace_header(
    report: CorrelationReport, resp_headers: dict[str, str], trace_id: str
) -> None:
    """A2: the ``x-trace-id`` response header equals the injected trace id."""
    if not resp_headers:
        report.register(
            2, FAIL, razon="header x-trace-id ausente (service-a devolvio status de error)"
        )
        return
    actual = resp_headers.get("x-trace-id")
    if actual is None:
        report.register(
            2,
            FAIL,
            razon="header x-trace-id ausente (service-a sin OTel o version antigua)",
            detalles={"headers": sorted(resp_headers)},
        )
    elif actual == trace_id:
        report.register(2, PASS, detalles={"x-trace-id": actual})
    else:
        report.register(
            2, FAIL, razon="x-trace-id != trace inyectado",
            detalles={"esperado": trace_id, "recibido": actual},
        )


def check_cloud_trace(report: CorrelationReport, token: str, project: str, trace_id: str) -> bool:
    """A10: Cloud Trace v2 holds the injected trace with spans of both services.

    Cloud Trace preserves the W3C 32-hex id (PLAN §3.B note), so the id is
    compared directly. Returns True on PASS.
    """
    url = CLOUDTRACE_URL.format(project=project, trace_id=trace_id)
    auth = {"Authorization": f"Bearer {token}"}

    def fetch() -> Any:
        try:
            _status, _headers, body = http_request("GET", url, headers=auth)
            return body
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise

    body = _retry_read(fetch, retry_on_empty=lambda b: not b or not b.get("spans"))
    if not isinstance(body, dict) or not body.get("spans"):
        report.register(
            10, FAIL, razon="traza inyectada no encontrada en Cloud Trace", detalles={"url": url}
        )
        return False

    names = {_span_display_name(s) for s in body["spans"] if isinstance(s, dict)}
    found_a = sorted(names & SERVICE_A_SPANS)
    found_b = sorted(names & SERVICE_B_SPANS)
    if found_a and found_b:
        report.register(
            10,
            PASS,
            detalles={
                "spans_service_a": found_a,
                "spans_service_b": found_b,
                "total_spans": len(body["spans"]),
            },
        )
        return True
    report.register(
        10,
        FAIL,
        razon="la traza existe pero faltan spans de algun servicio",
        detalles={"spans_service_a": found_a, "spans_service_b": found_b},
    )
    return False


def _entry_service(entry: dict[str, Any]) -> str | None:
    """Attribute a Cloud Logging entry to service-a/service-b.

    The collector exports with ``resource.type=k8s_container`` and the
    container name label; the structlog ``name`` field in jsonPayload is the
    fallback.
    """
    labels = (entry.get("resource") or {}).get("labels") or {}
    svc = labels.get("container_name") or (entry.get("jsonPayload") or {}).get("name")
    return svc if svc in {"service-a", "service-b"} else None


def check_cloud_logging(report: CorrelationReport, token: str, project: str, trace_id: str) -> bool:
    """A11: Cloud Logging holds ≥1 entry per service carrying the trace id.

    Primary filter is the normalized ``trace`` field (the collector's
    transform/logs writes ``logging.googleapis.com/trace``); the fallback is
    the raw ``jsonPayload.trace_id``. Returns True on PASS.
    """
    auth = {"Authorization": f"Bearer {token}"}
    filters = [
        f'trace:"projects/{project}/traces/{trace_id}"',
        f'jsonPayload.trace_id="{trace_id}"',
    ]
    entries: list[dict[str, Any]] = []
    used_filter = filters[0]
    for filt in filters:
        used_filter = filt

        def fetch(f: str = filt) -> list[dict[str, Any]]:
            _status, _headers, body = http_request(
                "POST",
                LOGGING_LIST_URL,
                headers=auth,
                body={"projectNames": [f"projects/{project}"], "filter": f, "pageSize": 256},
            )
            return (body or {}).get("entries", []) if isinstance(body, dict) else []

        entries = _retry_read(fetch, retry_on_empty=lambda e: not e)
        if entries:
            break

    services = sorted({svc for e in entries if (svc := _entry_service(e))})
    if {"service-a", "service-b"} <= set(services):
        report.register(
            11, PASS,
            detalles={"entradas": len(entries), "servicios": services, "filtro": used_filter},
        )
        return True
    report.register(
        11,
        FAIL,
        razon="no hay >=1 entrada por servicio para el trace inyectado",
        detalles={"entradas": len(entries), "servicios": services, "filtro": used_filter},
    )
    return False


def _point_value(point: dict[str, Any]) -> float:
    """Extract the numeric value of a Cloud Monitoring timeSeries point."""
    value = point.get("value") or {}
    for key in ("int64Value", "doubleValue"):
        if key in value:
            return float(value[key])
    return 0.0


def check_cloud_monitoring(report: CorrelationReport, token: str, project: str, window: int) -> bool:
    """A12: the GMP counter ``orders_created_total`` grew inside ``window``.

    The real metric type is discovered via a ``starts_with`` descriptor
    filter (the expected type is preferred when it exists). GMP delivers
    counters as cumulative points, so the increment per series is
    last-first (a single point contributes its cumulative value, which is
    >0 only if orders were created by that series' process). Returns True on
    PASS.
    """
    auth = {"Authorization": f"Bearer {token}"}

    descriptor_filter = 'metric.type=starts_with("prometheus.googleapis.com/orders_created")'
    desc_url = (
        f"{DESCRIPTORS_URL}?{urllib.parse.urlencode({'filter': descriptor_filter, 'pageSize': '100'})}"
    )
    _status, _headers, desc_body = http_request("GET", desc_url, headers=auth)
    types = (
        [
            d.get("type", "")
            for d in (desc_body or {}).get("metricDescriptors", [])
            if isinstance(d, dict)
        ]
        if isinstance(desc_body, dict)
        else []
    )

    if GMP_ORDERS_COUNTER in types:
        metric_type = GMP_ORDERS_COUNTER
    elif types:
        metric_type = types[0]
    else:
        report.register(
            12,
            FAIL,
            razon="no existe descriptor GMP para orders_created_total",
            detalles={"descriptores_encontrados": types},
        )
        return False

    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=window)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {
        "metric.type": metric_type,
        "interval": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        "pageSize": "1000",
    }
    ts_url = f"{TIMESERIES_URL}?{urllib.parse.urlencode(params)}"
    _status, _headers, ts_body = http_request("GET", ts_url, headers=auth)
    series = (ts_body or {}).get("timeSeries", []) if isinstance(ts_body, dict) else []

    total_increment = 0.0
    per_series: list[float] = []
    for s in series if isinstance(series, list) else []:
        points = sorted(
            (s.get("points") or []),
            key=lambda p: (p.get("interval") or {}).get("startTime", ""),
        )
        if not points:
            continue
        values = [_point_value(p) for p in points]
        increment = values[-1] - values[0] if len(values) > 1 else values[0]
        per_series.append(increment)
        total_increment += increment

    details = {
        "metric_type": metric_type,
        "ventana_segundos": window,
        "series": len(per_series),
        "incremento_total": total_increment,
    }
    if total_increment > 0:
        report.register(12, PASS, detalles=details)
        return True
    report.register(
        12, FAIL, razon="el contador orders_created_total no incremento en la ventana", detalles=details
    )
    return False


def check_cross_cloud(report: CorrelationReport, include_aws: bool, gcp_trace_ok: bool, window: int) -> None:
    """A13: the same W3C trace shows up in AWS and GCP signals.

    While AWS is sandboxed (#1/#6) there is no X-Ray data, so the assertion
    SKIPs with an explicit reason. With ``--include-aws`` the X-Ray side is
    checked by structure (segments of service-a and service-b in the window),
    NOT by id equality: the awsxray exporter rewrites the 128-bit id (PLAN
    §3.B note), the W3C trace id is only the pivot on the GCP side.
    """
    if not include_aws:
        report.register(13, SKIP, razon=AWS_SKIP_REASON)
        return

    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=window)
    cmd = [
        "aws", "xray", "get-trace-summaries",
        "--start-time", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--end-time", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        report.register(13, SKIP, razon=f"{AWS_SKIP_REASON} (aws CLI no disponible: {exc.__class__.__name__})")
        return
    if proc.returncode != 0:
        report.register(13, SKIP, razon=f"{AWS_SKIP_REASON} (aws CLI: {proc.stderr.strip()[:200]})")
        return
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report.register(13, SKIP, razon=f"{AWS_SKIP_REASON} (salida aws CLI no parseable)")
        return

    summaries = payload.get("TraceSummaries", [])

    def covers_both_services(summary: dict[str, Any]) -> bool:
        segment_names = {seg.get("name") for seg in (summary.get("Segments") or [])}
        service_names = {svc.get("name") for svc in (summary.get("ServiceIds") or [])}
        return {"service-a", "service-b"} <= segment_names | service_names

    matched = [s for s in summaries if covers_both_services(s)]
    if gcp_trace_ok and matched:
        report.register(
            13,
            PASS,
            detalles={
                "pivot": "trace_id W3C; X-Ray verificado por estructura (PLAN §3.B)",
                "summaries": len(summaries),
                "coincidentes": len(matched),
            },
        )
    elif summaries:
        report.register(
            13,
            FAIL,
            razon="hay trazas AWS pero ninguna cubre service-a+service-b en la ventana",
            detalles={"summaries": len(summaries)},
        )
    else:
        report.register(13, SKIP, razon=f"{AWS_SKIP_REASON} (sin datos X-Ray en la ventana)")


def _run_gcp_check(
    report: CorrelationReport,
    assertion_id: int,
    fn: Any,
    *args: Any,
) -> Any:
    """Execute one GCP assertion converting API errors into a FAIL row."""
    try:
        return fn(report, *args)
    except API_ERRORS as exc:
        report.register(assertion_id, FAIL, razon=f"API GCP fallo: {exc}")
        return False


def run_assertions(args: argparse.Namespace) -> tuple[CorrelationReport, str]:
    """Execute the whole catalog once and return the report + injected id."""
    report = CorrelationReport()
    traceparent, trace_id = generate_traceparent()

    resp_headers = check_traceparent_injection(report, args.endpoint, traceparent)
    if resp_headers is None:
        report.register(2, FAIL, razon="A1 fallo: endpoint no alcanzable")
    else:
        check_response_trace_header(report, resp_headers, trace_id)

    for assertion_id in range(3, 10):
        report.register(assertion_id, SKIP, razon=FEATURE6_SKIP_REASON)

    gcp_trace_ok = False
    if args.include_gcp:
        try:
            token = gcloud_access_token(args.project)
        except AuthError as exc:
            for assertion_id in (10, 11, 12):
                report.register(assertion_id, FAIL, razon=f"ADC/token GCP no disponible: {exc}")
        else:
            gcp_trace_ok = _run_gcp_check(report, 10, check_cloud_trace, token, args.project, trace_id)
            _run_gcp_check(report, 11, check_cloud_logging, token, args.project, trace_id)
            _run_gcp_check(report, 12, check_cloud_monitoring, token, args.project, args.window)
    else:
        for assertion_id in (10, 11, 12):
            report.register(assertion_id, SKIP, razon=GCP_SKIP_REASON)

    check_cross_cloud(report, args.include_aws, gcp_trace_ok, args.window)
    return report, trace_id


def render_markdown(payload: dict[str, Any]) -> str:
    """Mirror of the JSON evidence as a Markdown table (PLAN §3.A)."""
    lines = [
        "# 03 — Correlación cross-signal (verify_correlation.py)",
        "",
        f"> **Fecha:** {payload['timestamp']} · **Proyecto:** `{payload['project']}` · "
        f"**Endpoint:** `{payload['endpoint']}` · **trace_id inyectado:** `{payload['trace_id']}`",
        "",
        "| # | Aserción | Estado | Razón |",
        "|---|---|---|---|",
    ]
    for a in payload["aserciones"]:
        razon = a["razon"].replace("|", "\\|") or "—"
        lines.append(f"| {a['id']} | {a['nombre']} | **{a['estado']}** | {razon} |")
    lines += [
        "",
        "Detalles por aserción en `03-correlation.json` (campo `detalles`).",
        "SKIP no es fallo; el exit code !=0 solo si una aserción OBLIGATORIA falla (PLAN §3.A).",
        "",
    ]
    return "\n".join(lines)


def write_evidence(payload: dict[str, Any], out_dir: Path) -> Path:
    """Write the JSON + Markdown evidence pair and return the JSON path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "03-correlation.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "03-correlation.md").write_text(render_markdown(payload), encoding="utf-8")
    return json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI of the framework (PLAN §3.A + feature #23 brief)."""
    parser = argparse.ArgumentParser(description="Verify W3C cross-signal correlation (PLAN §3.A)")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="base URL of service-a")
    parser.add_argument(
        "--include-gcp", action="store_true",
        help="run the Cloud Trace/Logging/Monitoring assertions (A10-A12)",
    )
    parser.add_argument(
        "--include-aws", action="store_true",
        help="run the X-Ray cross-cloud assertion (A13); SKIPs while #1/#6 are blocked",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="GCP project id")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_SECONDS, help="read window in seconds")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DIR,
        help="evidence directory (docs/evidence by default; tests point it to a tmp dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: run, write evidence, exit non-zero on required failures."""
    # Console-safe output on Windows (cp1252 cannot encode ≥ or arrows).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    report, trace_id = run_assertions(args)

    payload = report.to_payload(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        project=args.project,
        endpoint=args.endpoint,
        trace_id=trace_id,
    )
    json_path = write_evidence(payload, args.out_dir)

    for a in payload["aserciones"]:
        suffix = f" — {a['razon']}" if a["razon"] else ""
        print(f"[{a['estado']}] #{a['id']:>2} {a['nombre']}{suffix}")
    print(f"evidencia: {json_path}")
    return 1 if report.required_failures else 0


if __name__ == "__main__":
    sys.exit(main())
