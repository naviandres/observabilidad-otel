# Evidencia 05 — Dashboard SLO de Grafana (validación local en vivo)

Fecha: 2026-10-12 · Modo: stack local (`deploy/docker-compose.yml` +
`deploy/docker-compose.observability.yml`), $0, sin nubes.

## Procedencia de los nombres de métrica (no adivinados)

Todo lo siguiente se levantó del scrape REAL del exporter `prometheus` del
collector (`:8889`, config `deploy/collector/otel-collector-local.yaml`,
`namespace: otel`) con el stack vivo y tráfico de negocio generado
(`POST /orders` contra `:8000`; SKU sembrados MOUSE-001/KEYBOARD-001,
reabastecidos con `psql -d inventory -c "UPDATE inventory SET quantity = quantity + 60"`;
los 5xx se generaron deteniendo `service-b` 3 s → el flujo devuelve 503).

### Hallazgo de labels (crítico para las queries)

El exporter prometheus expone `service.name` como label `job` e
`instance=<service.instance.id>`. El scrape de Prometheus usa
`job_name: otel-collector` (deploy/collector/prometheus.yml) contra
`otel-collector:8889`, por lo que las labels expuestas `job`/`instance`
COLISIONAN con las del propio scrape y Prometheus las renombra en la TSDB a
`exported_job` / `exported_instance`. Verificado en vivo:

```
# Respuesta real de /api/v1/query?query=otel_http_server_duration_milliseconds_count
{
  "__name__": "otel_http_server_duration_milliseconds_count",
  "exported_job": "service-a",
  "exported_instance": "36fc22f1-3429-44c6-a960-44d3e1b254a1",
  "instance": "otel-collector:8889",
  "job": "otel-collector",
  "http_status_code": "200", "http_target": "/orders", ...
}
```

Conclusión: las queries del dashboard filtran/agrupan por **`exported_job`**
(= service.name real), NUNCA por `job` (esa es "otel-collector").

### Nombres reales vs PLAN §3.C (desviaciones anotadas en el JSON)

| PLAN §3.C | Nombre REAL en el scrape | Motivo |
|---|---|---|
| `http_server_duration_count` | `otel_http_server_duration_milliseconds_count` | namespace `otel` + semconv 1.11 del SDK Python (unidad ms en el nombre) |
| `http_response_status_code=~"5.."` | `http_status_code=~"5.."` | semconv 1.11 (la label nueva es de HTTP semconv 1.27+, no la que emite FastAPI instrumentation 0.66b1) |
| `by (service_name)` | `by (exported_job)` | el exporter mapea `service.name`→`job`; el scrape la renombra a `exported_job` (colisión arriba) |
| `db_client_operation_duration_bucket` | **no existe** → proxy `otel_http_server_duration_milliseconds_bucket{exported_job="service-b",http_target="/inventory/reserve"}` | SQLAlchemy instrumentation 0.66b1 solo emite `otel_db_client_connections_usage`; el endpoint de service-b ES la dependencia DB |
| `system_cpu_utilization` / `process_cpu_utilization` | `otel_system_cpu_time_seconds_total` (derivada `1 - idle/total`) y `otel_otelcol_process_cpu_seconds_total` | contrib 0.162 retiró los gauges `*_utilization` del scraper de cpu (emite `system.cpu.time` por state); el `processes` scraper no produce series dentro del contenedor |
| `otelcol_exporter_send_failed_spans` | **no existe** → `otel_otelcol_receiver_failed_spans_total` (+refused) | contrib 0.162 no registra ese instrument; verificado con grep del scrape completo (0 coincidencias `send_failed`) |
| `otelcol_exporter_queue_size` / `otelcol_receiver_refused_spans` / `otelcol_process_uptime` | `otel_otelcol_exporter_queue_size` / `otel_otelcol_receiver_refused_spans_total` / `otel_otelcol_process_uptime_total` | prefijo `otel_` + sufijo `_total` de counters |

Fix habilitador: el receiver `hostmetrics` estaba DECLARADO en
`otel-collector-local.yaml` pero NO cableado al pipeline `metrics` (sin él el
scrape no contenía ninguna métrica de CPU). Se añadió a
`receivers: [otlp, prometheus, hostmetrics]` (regresión cubierta por
`tests/test_dashboards.py::test_local_collector_wires_hostmetrics_into_metrics_pipeline`).

## a) Target del collector en Prometheus (`:9090/api/v1/targets`)

```json
[
  {
    "job": "otel-collector",
    "scrapeUrl": "http://otel-collector:8889/metrics",
    "health": "up",
    "lastError": ""
  }
]
```

## b) Las queries de los 6 paneles (instant + range 15 min/60 s)

Salida condensada de `/api/v1/query` y `/api/v1/query_range` con tráfico
recién generado (15 orders OK + 5×503 + 6 OK tras recuperación; ventana [5m]):

| Panel | Query final (literal del JSON) | Series instant | Puntos range | Valor instantáneo |
|---|---|---|---|---|
| 1 Request rate | `sum by (exported_job) (rate(otel_http_server_duration_milliseconds_count{exported_job=~"$service"}[5m]))` | 1 | 16 | 0.0912 req/s (service-a) |
| 2 Error rate (ratio 5xx) | `sum(rate(otel_http_server_duration_milliseconds_count{exported_job=~"$service",http_status_code=~"5.."}[5m])) / sum(rate(otel_http_server_duration_milliseconds_count{exported_job=~"$service"}[5m]))` | 1 | 16 | 0.1923 (19 % durante la ráfaga de 503) |
| 3 Latency p99 end-to-end (servidor) | `histogram_quantile(0.99, sum by (le) (rate(otel_http_server_duration_milliseconds_bucket{exported_job=~"$service"}[5m])))` | 1 | 16 | 4870 ms (inflado por los timeouts 503) |
| 3 overlay (hop de traza, cliente) | `histogram_quantile(0.99, sum by (le) (rate(otel_http_client_duration_milliseconds_bucket{exported_job=~"$service"}[5m])))` | 1 | 16 | 74.75 ms |
| 4 Latencia p95 de dependencia DB (proxy) | `histogram_quantile(0.95, sum by (le) (rate(otel_http_server_duration_milliseconds_bucket{exported_job="service-b",http_target="/inventory/reserve"}[5m])))` | 1 | 16 | 73.75 ms |
| 5 CPU host | `1 - sum(rate(otel_system_cpu_time_seconds_total{state="idle"}[5m])) / sum(rate(otel_system_cpu_time_seconds_total[5m]))` | 1 | 14 | 0.0065 (0.65 %) |
| 5 CPU contenedor (collector) | `rate(otel_otelcol_process_cpu_seconds_total[5m])` | 1 | 20 | 0.0029 core |
| 6 queue | `sum by (exporter) (otel_otelcol_exporter_queue_size)` | 1 | 16 | 0 (exporter otlp/jaeger) |
| 6 refused | `sum(rate(otel_otelcol_receiver_refused_spans_total[5m]))` | 1 | 15 | 0 |
| 6 failed | `sum(rate(otel_otelcol_receiver_failed_spans_total[5m]))` | 1 | 15 | 0 |
| 6 uptime | `otel_otelcol_process_uptime_total` | 1 | 16 | 792.47 s |

Las 11 queries devuelven series NO vacías. El JSON del dashboard guarda la
variable `$service` (service-a/service-b); en esta corrida se resolvió a
`service-a` (default del panel).

## c) Dashboard servido por Grafana (`:3000/api/dashboards/uid/otel-lab-slo`)

```json
{
  "uid": "otel-lab-slo",
  "title": "OpenTelemetry SLO Dashboard",
  "panels": [
    "Request rate",
    "Error rate (ratio 5xx)",
    "Latency p99 end-to-end",
    "Latencia p95 de dependencia DB",
    "CPU del host y de los contenedores",
    "Errores del OTel Collector"
  ],
  "variables": ["service", "env"]
}
```

Servido por PROVISIONING (nunca a mano): `deploy/grafana/provisioning/dashboards/dashboards.yml`
(provider `lab_obs`, `path: /var/lib/grafana/dashboards`) + mount
`../dashboards:/var/lib/grafana/dashboards:ro` del compose de observabilidad.
El provider recogió la versión 4 del JSON en caliente (updateIntervalSeconds 30).

## Limpieza

`docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml down -v`
→ contenedores, red y volumen de postgres eliminados; `docker ps` sin restos
del proyecto. Coste de la sesión: $0 (todo local).

## Correspondencia entre los tres paneles (los 6 SLIs viven en Prometheus)

| Señal | Grafana `otel-lab-slo` (Prometheus, 6 paneles) | Cloud Monitoring GCP (`cloud-monitoring-gcp.json`, 6 charts) | CloudWatch `otel-lab-slo` (≤3 widgets) |
|---|---|---|---|
| Request rate | panel 1 (`otel_http_server_duration_milliseconds_count`) | chart 1 (Managed Prometheus) | widget 1 (`orders_created_total` EMF, proxy de tasa) |
| Error ratio | panel 2 (`http_status_code=~"5.."`) | chart 2 | widget 2 (expresión `orders_failed/orders_created`) |
| p99 e2e + overlay | panel 3 (bucket ms + hop cliente) | chart 3 (proxy sum/count) | — (histogramas no llegan a EMF; decisión de coste) |
| p95 DB | panel 4 (proxy /inventory/reserve) | chart 4 | widget 3 (`db_query_errors_total` + `payment_authorization_failures_total`) |
| CPU host/contenedor | panel 5 (hostmetrics) | chart 5 | — (cw-agent caído exit 1, sin ContainerInsights: no existe CPUUtilization; sustituido por salud de dependencia, documentado en el JSON) |
| Salud collector | panel 6 (autotelemetría) | chart 6 (Managed Prometheus) | — (no hay métrica del collector en CloudWatch, PLAN 3.C) |

El coste mensual por nube como stat de Grafana alimentado por el JSON de
`cost_guard.ps1` NO vive aquí: pertenece al guardrail de coste del proximo ciclo.
