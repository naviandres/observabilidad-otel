"""Verificacion OFFLINE de los dashboards SLO (Fase 3, PLAN 3.B/3.C).

Determinista: no toca ninguna API de nube ni levanta contenedores. Valida la
forma de los JSON versionados (los 6 paneles Grafana contra los nombres REALES
que emite el pipeline local, el panel nativo AWS de maximo 3 widgets y sus 2
alarmas), el provisioning por codigo y la ausencia de secretos. La evidencia
de que las queries devuelven datos en vivo esta en
docs/evidence/05-dashboards-local.md.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH_DIR = ROOT / "dashboards"
SLO = DASH_DIR / "grafana-slo.json"
CW = DASH_DIR / "cloudwatch-slo.json"
CW_ALARMS = DASH_DIR / "cloudwatch-alarms.json"
GCP_DASH = DASH_DIR / "cloud-monitoring-gcp.json"
DASH_YML = ROOT / "deploy" / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
DATASOURCES_YML = ROOT / "deploy" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
OBS_COMPOSE = ROOT / "deploy" / "docker-compose.observability.yml"
LOCAL_COLLECTOR = ROOT / "deploy" / "collector" / "otel-collector-local.yaml"
MAKEFILE = ROOT / "Makefile"

# Titulos literales de PLAN 3.C (columna "Panel").
PLAN_TITLES = [
    "Request rate",
    "Error rate (ratio 5xx)",
    "Latency p99 end-to-end",
    "Latencia p95 de dependencia DB",
    "CPU del host y de los contenedores",
    "Errores del OTel Collector",
]

# Lista blanca EMF real del collector AWS (deploy/collector/otel-collector-aws.yaml):
# unicas metricas que existen en CloudWatch para este pipeline.
EMF_WHITELIST = {
    "orders_created_total",
    "orders_failed_total",
    "payment_authorization_failures_total",
    "db_query_errors_total",
}

SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"BEGIN (RSA |EC )?PRIVATE KEY",
    r"(?i)password\s*[:=]\s*\"?[^\s\"]{8,}",
    r"(?i)bearer\s+[a-z0-9._-]{20,}",
]


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _all_exprs(dash):
    return [t["expr"] for p in dash["panels"] for t in p.get("targets", [])]


# ---------------------------------------------------------------------------
# dashboards/grafana-slo.json — los 6 paneles de PLAN 3.C
# ---------------------------------------------------------------------------


def test_slo_dashboard_parses_with_stable_uid():
    dash = _load(SLO)
    assert dash["uid"] == "otel-lab-slo"
    assert dash["schemaVersion"] >= 39
    assert dash["version"] >= 2


def test_slo_dashboard_has_exactly_six_plan_panels():
    dash = _load(SLO)
    titles = [p["title"] for p in dash["panels"]]
    assert titles == PLAN_TITLES, f"paneles fuera de PLAN 3.C: {titles}"
    ids = [p["id"] for p in dash["panels"]]
    assert ids == sorted(ids) == list(range(1, 7))


def test_slo_dashboard_panels_read_prometheus_datasource():
    dash = _load(SLO)
    for panel in dash["panels"]:
        assert panel["type"] == "timeseries"
        for target in panel["targets"]:
            ds = target["datasource"]
            assert ds == {"type": "prometheus", "uid": "prometheus"}, panel["title"]


def test_slo_dashboard_keeps_service_and_env_variables():
    dash = _load(SLO)
    variables = {v["name"]: v for v in dash["templating"]["list"]}
    assert set(variables) >= {"service", "env"}
    assert set(variables["service"]["query"].split(",")) == {"service-a", "service-b"}
    assert variables["env"]["query"] == "lab"


def test_sli_queries_use_real_metric_names_from_the_local_scrape():
    """Nombres REALES del exporter prometheus del collector (:8889, namespace
    otel, semconv 1.11 del SDK Python) — ver docs/evidence/05-dashboards-local.md.
    El PLAN citaba http_server_duration_count sin prefijo ni unidad y
    service_name como label; el scrape real usa otel_http_server_duration_
    milliseconds_* y el servicio viaja en exported_job (el exporter expone
    service.name como job y el scrape de Prometheus la renombra a exported_job
    al colisionar con su propio job_name)."""
    exprs = " ".join(_all_exprs(_load(SLO)))
    assert "otel_http_server_duration_milliseconds_count" in exprs
    assert "otel_http_server_duration_milliseconds_bucket" in exprs
    assert "otel_http_client_duration_milliseconds_bucket" in exprs
    # el ratio 5xx usa la label real de la semconv 1.11, no http_response_status_code
    assert 'http_status_code=~"5.."' in exprs
    # el servicio se filtra/agrupa por exported_job (= service.name real del scrape)
    assert "by (exported_job)" in exprs
    assert 'exported_job=~"$service"' in exprs


def test_db_panel_documents_the_proxy_and_pins_service_b():
    """db_client_operation_duration NO existe en el scrape real (SQLAlchemy
    0.66b1 solo emite db_client_connections_usage); el panel usa el endpoint
    de service-b que ES la dependencia DB."""
    dash = _load(SLO)
    panel = next(p for p in dash["panels"] if p["title"] == "Latencia p95 de dependencia DB")
    expr = panel["targets"][0]["expr"]
    assert 'exported_job="service-b"' in expr
    assert 'http_target="/inventory/reserve"' in expr
    assert "histogram_quantile(0.95" in expr
    assert "db_client_operation_duration" not in expr
    assert "DESVIACION" in panel["description"]


def test_cpu_panel_uses_real_hostmetrics_names():
    """contrib 0.162 no emite *_utilization: la CPU de host se deriva de
    otel_system_cpu_time_seconds_total (state=idle/total) y el contenedor se
    ve via otel_otelcol_process_cpu_seconds_total."""
    dash = _load(SLO)
    panel = next(p for p in dash["panels"] if p["title"] == "CPU del host y de los contenedores")
    exprs = " ".join(t["expr"] for t in panel["targets"])
    assert "otel_system_cpu_time_seconds_total" in exprs
    assert 'state="idle"' in exprs
    assert "otel_otelcol_process_cpu_seconds_total" in exprs


def test_collector_panel_uses_real_self_telemetry_names():
    """send_failed_spans no existe en contrib 0.162 (registrado en evidencia);
    los equivalentes vivos son queue_size + refused/failed + uptime."""
    dash = _load(SLO)
    panel = next(p for p in dash["panels"] if p["title"] == "Errores del OTel Collector")
    exprs = " ".join(t["expr"] for t in panel["targets"])
    assert "otel_otelcol_exporter_queue_size" in exprs
    assert "otel_otelcol_receiver_refused_spans_total" in exprs
    assert "otel_otelcol_receiver_failed_spans_total" in exprs
    assert "otel_otelcol_process_uptime_total" in exprs
    assert "send_failed_spans" not in exprs


def test_latency_panels_keep_exemplar_and_tempo_data_link():
    """Mecanismo 3.B (no perderlo): click en el exemplar -> traza. La vista de
    traza de cloud es Tempo (aplazada) y la local es Jaeger."""
    dash = _load(SLO)
    latency_panels = [p for p in dash["panels"] if "Latenc" in p["title"]]
    assert len(latency_panels) == 2
    for panel in latency_panels:
        custom = panel["fieldConfig"]["defaults"]["custom"]
        assert custom["exemplar"] is True
        first = custom["links"][0]
        assert first["internal"] is True
        assert "tempo" in first["url"]
        assert "${__field.labels.service_name}" in first["url"]
        assert any("jaeger" in link["url"] for link in custom["links"])


def test_local_collector_wires_hostmetrics_into_metrics_pipeline():
    """Regresion del panel 5: el receiver estaba declarado pero no consumido;
    sin cableadura el scrape :8889 no tiene ninguna metrica de CPU."""
    text = LOCAL_COLLECTOR.read_text(encoding="utf-8")
    match = re.search(
        r"metrics:\s*\n(?:\s*#[^\n]*\n)*\s*receivers:\s*\[([a-z,\s]+)\]", text
    )
    assert match, "pipeline de metricas no encontrado"
    receivers = [r.strip() for r in match.group(1).split(",")]
    assert "hostmetrics" in receivers


# ---------------------------------------------------------------------------
# dashboards/cloudwatch-slo.json — panel nativo AWS (max 3 widgets, PLAN 3.C)
# ---------------------------------------------------------------------------


def test_cloudwatch_dashboard_has_at_most_three_widgets():
    dash = _load(CW)
    assert dash["dashboardName"] == "otel-lab-slo"
    assert 1 <= len(dash["widgets"]) <= 3


def test_cloudwatch_dashboard_only_reads_emf_whitelist():
    """Ningun widget inventa metricas: las unicas que existen en CloudWatch son
    los 4 contadores de la lista blanca EMF del namespace OtelLab. No hay
    CPUUtilization (cw-agent murio exit 1 y ContainerInsights es de pago) — la
    eleccion del sustituto esta documentada en el comment del JSON."""
    dash = _load(CW)
    names = set()
    for widget in dash["widgets"]:
        assert widget["type"] == "metric"
        assert widget["properties"]["region"] == "us-east-1"
        for row in widget["properties"]["metrics"]:
            if len(row) >= 2 and row[0] == "OtelLab":
                names.add(row[1])
    assert names, "el dashboard no referencia ninguna metrica"
    assert names <= EMF_WHITELIST, f"metricas fuera de la lista blanca EMF: {names - EMF_WHITELIST}"
    assert "CPUUtilization" not in names, "cw-agent caido: no existe CPUUtilization que graficar"


def test_cloudwatch_dashboard_no_getmetricdata_from_grafana():
    """R-05: el dashboard CloudWatch es nativo (consola/put-dashboard); Grafana
    no consulta CloudWatch. El JSON de Grafana no puede traer datasource cloudwatch."""
    slo_text = SLO.read_text(encoding="utf-8")
    assert "cloudwatch" not in slo_text.lower()


# ---------------------------------------------------------------------------
# dashboards/cloudwatch-alarms.json — 2 alarmas, sin SNS por ahora
# ---------------------------------------------------------------------------


def test_cloudwatch_alarms_exactly_two_definitions():
    alarms = _load(CW_ALARMS)
    assert isinstance(alarms, list)
    assert len(alarms) == 2
    assert {a["AlarmName"] for a in alarms} == {"otel-lab-error-ratio", "otel-lab-db-degradation"}


def test_cloudwatch_alarms_reference_only_emf_whitelist_and_have_thresholds():
    alarms = _load(CW_ALARMS)
    for alarm in alarms:
        assert alarm["AlarmActions"] == [], "las acciones SNS son deuda del guardrail, no van aqui"
        for action_key in ("OKActions", "InsufficientDataActions"):
            assert alarm.get(action_key, []) == []
        assert float(alarm["Threshold"]) > 0
        assert alarm["ComparisonOperator"] == "GreaterThanThreshold"
        referenced = set()
        if "MetricName" in alarm:
            assert alarm["Namespace"] == "OtelLab"
            referenced.add(alarm["MetricName"])
        for item in alarm.get("Metrics", []):
            if "MetricStat" in item:
                assert item["MetricStat"]["Metric"]["Namespace"] == "OtelLab"
                referenced.add(item["MetricStat"]["Metric"]["MetricName"])
        assert referenced <= EMF_WHITELIST, f"metricas fuera de lista: {referenced - EMF_WHITELIST}"


def test_error_ratio_alarm_has_one_return_data_expression():
    alarm = _load(CW_ALARMS)[0]
    returns = [m for m in alarm["Metrics"] if m.get("ReturnData") is True]
    assert len(returns) == 1 and "Expression" in returns[0]


# ---------------------------------------------------------------------------
# Provisioning y Makefile — por codigo, nunca a mano
# ---------------------------------------------------------------------------


def test_dashboards_are_provisioned_not_manual():
    yml = DASH_YML.read_text(encoding="utf-8")
    assert "path: /var/lib/grafana/dashboards" in yml
    compose = OBS_COMPOSE.read_text(encoding="utf-8")
    assert "../dashboards:/var/lib/grafana/dashboards" in compose
    assert "GF_SECURITY_ADMIN_PASSWORD" in compose  # admin local fijo del lab, no un secreto


def test_datasources_prometheus_links_exemplars_to_tempo():
    text = DATASOURCES_YML.read_text(encoding="utf-8")
    assert "linkedDatasourceUid: tempo" in text


def test_makefile_aws_dashboard_and_alarms_targets():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "aws-dashboard" in text
    assert "put-dashboard --dashboard-name otel-lab-slo" in text
    assert "file://dashboards/cloudwatch-slo.json" in text
    assert "aws-alarms" in text
    assert "scripts/cloudwatch_alarms.ps1" in text


def test_gcp_equivalent_dashboard_untouched():
    """El equivalente Cloud Monitoring (feature GCP propia) sigue presente y
    parseable: los 6 paneles Prometheus tienen su espejo GCP y los 3 widgets
    AWS nativos son el complemento, no un reemplazo."""
    dash = _load(GCP_DASH)
    assert dash.get("displayName") or dash.get("title")


# ---------------------------------------------------------------------------
# Higiene — sin secretos en los artefactos versionados
# ---------------------------------------------------------------------------


def test_no_secrets_in_dashboard_artifacts():
    corpus = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [SLO, CW, CW_ALARMS, DASH_YML, DATASOURCES_YML]
    )
    for pattern in SECRET_PATTERNS:
        assert not re.search(pattern, corpus), f"posible secreto ({pattern}) en artefactos de dashboards"
