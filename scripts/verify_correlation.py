"""Cross-signal correlation verification framework (PLAN §3.A).

The script injects a known W3C ``traceparent`` into ``POST /orders`` and then
asserts that the same ``trace_id`` shows up across the three observability
signals, in each cloud it was asked to check:

* ``--include-aws``  -> X-Ray (A3, A4), CloudWatch Logs (A5), CloudWatch
  metrics via EMF (A6), resource consistency across the three AWS signals
  (A7), Prometheus exemplars scraped from the collector's :8889 exporter
  reached through the operator's temporary ingress (A8) and the
  cardinality limits (A9). AWS calls go through boto3 (lazy import: local
  mode and offline tests never need the SDK installed).
* ``--include-gcp``  -> Cloud Trace (A10), Cloud Logging (A11), Cloud
  Monitoring via Google Managed Prometheus (A12).
* Both flags together additionally evaluate A13: the same W3C ``trace_id``
  must be found in an AWS signal (X-Ray or CloudWatch Logs) AND in a GCP
  signal (Cloud Trace or Cloud Logging). With one flag missing A13 SKIPs
  with an explicit reason.

X-Ray verification is STRUCTURAL, never by id equality (R-03): the awsxray
exporter rewrites the 128-bit id to the ``1-<8hex>-<24hex>`` form, so the
W3C id is converted deterministically (``w3c_to_xray_trace_id``) and the
parent/child relationship between the service-a and service-b segments is
what proves propagation.

Design constraints (PLAN §3.A): cost ≈ $0 — only read APIs inside free
quotas (``GetMetricStatistics`` and never ``GetMetricData``, ``FilterLogEvents``
and never Logs Insights), GCP authentication via the ADC token of
``gcloud auth print-access-token`` and plain ``urllib`` (no Google SDK).

Outputs ``docs/evidence/03-correlation.json`` plus a mirrored Markdown table
``03-correlation.md``. Exit code is non-zero when a REQUIRED assertion ends in
FAIL; SKIP is never a failure.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
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

# Cloud Trace's REST surface is v1 (v2 is gRPC-only; v2 REST paths return
# HTML 404). v2/v1 preserve the W3C 32-hex id, so the id compares directly.
CLOUDTRACE_URL = "https://cloudtrace.googleapis.com/v1/projects/{project}/traces/{trace_id}"
LOGGING_LIST_URL = "https://logging.googleapis.com/v2/entries:list"
DESCRIPTORS_URL = "https://monitoring.googleapis.com/v3/projects/{project}/metricDescriptors"
TIMESERIES_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"

# Expected GMP metric type for the business counter (metrics.py instrument is
# already named orders_created_total). The real descriptor is discovered with
# a starts_with filter and this exact type is preferred when present.
GMP_ORDERS_COUNTER = "prometheus.googleapis.com/orders_created_total/counter"

# --- AWS side (PLAN §3.A, cost ≈ $0: read APIs inside the free tier) --------
AWS_DEFAULT_PROFILE = "otel-lab"
AWS_DEFAULT_REGION = "us-east-1"
EMF_NAMESPACE = "OtelLab"
ORDERS_COUNTER = "orders_created_total"
# The OTel collector's awscloudwatchlogs exporter writes the app logs to
# /otel/labs/ecs-cluster/<service>; the awslogs DRIVER groups
# (/otel/labs/ecs-platform/*) carry container noise and are NOT the target.
EXPORTER_LOG_GROUP_TEMPLATE = "/otel/labs/ecs-cluster/{service}"
CORRELATION_SERVICES = ("service-a", "service-b")
# PLAN §13 documents GetMetricStatistics with --period 300 / Sum.
METRIC_PERIOD_SECONDS = 300
# Bounded reads (never a dashboard-style loop): paging caps keep the API
# call count in the tens per run (R-05/R-14).
MAX_SUMMARY_PAGES = 10
MAX_STRUCTURAL_SCAN_TRACES = 30
# X-Ray BatchGetTraces rejects more than 5 ids per call ("Exceeding maximum
# query size: 5", verified live 2026-10-12). The structural scan pages the
# window in chunks of this size.
BATCH_GET_TRACES_MAX_IDS = 5
CUSTOM_METRICS_LIMIT = 10
PROM_SERIES_LIMIT = 1500
# Prometheus exporter of the collector. SSM port-forwarding to a Fargate task
# is NOT available (rejected live 2026-10-12), so the AWS run reaches it on the
# task's public ENI IP (:8889) after `make aws-access-grant` — the default here
# matches the local compose stack, which exposes the same port on localhost.
DEFAULT_PROMETHEUS_URL = "http://localhost:8889/metrics"

HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")
TRACE_ID_FIELD_RE = re.compile(r'"trace_id":\s*"([0-9a-f]{32})"')
EXEMPLAR_TRACE_RE = re.compile(r'trace_id="([0-9a-f]{32})"')

# structlog writes "logger": "service-a|service-b" inside the JSON line; the
# filelog->OTLP pipeline lands it in textPayload (verified live 2026-10-11).
LOGGER_RE = re.compile(r'"logger":\s*"(service-[ab])"')

# Managed backends ingest with a delay (batch exporter + backend lag is
# 30-90 s): poll until the signal appears or the deadline expires.
POLL_DEADLINE_S = 180.0
POLL_INTERVAL_S = 12.0

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

AWS_SKIP_REASON = "AWS no incluido (ejecutar con --include-aws)"
GCP_SKIP_REASON = "GCP no incluido (ejecutar con --include-gcp)"
CROSS_CLOUD_SKIP_REASON = "A13 requiere --include-aws y --include-gcp simultaneos (trafico cruzando las nubes)"


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

    The binary is resolved with ``shutil.which`` because on Windows the gcloud
    CLI is a ``.cmd`` shim that ``subprocess`` cannot find by bare name
    (``WinError 2``).

    Raises:
        AuthError: when the gcloud CLI is missing or the token cannot be
            printed (no ADC configured).
    """
    gc = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if gc is None:
        raise AuthError("gcloud no encontrado en PATH (SDK de Google Cloud no instalado)")
    cmd = [
        gc,
        "auth",
        "print-access-token",
        f"--project={project}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"gcloud no disponible: {exc}") from exc
    if proc.returncode != 0:
        raise AuthError(f"gcloud auth print-access-token fallo: {proc.stderr.strip()[:300]}")
    token = proc.stdout.strip()
    if not token:
        # print-access-token does not honour --format: send it bare.
        raise AuthError("gcloud auth print-access-token devolvio un token vacio (ADC?)")
    return token


# ---------------------------------------------------------------------------
# AWS seams and pure helpers (unit-testable without boto3 or network)
# ---------------------------------------------------------------------------


def w3c_to_xray_trace_id(trace_id: str) -> str:
    """Convert a W3C 32-hex trace id to the X-Ray ``1-<epoch8>-<suffix24>`` form.

    R-03: the awsxray exporter rewrites the 128-bit id, so the two strings are
    never compared literally — this deterministic mapping is only used to look
    the trace up inside X-Ray; the assertion itself checks STRUCTURE (segments
    and parent ids), not id equality.

    Args:
        trace_id: 32 lowercase hex characters (W3C trace id).

    Returns:
        The equivalent X-Ray trace id, e.g. ``1-aabbccdd-eeff00...``.
    """
    if not HEX32_RE.match(trace_id):
        raise ValueError(f"trace_id no es 32 hex: {trace_id!r}")
    return f"1-{trace_id[:8]}-{trace_id[8:32]}"


def make_aws_client(service: str, profile: str, region: str) -> Any:
    """Build one boto3 client (lazy import: local mode never needs boto3).

    Args:
        service: boto3 service name (``xray``, ``logs``, ``cloudwatch``).
        profile: AWS credentials profile (``otel-lab`` by default).
        region: AWS region (``us-east-1`` by default).

    Raises:
        ImportError: when boto3 is not installed (dev requirements missing).
    """
    import boto3  # deferred: offline tests and --include-gcp runs skip this

    session = boto3.Session(
        profile_name=profile or None,
        region_name=region or None,
    )
    return session.client(service)


def fetch_trace_summary_ids(xray_client: Any, window: int) -> list[str]:
    """All X-Ray trace ids seen in the last ``window`` seconds (bounded paging).

    Uses only ``GetTraceSummaries`` (free tier) with a hard page cap, never a
    loop that could turn into a paid dashboard pattern. The boto3 API takes
    the window as top-level ``StartTime``/``EndTime`` datetimes — a
    ``TimeRange`` dict is rejected with ParamValidationError (verified live
    2026-10-12).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=window)
    ids: list[str] = []
    token: str | None = None
    for _ in range(MAX_SUMMARY_PAGES):
        params: dict[str, Any] = {"StartTime": start, "EndTime": end}
        if token:
            params["NextToken"] = token
        response = xray_client.get_trace_summaries(**params)
        for summary in response.get("TraceSummaries", []):
            # botocore model: TraceIdSummary member is "Id" (locationName
            # "id"); "TraceId" does NOT exist in the response (verified live
            # 2026-10-12: pages came back with 102 summaries and the script
            # collected 0 ids because it read a phantom key).
            trace_id = summary.get("Id")
            if trace_id:
                ids.append(trace_id)
        token = response.get("NextToken")
        if not token:
            break
    return ids


def parse_trace_segments(batch_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ``BatchGetTraces`` response into segment documents (dicts).

    Three wire shapes have to be tolerated (all verified live 2026-10-12):
    the legacy ``TraceSegments`` list of JSON strings, the botocore 1.43
    ``Segments`` list whose items are ``{"Document": "<json str>", "Id": ...}``
    envelopes, and plain dicts (offline tests). Each flattened segment
    document is the X-Ray segment itself (snake_case ``parent_id`` etc.).
    """
    docs: list[dict[str, Any]] = []
    for trace in batch_response.get("Traces", []):
        raw_segments = trace.get("Segments") or trace.get("TraceSegments") or []
        for raw in raw_segments:
            if isinstance(raw, dict) and "Document" in raw:
                raw = raw["Document"]
            doc = raw if isinstance(raw, dict) else json.loads(raw)
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def service_segment_ids(docs: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Map each correlation service to the ids it owns in a trace.

    A service owns its segment, the subsegments inlined in that segment
    document, and (transitively) any separate document whose ``parent_id``
    chain lands inside that set.
    """
    owned: dict[str, set[str]] = {svc: set() for svc in CORRELATION_SERVICES}
    for doc in docs:
        name = doc.get("name")
        if name in owned and doc.get("id"):
            owned[name].add(doc["id"])
            for sub in doc.get("subsegments") or []:
                if isinstance(sub, dict) and sub.get("id"):
                    owned[name].add(sub["id"])
    changed = True
    while changed:
        changed = False
        for doc in docs:
            doc_id = doc.get("id")
            # A document that is itself a named service segment belongs only
            # to its own service, never to its caller's set.
            if not doc_id or doc.get("name") in owned:
                continue
            for ids in owned.values():
                if doc_id not in ids and doc.get("parent_id") in ids:
                    ids.add(doc_id)
                    changed = True
    return owned


def xray_parent_link_ok(docs: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    """A3 structural check: service-b's segment hangs off a service-a subsegment.

    Returns:
        ``(ok, details)`` where ok requires a service-a segment, a service-b
        segment, and service-b's ``parent_id`` equal to the id of a span
        (subsegment) owned by service-a — i.e. the client span that called it.
    """
    owned = service_segment_ids(docs)
    a_segments = [d for d in docs if d.get("name") == "service-a"]
    b_segments = [d for d in docs if d.get("name") == "service-b"]
    matched = [
        b["parent_id"]
        for b in b_segments
        if b.get("parent_id") and b["parent_id"] in owned["service-a"]
    ]
    ok = bool(a_segments) and bool(b_segments) and bool(matched)
    details: dict[str, Any] = {
        "segmentos_service_a": len(a_segments),
        "segmentos_service_b": len(b_segments),
        "parent_ids_service_b": sorted(
            {b.get("parent_id") for b in b_segments if b.get("parent_id")}
        ),
        "ids_pertenecientes_a_service_a": len(owned["service-a"]),
        "enlace_parent_id_correcto": bool(matched),
    }
    return ok, details


def xray_graph_edges(graph: dict[str, Any]) -> list[tuple[str, str]]:
    """(source service name, target service name) pairs of a GetTraceGraph response.

    X-Ray's graph view resolves the ``parent_id`` chain INCLUDING the remote
    subsegment documents that BatchGetTraces omits (verified live 2026-10-12:
    the OTLP->X-Ray translator sends the httpx client span as its own
    ``type: subsegment`` doc; X-Ray stores it but the segment listing never
    returns it, leaving service-b's ``parent_id`` pointing at an invisible id).
    """
    services = graph.get("Services", []) or []
    by_ref = {s.get("ReferenceId"): s for s in services}
    edges: list[tuple[str, str]] = []
    for svc in services:
        for edge in svc.get("Edges", []) or []:
            target = by_ref.get(edge.get("ReferenceId"))
            if target is not None:
                edges.append((svc.get("Name", ""), target.get("Name", "")))
    return edges


def find_attribute(obj: Any, key: str) -> Any:
    """Recursively search a dict/list structure for ``key`` (first hit wins).

    X-Ray segment documents park OTel span attributes under ``metadata``
    (namespaced), ``annotations`` or the document root depending on the
    exporter version; a recursive search is tolerant to all three shapes.
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_attribute(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_attribute(item, key)
            if found is not None:
                return found
    return None


def log_event_trace_id(message: str) -> str | None:
    """Extract the W3C ``trace_id`` from a CloudWatch log event message.

    The structlog JSON line carries ``"trace_id": "<32 hex>"``; the message is
    parsed as JSON first and falls back to a regex over the raw text.
    """
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        trace_id = payload.get("trace_id")
        if isinstance(trace_id, str) and HEX32_RE.match(trace_id):
            return trace_id
    match = TRACE_ID_FIELD_RE.search(message)
    return match.group(1) if match else None


def emf_window_increment(datapoints: list[dict[str, Any]]) -> float:
    """Total increment of an EMF counter inside the window.

    The awsemf exporter publishes each bucket as the DELTA observed in that
    bucket (live 2026-10-12: ``[1.0, 0.0, 0.0]`` after one POST), so the
    window's increment is the SUM of the bucket values — last-minus-first
    would go negative whenever the traffic lands in the first bucket.
    """
    total = 0.0
    for point in datapoints:
        for key in ("Sum", "Average"):
            if key in point:
                total += float(point[key])
                break
    return total


def parse_prometheus_scrape(text: str) -> tuple[int, dict[str, list[str]]]:
    """Count samples and collect exemplar trace ids from a scrape body.

    Returns:
        ``(series_count, exemplars)`` where ``exemplars`` maps a metric name
        to the 32-hex ``trace_id`` values found in its exemplar labels. Only
        histogram bucket series whose name contains ``duration`` are kept as
        latency exemplars (A8); every non-comment line counts as one series
        (A9).
    """
    series_count = 0
    exemplars: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample, sep, rest = stripped.partition(" # ")
        series_count += 1
        if not sep:
            continue
        name = sample.split("{", 1)[0].split(" ", 1)[0]
        if "duration" in name and name.endswith("_bucket"):
            match = EXEMPLAR_TRACE_RE.search(rest)
            if match:
                exemplars.setdefault(name, []).append(match.group(1))
    return series_count, exemplars


def dimensions_to_attributes(dimensions: list[dict[str, str]]) -> dict[str, str]:
    """Normalize CloudWatch metric dimensions to OTel resource keys.

    ``ServiceName`` (EMF default dimension naming) maps to ``service.name``;
    every other dimension keeps its own name, so ``cloud.provider`` and
    ``deployment.environment`` are found when the resource processor stamps
    them through the EMF pipeline.
    """
    attrs: dict[str, str] = {}
    for dim in dimensions:
        name, value = dim.get("Name"), dim.get("Value")
        if not name or value is None:
            continue
        key = "service.name" if name.lower() in {"servicename", "service.name"} else name
        attrs[key] = str(value)
    return attrs


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _span_display_name(span: dict[str, Any]) -> str:
    """Extract the OTel span name from a Cloud Trace span object (v1 REST)."""
    display = span.get("displayName")
    if isinstance(display, dict):
        return str(display.get("value", ""))
    return str(span.get("name", ""))


def _poll_until(fetch: Any, ready: Any) -> tuple[Any, bool]:
    """Poll ``fetch`` until ``ready(result)`` or the deadline expires.

    Cloud Trace/Logging/Monitoring ingest with a 30-90 s lag after the batch
    exporter fires, so an empty first read is not conclusive (PLAN §3.A runs
    right after live traffic). Transport/API errors propagate on the first
    attempt: they are failures, not "not yet".

    Returns:
        ``(last_result, satisfied)``.
    """
    deadline = time.monotonic() + POLL_DEADLINE_S
    result = fetch()
    while not ready(result):
        if time.monotonic() >= deadline:
            return result, False
        time.sleep(POLL_INTERVAL_S)
        result = fetch()
    return result, True


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


def _span_service(span: dict[str, Any]) -> str | None:
    """Attribute a Cloud Trace span to a service.

    The live shape (verified 2026-10-11) carries the OTel resource attribute
    as ``labels["service.name"]``; the span-name sets are the fallback for
    older exporters that dropped the label.
    """
    labels = span.get("labels") or {}
    svc = labels.get("service.name")
    if svc in {"service-a", "service-b"}:
        return str(svc)
    name = _span_display_name(span)
    if name in SERVICE_A_SPANS:
        return "service-a"
    if name in SERVICE_B_SPANS:
        return "service-b"
    return None


def check_cloud_trace(report: CorrelationReport, token: str, project: str, trace_id: str) -> bool:
    """A10: Cloud Trace holds the injected trace with spans of both services.

    Uses the v1 REST surface (v2 is gRPC-only; its REST paths answer HTML 404
    — verified live). Cloud Trace preserves the W3C 32-hex id, so the id
    compares directly. Polls until both services appear or the deadline
    expires (batch-exporter + ingest lag). Returns True on PASS.
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

    def services_of(body: Any) -> set[str]:
        if not isinstance(body, dict):
            return set()
        spans = body.get("spans") or []
        return {svc for s in spans if isinstance(s, dict) and (svc := _span_service(s))}

    body, ok = _poll_until(fetch, ready=lambda b: {"service-a", "service-b"} <= services_of(b))
    if not isinstance(body, dict) or not body.get("spans"):
        report.register(
            10, FAIL, razon="traza inyectada no encontrada en Cloud Trace (deadline agotado)",
            detalles={"url": url},
        )
        return False

    spans = body["spans"]
    names_a = sorted(_span_display_name(s) for s in spans if _span_service(s) == "service-a")
    names_b = sorted(_span_display_name(s) for s in spans if _span_service(s) == "service-b")
    if names_a and names_b:
        report.register(
            10,
            PASS,
            detalles={
                "spans_service_a": names_a,
                "spans_service_b": names_b,
                "total_spans": len(spans),
                "trace_id": body.get("traceId", trace_id),
            },
        )
        return True
    report.register(
        10,
        FAIL,
        razon="la traza existe pero faltan spans de algun servicio",
        detalles={"spans_service_a": names_a, "spans_service_b": names_b},
    )
    return False


def _entry_service(entry: dict[str, Any]) -> str | None:
    """Attribute a Cloud Logging entry to service-a/service-b.

    Four shapes, in order of preference (all verified against the live
    project; la forma OTLP confirmada en vivo 2026-10-12 Ronda 6): the entry
    ``labels["service.name"]`` written by the googlecloud exporter from the
    OTLP resource (the ``LoggingHandler`` path — body is the plain message as
    ``textPayload``, no k8s labels, no ``jsonPayload``), the k8s_container
    label set, the structlog ``name``/``logger`` in jsonPayload, and the
    structlog JSON line kept as text by the filelog pipeline (``"logger":
    "service-a"`` inside ``textPayload``).
    """
    labels = (entry.get("resource") or {}).get("labels") or {}
    entry_labels = entry.get("labels") or {}
    svc = (
        labels.get("container_name")
        or entry_labels.get("service.name")
        or (entry.get("jsonPayload") or {}).get("name")
    )
    if svc in {"service-a", "service-b"}:
        return str(svc)
    payload_logger = (entry.get("jsonPayload") or {}).get("logger")
    if payload_logger in {"service-a", "service-b"}:
        return str(payload_logger)
    # Text fallback for the current filelog pipeline (structlog JSON lands as
    # text). Restricted to the dedicated logName so collector self-logs that
    # echo a dropped payload cannot create false attribution.
    if str(entry.get("logName", "")).endswith("/otel-app-logs"):
        match = LOGGER_RE.search(entry.get("textPayload") or "")
        if match:
            return match.group(1)
    return None


LOGGING_LOOKBACK_SECONDS = 900


def _logging_filters(project: str, trace_id: str) -> list[str]:
    """A11/A13 chain of Cloud Logging filters, in preference order.

    Ronda 6 (2026-10-12, verificado en vivo): ``entries:list`` devuelve 0
    resultados de forma intermitente para filtros estructurados (``trace:`` /
    ``jsonPayload.``) SIN cota inferior de ``timestamp`` — la misma consulta
    con ``timestamp>=...`` encuentra las entradas. La cota (15 min hacia
    atras) cubre de sobra la duracion de la corrida y el lag de ingesta.
    """
    since = f'timestamp>="{(datetime.now(timezone.utc) - timedelta(seconds=LOGGING_LOOKBACK_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")}" AND '
    return [
        since + f'trace:"projects/{project}/traces/{trace_id}"',
        since + f'jsonPayload.trace_id="{trace_id}"',
        since + f'textPayload:"{trace_id}"',
    ]


def check_cloud_logging(report: CorrelationReport, token: str, project: str, trace_id: str) -> bool:
    """A11: Cloud Logging holds ≥1 entry per service carrying the trace id.

    Filter chain (first one that yields entries wins, all bounded by a
    timestamp floor — see :func:`_logging_filters`): the normalized ``trace``
    field (``transform/logs`` writes ``logging.googleapis.com/trace`` and the
    OTLP ``LoggingHandler`` path lands it with ``labels["service.name"]``),
    ``jsonPayload.trace_id``, and the raw ``textPayload`` substring — the
    filelog pipeline lands the structlog JSON as text without a normalized
    trace field (verified live). Polls until both services appear or the
    deadline expires. Returns True on PASS.
    """
    auth = {"Authorization": f"Bearer {token}"}
    filters = _logging_filters(project, trace_id)

    def fetch_round() -> tuple[str, list[dict[str, Any]]]:
        """Try every filter once; return the first non-empty (filter, entries)."""
        for filt in filters:
            _status, _headers, body = http_request(
                "POST",
                LOGGING_LIST_URL,
                headers=auth,
                body={"resourceNames": [f"projects/{project}"], "filter": filt, "pageSize": 256},
            )
            entries = (body or {}).get("entries", []) if isinstance(body, dict) else []
            if entries:
                return filt, entries
        return filters[-1], []

    def ready(round_result: Any) -> bool:
        _filt, entries = round_result
        return {"service-a", "service-b"} <= {_entry_service(e) for e in entries}

    (used_filter, entries), _ok = _poll_until(fetch_round, ready)

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
        razon="no hay >=1 entrada por servicio para el trace inyectado (deadline agotado)",
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


def check_cloud_monitoring(
    report: CorrelationReport,
    token: str,
    project: str,
    window: int,
    run_started: datetime,
) -> bool:
    """A12: the GMP counter ``orders_created_total`` grew inside ``window``.

    v3 ``timeSeries.list`` takes the interval as ``interval.startTime`` /
    ``interval.endTime`` and the metric as ``filter=metric.type="..."``
    (a bare ``metric.type``/``interval`` query param is rejected — verified
    live; v3 also rejects ``aggregation.*`` params). The real metric type is
    discovered via a ``starts_with`` descriptor filter (the expected type is
    preferred when it exists).

    Ronda 6 (2026-10-12, verificado en vivo): GMP entrega los counters como
    puntos CUMULATIVOS — ``interval.startTime`` fijo en la creacion del
    contador y ``value`` = total desde entonces, repetido en cada reporte de
    los 60 s. El old ``last-first`` dentro de una ventana deslizante daba 0
    cuando el incremento ocurrio antes del primer punto de la ventana (el
    reader a 60 s no produce el punto de transicion 0→1). El incremento de la
    corrida es: (ultimo punto con endTime >= run_started) - (ultimo punto con
    endTime < run_started, 0 si el contador nacio en la ventana); las series
    sin puntos dentro de la ventana (pods viejos) no cuentan. Polls until the
    increment shows up or the deadline expires. Returns True on PASS.
    """
    auth = {"Authorization": f"Bearer {token}"}

    descriptor_filter = 'metric.type=starts_with("prometheus.googleapis.com/orders_created")'
    desc_url = (
        f"{DESCRIPTORS_URL.format(project=project)}?"
        f"{urllib.parse.urlencode({'filter': descriptor_filter, 'pageSize': '100'})}"
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

    def increment_over_window() -> tuple[float, int]:
        end = datetime.now(timezone.utc)
        # Margen hacia atras para atrapar el ultimo reporte previo a la corrida
        # (baseline). El reader exporta cada <=window s, 180 s sobra.
        start = run_started - timedelta(seconds=180)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        run_started_iso = run_started.strftime(fmt)
        params = {
            "interval.startTime": start.strftime(fmt),
            "interval.endTime": end.strftime(fmt),
            "filter": f'metric.type="{metric_type}"',
            "pageSize": "1000",
        }
        ts_url = f"{TIMESERIES_URL.format(project=project)}?{urllib.parse.urlencode(params)}"
        _status, _headers, ts_body = http_request("GET", ts_url, headers=auth)
        series = (ts_body or {}).get("timeSeries", []) if isinstance(ts_body, dict) else []

        total_increment = 0.0
        n_series = 0
        for s in series if isinstance(series, list) else []:
            points = sorted(
                (s.get("points") or []),
                key=lambda p: (p.get("interval") or {}).get("endTime", ""),
            )
            within = [
                p for p in points
                if (p.get("interval") or {}).get("endTime", "") >= run_started_iso
            ]
            if not within:
                # Serie sin reportes dentro de la ventana (pod viejo): no
                # contribuye ni resta.
                continue
            before = [
                p for p in points
                if (p.get("interval") or {}).get("endTime", "") < run_started_iso
            ]
            baseline = _point_value(before[-1]) if before else 0.0
            increment = max(_point_value(within[-1]) - baseline, 0.0)
            total_increment += increment
            n_series += 1
        return total_increment, n_series

    (total_increment, n_series), _ok = _poll_until(
        increment_over_window, ready=lambda r: r[0] > 0
    )

    details = {
        "metric_type": metric_type,
        "ventana_segundos": window,
        "series": n_series,
        "incremento_total": total_increment,
    }
    if total_increment > 0:
        report.register(12, PASS, detalles=details)
        return True
    report.register(
        12, FAIL,
        razon="el contador orders_created_total no incremento en la ventana (deadline agotado)",
        detalles=details,
    )
    return False


def _gcp_trace_id_present(token: str, project: str, trace_id: str) -> tuple[bool, str]:
    """Where (if anywhere) the injected trace id shows up in GCP signals.

    Reuses the Cloud Trace v1 GET and the Cloud Logging ``entries:list``
    filter chain from the ``--include-gcp`` path, but with single-trace
    semantics: any span in Cloud Trace or any entry in Cloud Logging counts
    (the cross-cloud flow may carry only the far-side service's signal).

    Returns:
        ``(found, where)`` with ``where`` one of ``cloud_trace``,
        ``cloud_logging`` or ``""``.
    """
    auth = {"Authorization": f"Bearer {token}"}
    try:
        _status, _headers, body = http_request(
            "GET", CLOUDTRACE_URL.format(project=project, trace_id=trace_id), headers=auth
        )
        if isinstance(body, dict) and body.get("spans"):
            return True, "cloud_trace"
    except HttpError as exc:
        if exc.status != 404:
            raise
    for filt in _logging_filters(project, trace_id):
        _status, _headers, body = http_request(
            "POST",
            LOGGING_LIST_URL,
            headers=auth,
            body={"resourceNames": [f"projects/{project}"], "filter": filt, "pageSize": 10},
        )
        entries = (body or {}).get("entries", []) if isinstance(body, dict) else []
        if entries:
            return True, "cloud_logging"
    return False, ""


def check_cross_cloud(
    report: CorrelationReport,
    include_aws: bool,
    include_gcp: bool,
    aws_ctx: dict[str, Any],
    token: str | None,
    project: str,
    trace_id: str,
) -> None:
    """A13: the same W3C trace id is present in signals of BOTH clouds.

    The AWS side is whatever the ``--include-aws`` block already found (an
    X-Ray trace with the converted id, or a CloudWatch Logs event carrying
    the injected trace id); the GCP side reuses the Cloud Trace / Cloud
    Logging reads above. Requires both flags active — with one missing the
    assertion SKIPs with an explicit reason. X-Ray is matched by structure
    (R-03), never by literal id equality; Cloud Trace preserves the W3C id
    so it compares directly.
    """
    if not include_aws or not include_gcp:
        report.register(13, SKIP, razon=CROSS_CLOUD_SKIP_REASON)
        return

    if aws_ctx.get("aws_error"):
        report.register(13, FAIL, razon=f"lado AWS no evaluable: {aws_ctx['aws_error']}")
        return

    aws_where = [
        name
        for name, flag in (
            ("xray", aws_ctx.get("xray_trace_found")),
            ("cloudwatch_logs", aws_ctx.get("cloudwatch_logs_found")),
        )
        if flag
    ]
    if not aws_where:
        report.register(
            13,
            FAIL,
            razon="el trace_id inyectado no aparecio en senales AWS (X-Ray ni CloudWatch Logs)",
        )
        return
    if token is None:
        report.register(13, FAIL, razon="lado GCP no evaluable (token ADC no disponible)")
        return

    def fetch() -> tuple[bool, str]:
        return _gcp_trace_id_present(token, project, trace_id)

    (found, gcp_where), _ok = _poll_until(fetch, ready=lambda r: r[0])
    if found:
        report.register(
            13,
            PASS,
            detalles={
                "pivot": "trace_id W3C (X-Ray verificado por estructura, Cloud Trace conserva el id)",
                "aws": aws_where,
                "gcp": gcp_where,
                "trace_id": trace_id,
            },
        )
    else:
        report.register(
            13,
            FAIL,
            razon="el trace_id inyectado no aparecio en senales GCP (Cloud Trace ni Cloud Logging)",
            detalles={"aws": aws_where, "trace_id": trace_id},
        )


# ---------------------------------------------------------------------------
# AWS assertions (A3-A9), executed only with --include-aws
# ---------------------------------------------------------------------------


def _fail_assertions(report: CorrelationReport, ids: list[int], razon: str) -> None:
    """Register a batch of assertions as FAIL with the same reason."""
    for assertion_id in ids:
        report.register(assertion_id, FAIL, razon=razon)


def _fetch_exporter_logs(
    logs_client: Any, trace_id: str, window: int
) -> dict[str, list[dict[str, Any]]]:
    """FilterLogEvents over the exporter log groups; matched payloads per service.

    Window + Python parsing (assertion 5 of PLAN §3.A): no Logs Insights query
    is ever started. Returns ``{service: [parsed_json_payload, ...]}`` for
    events whose trace_id equals the injected one.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=window)

    def fetch() -> dict[str, list[dict[str, Any]]]:
        matched: dict[str, list[dict[str, Any]]] = {}
        for service in CORRELATION_SERVICES:
            response = logs_client.filter_log_events(
                logGroupName=EXPORTER_LOG_GROUP_TEMPLATE.format(service=service),
                startTime=int(start.timestamp() * 1000),
                endTime=int(end.timestamp() * 1000),
                startFromHead=True,
                limit=250,
            )
            for event in response.get("events", []):
                message = event.get("message", "")
                if log_event_trace_id(message) == trace_id:
                    parsed = None
                    try:
                        payload = json.loads(message)
                        parsed = payload if isinstance(payload, dict) else None
                    except json.JSONDecodeError:
                        parsed = None
                    matched.setdefault(service, []).append(parsed or {"raw": message})
        return matched

    matched, _ok = _poll_until(fetch, ready=lambda m: set(m) >= set(CORRELATION_SERVICES))
    return matched


def _find_orders_series(clients: dict[str, Any]) -> tuple[list[dict[str, str]], int]:
    """Discover the EMF dimensions of orders_created_total + the namespace metric count.

    One ``ListMetrics`` call set (free tier) answers both A6 (which dimensions
    to query) and A9 (how many custom metrics the namespace holds).

    When several dimension sets exist for the counter (legacy series keep
    living in CloudWatch for hours after a collector config change), prefer
    the one carrying the resource identity (``cloud.provider`` +
    ``deployment.environment``): that is the series A7 can cross-check
    against the trace and the logs. Falls back to the first series found so
    A6 still answers with real data.
    """
    cw = clients["cloudwatch"]
    names: set[str] = set()
    counter_series: list[list[dict[str, str]]] = []
    token: str | None = None
    for _ in range(MAX_SUMMARY_PAGES):
        params: dict[str, Any] = {"Namespace": EMF_NAMESPACE}
        if token:
            params["NextToken"] = token
        response = cw.list_metrics(**params)
        for metric in response.get("Metrics", []):
            names.add(metric.get("MetricName", ""))
            if metric.get("MetricName") == ORDERS_COUNTER:
                counter_series.append(list(metric.get("Dimensions", [])))
        token = response.get("NextToken")
        if not token:
            break
    counter_dims: list[dict[str, str]] = []
    for dims in counter_series:
        dim_names = {d.get("Name") for d in dims}
        if {"cloud.provider", "deployment.environment"} <= dim_names:
            counter_dims = dims
            break
    if not counter_dims and counter_series:
        counter_dims = counter_series[0]
    return counter_dims, len(names)


def run_aws_assertions(
    report: CorrelationReport, args: argparse.Namespace, trace_id: str
) -> dict[str, Any]:
    """Execute A3-A9 against AWS and return the context A13 needs.

    Every read is a $0 free-tier API: ``xray:GetTraceSummaries`` +
    ``xray:BatchGetTraces``, ``logs:FilterLogEvents`` (never Insights),
    ``cloudwatch:GetMetricStatistics`` (never ``GetMetricData``) and
    ``cloudwatch:ListMetrics``. Shared fetches (trace summaries, the
    Prometheus scrape, ListMetrics) keep the call count bounded.

    Each assertion block is INDEPENDENT: one API failing registers its rows
    as FAIL with the API reason and lets the remaining assertions still run
    and register — the evidence JSON must never lose catalog rows (lesson
    from the live session of 2026-10-12, where an X-Ray ParamValidationError
    made A5-A9 vanish).
    """
    ctx: dict[str, Any] = {"xray_trace_found": False, "cloudwatch_logs_found": False}
    try:
        clients = {
            name: make_aws_client(name, args.aws_profile, args.aws_region)
            for name in ("xray", "logs", "cloudwatch")
        }
    except ImportError as exc:
        reason = f"boto3 no instalado (pip install -r requirements-dev.txt): {exc}"
        _fail_assertions(report, list(range(3, 10)), reason)
        ctx["aws_error"] = reason
        return ctx
    except Exception as exc:
        reason = f"clientes AWS no disponibles ({type(exc).__name__}): {exc}"
        _fail_assertions(report, list(range(3, 10)), reason)
        ctx["aws_error"] = reason
        return ctx

    # --- A3 / A4: X-Ray by structure ---------------------------------------
    # INDEPENDENT block: an X-Ray API error FAILs A3/A4 with the API reason
    # but never swallows the A5-A9 rows (verified live 2026-10-12: the old
    # early-return dropped them from the evidence JSON).
    summaries: list[str] = []
    docs: list[dict[str, Any]] = []
    xray_id = w3c_to_xray_trace_id(trace_id)
    xray_error: str | None = None

    def _fetch_xray_window() -> tuple[list[str], list[dict[str, Any]], bool]:
        """One X-Ray attempt: summaries + exact-id match or structural scan.

        X-Ray indexes ``PutTraceSegments`` with a 30-90 s lag, like the other
        readers (single-read bug verified live 2026-10-12: A3 FAILed with
        ``trazas_ventana=0`` while the trace appeared ~1 min later). The
        structural fallback (R-03) stays: scan the window for a
        service-a -> service-b parent link instead of trusting the id alone.
        """
        window_summaries = fetch_trace_summary_ids(clients["xray"], args.window)
        if xray_id in window_summaries:
            exact_docs = parse_trace_segments(
                clients["xray"].batch_get_traces(TraceIds=[xray_id])
            )
            # ready only if the segments are actually retrievable: an id in
            # the summaries with no docs yet is still mid-ingest.
            return window_summaries, exact_docs, bool(exact_docs)
        for chunk_start in range(
            0, min(len(window_summaries), MAX_STRUCTURAL_SCAN_TRACES), BATCH_GET_TRACES_MAX_IDS
        ):
            chunk = window_summaries[
                chunk_start : chunk_start + BATCH_GET_TRACES_MAX_IDS
            ]
            candidate_docs = parse_trace_segments(
                clients["xray"].batch_get_traces(TraceIds=chunk)
            )
            ok, _details = xray_parent_link_ok(candidate_docs)
            if ok:
                return window_summaries, candidate_docs, True
        return window_summaries, [], False

    try:
        (summaries, docs, found), _ok = _poll_until(
            _fetch_xray_window, ready=lambda r: r[2]
        )
        ctx["xray_trace_found"] = found
    except Exception as exc:  # boto3 raises many client/credential error types
        xray_error = (
            f"AWS API fallo (xray:GetTraceSummaries/BatchGetTraces): "
            f"{type(exc).__name__}: {exc}"
        )

    if xray_error is not None:
        report.register(3, FAIL, razon=xray_error)
        report.register(4, FAIL, razon=xray_error)
    else:
        ok, details = xray_parent_link_ok(docs)
        graph_edges: list[tuple[str, str]] = []
        graph_error: str | None = None

        def _ensure_graph() -> list[tuple[str, str]]:
            """Fetch (once) the GetTraceGraph edges for the injected trace.

            Needed whenever the segment-level view is not conclusive: the
            OTLP->X-Ray translator sends client spans as separate
            ``type: subsegment`` docs that BatchGetTraces omits, so the
            service-a -> service-b link and the service-b -> Database edge
            are only visible through the graph (live 2026-10-12).
            """
            nonlocal graph_edges, graph_error
            if not graph_edges and graph_error is None:
                try:
                    graph_edges = xray_graph_edges(
                        clients["xray"].get_trace_graph(TraceIds=[xray_id])
                    )
                except Exception as exc:
                    graph_error = f"{type(exc).__name__}: {exc}"
            return graph_edges

        if docs and not ok and ("service-a", "service-b") in _ensure_graph():
            ok = True

        details_out = {
            **details,
            "xray_trace_id_buscado": xray_id,
            "trazas_ventana": len(summaries),
            "aristas_grafo": [list(e) for e in sorted(set(graph_edges))],
        }
        if graph_error:
            details_out["error_grafo"] = graph_error
        if not docs:
            report.register(
                3, FAIL,
                razon="traza inyectada no encontrada en X-Ray (GetTraceSummaries/BatchGetTraces, deadline agotado)",
                detalles=details_out,
            )
        elif ok:
            report.register(3, PASS, detalles=details_out)
        else:
            report.register(
                3, FAIL,
                razon="la traza en X-Ray no cumple el enlace parent_id service-a -> service-b",
                detalles=details_out,
            )
        b_docs = [d for d in docs if d.get("name") == "service-b"]
        b_child_ids = service_segment_ids(docs)["service-b"]
        b_all = [d for d in docs if d.get("id") in b_child_ids or d.get("name") == "service-b"]
        found_attr = any(
            str(find_attribute(d, "db.system")).lower() == "postgresql" for d in b_all
        )
        if not found_attr and docs:
            # X-Ray-native representation: the translator maps the OTel
            # db.system attribute to sql.database_type inside an inferred
            # Database::SQL segment, and that segment belongs to service-b
            # when the trace graph shows a service-b -> db edge.
            sql_docs = [
                d for d in docs
                if str(find_attribute(d, "database_type")).lower() == "postgresql"
            ]
            b_db_edge = any(src == "service-b" for src, _ in _ensure_graph())
            found_attr = bool(sql_docs) and b_db_edge
        if found_attr:
            report.register(
                4, PASS,
                detalles={
                    "documentos_service_b": len(b_all),
                    "aristas_grafo": [list(e) for e in sorted(set(graph_edges))],
                },
            )
        elif b_docs or b_all:
            report.register(
                4, FAIL,
                razon="atributo db.system=postgresql ausente en los segmentos de service-b",
                detalles={"documentos_service_b": len(b_all)},
            )
        else:
            report.register(
                4, FAIL,
                razon="sin segmentos de service-b que inspeccionar (ver A3)",
                detalles={"documentos_service_b": 0},
            )

    # --- A5 / A7 input: CloudWatch Logs (exporter groups) -------------------
    # INDEPENDENT block: a logs API error FAILs A5 only; A6-A9 keep running.
    log_payloads: dict[str, list[dict[str, Any]]] = {}
    logs_error: str | None = None
    try:
        log_payloads = _fetch_exporter_logs(clients["logs"], trace_id, args.window)
        ctx["cloudwatch_logs_found"] = bool(log_payloads)
    except Exception as exc:
        logs_error = f"AWS API fallo (logs:FilterLogEvents): {type(exc).__name__}: {exc}"

    if logs_error is not None:
        report.register(5, FAIL, razon=logs_error)
    else:
        services_found = sorted(log_payloads)
        if set(services_found) >= set(CORRELATION_SERVICES):
            report.register(
                5, PASS,
                detalles={
                    "servicios": services_found,
                    "entradas": {svc: len(v) for svc, v in log_payloads.items()},
                    "grupos": [
                        EXPORTER_LOG_GROUP_TEMPLATE.format(service=s)
                        for s in CORRELATION_SERVICES
                    ],
                },
            )
        else:
            report.register(
                5, FAIL,
                razon="no hay >=1 entrada con el trace_id en cada servicio (deadline agotado)",
                detalles={
                    "servicios": services_found,
                    "grupos": [
                        EXPORTER_LOG_GROUP_TEMPLATE.format(service=s)
                        for s in CORRELATION_SERVICES
                    ],
                },
            )

    # --- A6 / A7 / A9 input: EMF counter + namespace cardinality ------------
    counter_dims: list[dict[str, str]] = []
    namespace_metric_count = 0
    metric_error: str | None = None
    try:
        counter_dims, namespace_metric_count = _find_orders_series(clients)
    except Exception as exc:
        metric_error = f"AWS API fallo (cloudwatch:ListMetrics): {type(exc).__name__}: {exc}"

    datapoints: list[dict[str, Any]] = []
    if metric_error is None:
        if not counter_dims:
            report.register(
                6, FAIL,
                razon=f"no existe la metrica {ORDERS_COUNTER} en el namespace {EMF_NAMESPACE}",
                detalles={"namespace_metric_count": namespace_metric_count},
            )
        else:
            try:
                def _fetch_emf_datapoints() -> list[dict[str, Any]]:
                    end = datetime.now(timezone.utc)
                    start = end - timedelta(seconds=max(args.window, METRIC_PERIOD_SECONDS))
                    response = clients["cloudwatch"].get_metric_statistics(
                        Namespace=EMF_NAMESPACE,
                        MetricName=ORDERS_COUNTER,
                        Dimensions=counter_dims,
                        StartTime=start,
                        EndTime=end,
                        Period=METRIC_PERIOD_SECONDS,
                        Statistics=["Sum"],
                    )
                    return list(response.get("Datapoints", []))

                # EMF lands in CloudWatch with a ~1 min lag after the batch
                # flush: poll until the window shows a positive delta
                # (single-read A6 FAILed live 2026-10-12 for this reason).
                datapoints, _ok = _poll_until(
                    _fetch_emf_datapoints,
                    ready=lambda dps: emf_window_increment(dps) > 0,
                )
            except Exception as exc:
                report.register(
                    6, FAIL,
                    razon=f"AWS API fallo (cloudwatch:GetMetricStatistics): {type(exc).__name__}: {exc}",
                )
            else:
                increment = emf_window_increment(datapoints)
                details = {
                    "dimensions": counter_dims,
                    "periodo_segundos": METRIC_PERIOD_SECONDS,
                    "puntos": len(datapoints),
                    "incremento": increment,
                }
                if increment > 0:
                    report.register(6, PASS, detalles=details)
                else:
                    report.register(
                        6, FAIL,
                        razon=f"el contador {ORDERS_COUNTER} no incremento en la ventana (deadline agotado)",
                        detalles=details,
                    )
    else:
        report.register(6, FAIL, razon=metric_error)

    # --- A7: resource consistency trace / log / metric ----------------------
    # INDEPENDENT block: whatever the upstream blocks could not fetch simply
    # shows as a gap; an unexpected crash still registers the row.
    try:
        _check_resource_consistency(report, docs, log_payloads, counter_dims)
    except Exception as exc:
        report.register(
            7, FAIL, razon=f"comprobacion A7 no evaluable ({type(exc).__name__}): {exc}"
        )

    # --- A8 / A9: Prometheus scrape (:8889 via public ENI after grant) ------
    scrape_text: str | None = None
    scrape_error: str | None = None
    try:
        _status, _headers, body = http_request("GET", args.prometheus_url)
        scrape_text = body if isinstance(body, str) else json.dumps(body)
    except API_ERRORS as exc:  # urllib + HttpError: reaching it is the operator's job
        scrape_error = f"scrape Prometheus fallo ({args.prometheus_url}): {exc}"

    series_count = 0
    exemplars: dict[str, list[str]] = {}
    if scrape_text is not None:
        series_count, exemplars = parse_prometheus_scrape(scrape_text)

    latency_ids = sorted({tid for ids in exemplars.values() for tid in ids})
    if scrape_error:
        report.register(8, FAIL, razon=scrape_error)
    elif not latency_ids:
        report.register(
            8, FAIL,
            razon="la metrica de latencia no expone exemplars con trace_id en el scrape "
            "(activar exemplars en el exporter prometheus del collector)",
            detalles={"series": series_count},
        )
    else:
        verified: list[str] = []
        for exemplar_id in latency_ids[:3]:
            candidate = w3c_to_xray_trace_id(exemplar_id)
            try:
                if candidate in summaries:
                    verified.append(exemplar_id)
                    continue
                found_docs = parse_trace_segments(
                    clients["xray"].batch_get_traces(TraceIds=[candidate])
                )
                if found_docs:
                    verified.append(exemplar_id)
            except Exception:
                continue
        details = {
            "metricas_con_exemplares": sorted(exemplars),
            "trace_ids_ejemplar": latency_ids,
            "trazas_verificadas_en_xray": verified,
            "tempo": "sub-check externo SKIP: Grafana Cloud Tempo no configurado "
            "(placeholder honesto; la validacion real es BatchGetTraces)",
        }
        if verified:
            report.register(8, PASS, detalles=details)
        else:
            report.register(
                8, FAIL,
                razon="ningun trace_id de exemplar corresponde a una traza existente en X-Ray",
                detalles=details,
            )

    if metric_error is not None:
        report.register(9, FAIL, razon=metric_error)
    else:
        problems: list[str] = []
        if namespace_metric_count > CUSTOM_METRICS_LIMIT:
            problems.append(
                f"{namespace_metric_count} metricas custom en {EMF_NAMESPACE} (> {CUSTOM_METRICS_LIMIT})"
            )
        if scrape_error:
            problems.append(f"series Prometheus no verificables: {scrape_error}")
        elif series_count > PROM_SERIES_LIMIT:
            problems.append(f"{series_count} series Prometheus (> {PROM_SERIES_LIMIT})")
        details = {
            "namespace": EMF_NAMESPACE,
            "custom_metrics": namespace_metric_count,
            "prometheus_series": series_count,
            "limite_custom_metrics": CUSTOM_METRICS_LIMIT,
            "limite_series": PROM_SERIES_LIMIT,
        }
        if problems:
            report.register(9, FAIL, razon="; ".join(problems), detalles=details)
        else:
            report.register(9, PASS, detalles=details)

    return ctx


def _check_resource_consistency(
    report: CorrelationReport,
    docs: list[dict[str, Any]],
    log_payloads: dict[str, list[dict[str, Any]]],
    counter_dims: list[dict[str, str]],
) -> None:
    """A7: service.name / cloud.provider / deployment.environment agree.

    The three AWS signals (X-Ray segments, exporter log payloads, EMF metric
    dimensions) must expose the same resource identity: ``cloud.provider``
    ``aws``, ``deployment.environment`` ``lab`` and a service.name inside the
    {service-a, service-b} set. A key missing from a signal means the
    collector's resource pipeline did not normalize that signal, so it is a
    FAIL with the exact gap named.
    """
    expected = {"cloud.provider": "aws", "deployment.environment": "lab"}
    trace_attrs: dict[str, str] = {}
    for doc in docs:
        for key in expected:
            if trace_attrs.get(key) is None:
                value = find_attribute(doc, key)
                if value is None:
                    # The OTLP->X-Ray translator parks OTel resource
                    # attributes in metadata.default under the namespaced
                    # key "otel.resource.<key>" (verified live 2026-10-12);
                    # the bare key never appears in the segment document.
                    value = find_attribute(doc, f"otel.resource.{key}")
                if value is not None:
                    trace_attrs[key] = str(value)
    log_attrs: dict[str, str] = {}
    for payloads in log_payloads.values():
        for payload in payloads:
            for key in expected:
                if log_attrs.get(key) is None:
                    value = find_attribute(payload, key)
                    if value is not None:
                        log_attrs[key] = str(value)
    metric_attrs = dimensions_to_attributes(counter_dims)

    problems: list[str] = []
    for key, want in expected.items():
        for signal, attrs in (("traza", trace_attrs), ("log", log_attrs), ("metrica", metric_attrs)):
            got = attrs.get(key)
            if got is None:
                problems.append(f"{key} ausente en {signal}")
            elif got != want:
                problems.append(f"{key}={got!r} en {signal} (esperado {want!r})")

    trace_services = {d.get("name") for d in docs if d.get("name") in CORRELATION_SERVICES}
    log_services = set(log_payloads)
    metric_service = metric_attrs.get("service.name")
    if trace_services != set(CORRELATION_SERVICES):
        problems.append(f"service.name en traza: {sorted(trace_services)} (esperado ambos)")
    if log_services != set(CORRELATION_SERVICES):
        problems.append(f"service.name en log: {sorted(log_services)} (esperado ambos)")
    if metric_service not in CORRELATION_SERVICES:
        problems.append(f"service.name en metrica: {metric_service!r} (esperado uno de {CORRELATION_SERVICES})")

    seen = {
        "traza": sorted(trace_services),
        "log": sorted(log_services),
        "metrica": [metric_service] if metric_service else [],
    }
    if problems:
        report.register(
            7, FAIL,
            razon="consistencia de resource fallada: " + "; ".join(problems),
            detalles={"servicios_vistos": seen},
        )
    else:
        report.register(
            7, PASS,
            detalles={"servicios_vistos": seen, "esperado": expected},
        )


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


def _ensure_catalog_rows(report: CorrelationReport) -> None:
    """Contract: the evidence JSON always carries the 13 catalog rows.

    Any assertion that finished without registering (unexpected crash in one
    block, early return, …) is recorded as FAIL with the honest reason
    "no evaluada" — catalog rows are never silently dropped (the live run of
    2026-10-12 lost A5-A9 exactly this way).
    """
    registered = {r.id for r in report.results}
    for assertion_id, _nombre, _obligatoria in ASSERTION_CATALOG:
        if assertion_id not in registered:
            report.register(
                assertion_id, FAIL,
                razon="asercion no evaluada (el bloque no registro su fila)",
            )


def run_assertions(args: argparse.Namespace) -> tuple[CorrelationReport, str]:
    """Execute the whole catalog once and return the report + injected id."""
    report = CorrelationReport()
    traceparent, trace_id = generate_traceparent()
    # Ronda 6 (A12): ancla temporal de la corrida. Los puntos GMP del counter
    # son cumulative (value = total desde la creacion del contador), asi que
    # el incremento de la ventana se mide contra el ultimo punto ANTES de
    # este instante, no last-first dentro de una ventana deslizante.
    run_started = datetime.now(timezone.utc)

    resp_headers = check_traceparent_injection(report, args.endpoint, traceparent)
    if resp_headers is None:
        report.register(2, FAIL, razon="A1 fallo: endpoint no alcanzable")
    else:
        check_response_trace_header(report, resp_headers, trace_id)

    aws_ctx: dict[str, Any] = {}
    if args.include_aws:
        aws_ctx = run_aws_assertions(report, args, trace_id)
    else:
        for assertion_id in range(3, 10):
            report.register(assertion_id, SKIP, razon=AWS_SKIP_REASON)

    gcp_token: str | None = None
    if args.include_gcp:
        try:
            gcp_token = gcloud_access_token(args.project)
        except AuthError as exc:
            for assertion_id in (10, 11, 12):
                report.register(assertion_id, FAIL, razon=f"ADC/token GCP no disponible: {exc}")
        else:
            _run_gcp_check(report, 10, check_cloud_trace, gcp_token, args.project, trace_id)
            _run_gcp_check(report, 11, check_cloud_logging, gcp_token, args.project, trace_id)
            _run_gcp_check(report, 12, check_cloud_monitoring, gcp_token, args.project, args.window, run_started)
    else:
        for assertion_id in (10, 11, 12):
            report.register(assertion_id, SKIP, razon=GCP_SKIP_REASON)

    check_cross_cloud(
        report, args.include_aws, args.include_gcp, aws_ctx,
        gcp_token, args.project, trace_id,
    )
    _ensure_catalog_rows(report)
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
    """CLI of the framework (PLAN §3.A)."""
    parser = argparse.ArgumentParser(description="Verify W3C cross-signal correlation (PLAN §3.A)")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="base URL of service-a")
    parser.add_argument(
        "--include-gcp", action="store_true",
        help="run the Cloud Trace/Logging/Monitoring assertions (A10-A12)",
    )
    parser.add_argument(
        "--include-aws", action="store_true",
        help="run the AWS-side assertions (A3-A9, boto3) and enable the cross-cloud A13",
    )
    parser.add_argument(
        "--aws-profile", default=AWS_DEFAULT_PROFILE,
        help="AWS credentials profile for boto3 (default: otel-lab)",
    )
    parser.add_argument(
        "--aws-region", default=AWS_DEFAULT_REGION, help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--prometheus-url", default=DEFAULT_PROMETHEUS_URL,
        help="collector Prometheus scrape endpoint (task public ENI :8889 after aws-access-grant for the AWS run)",
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
