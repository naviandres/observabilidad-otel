# ADR-010: Dashboards nativos por nube bajo las limitaciones reales de cada API

**Proveedores: AWS + GCP.** Status: aceptado (ambos dashboards aplicados y
verificados en consola/API en sus sesiones de 2026-10-12 y 2026-10-11).

## Contexto

El enunciado pide un dashboard de 6 paneles (4 SLIs + CPU + salud del
collector). El diseño lo resuelve en Prometheus (decisión C1: `GetMetricData`
de CloudWatch se cobra siempre — el hub de correlación es local/Grafana). Como
**complemento nativo por nube** se aplican dos dashboards propios: CloudWatch
(AWS) y Cloud Monitoring (GCP). Ambos descubrieron limitaciones de API que no
aparecen en la documentación de diseño:

- **CloudWatch `put-dashboard`**: una expresión metric-math con `period: 300`
  es **rechazada** — los periodos válidos para expresiones son 1/5/10/30 s
  (multiplicadores de 60 s en la práctica de la API); y la propiedad `comment`
  del JSON no es admitida por el round-trip del CLI.
- **CloudWatch EMF**: el free tier de métricas custom es 10 — la config del
  collector fija una **whitelist de 4 contadores** (`filter/cloudwatch-metrics`);
  además, los rollups implícitos de EMF generaban **26 variantes de serie** por
  contador hasta que se fijó `dimension_rollup_option: NoDimensionRollup` → 1.
- **Cloud Monitoring API v1**: el JSON con `gridWidgets` es rechazado; el
  layout aceptado es **`mosaicLayout`**. Y p99/p95 no existen como agregación
  nativa sobre las series de Managed Prometheus → se usan promedios como proxy
  honesto (decisión C1 documentada en el propio JSON).

## Opciones consideradas

- Imitar en lo nativo el dashboard de Grafana (6 paneles, percentiles reales)
  — imposible por las limitaciones anteriores; habría forzado productos de
  pago (CloudWatch Contributor Insights, Synthetic Metrics) fuera de lista
  blanca.
- **Dashboards nativos reducidos y honestos** — elegido: AWS con **máximo 3
  widgets** sobre los únicos contadores EMF que existen; GCP con los 6
  equivalentes que sí se pueden calcular, declarando el proxy p99→avg en el
  artefacto.
- Omitir los nativos — el enunciado pide "dashboards Grafana/Cloud Monitoring"
  explícitamente; descartado.

## Decisión

- `dashboards/cloudwatch-slo.json`: ≤3 widgets (ratio de error EMF, orden
  creada, latencia aproximada), aplicado con `make aws-dashboard`
  (put-dashboard idempotente); las desviaciones (period de expresiones,
  `comment` movido a docs) se documentan aquí y en la evidencia. `make
  aws-alarms` crea 2 alarmas de guardarraíl con acción SNS opcional
  (`SNS_TOPIC_ARN`, output de `infra/aws`).
- `dashboards/cloud-monitoring-gcp.json`: 6 paneles en `mosaicLayout`,
  provisionado **por IaC** (`google_monitoring_dashboard` en
  `infra/gcp/dashboard.tf`, que aplica el JSON tal cual — la vía CLI
  `gcloud monitoring dashboards create` exigiría un envoltorio `dashboard:`
  que el artefacto versionado no lleva, como advierte el README).
- Ambos leídos del scrape/exporter real, nunca inventados: los nombres de
  métrica del scrape (`otel_*`, `exported_job`) se levantaron en vivo
  (evidencia 05).

## Consecuencias

- El dashboard de CloudWatch **no** pretende equivalencia con Grafana: es el
  complemento nativo con lo que el free tier permite (4 contadores, 3 widgets,
  3 dashboards, 10 alarmas — dentro de free tier).
- El panel de GCP sobre series GMP usa promedio como proxy de percentiles; la
  desviación está declarada en el JSON y en la evidencia, no escondida.
- La provisión por IaC del dashboard GCP demostró ser el camino correcto: el
  `mosaicLayout` se verificó aplicando el JSON real, no escribiéndolo a mano.

## Evidencia

- `dashboards/cloudwatch-slo.json` + `dashboards/cloud-monitoring-gcp.json`
  (artefactos aplicados: dashboard GCP real
  `projects/797970839119/dashboards/8ceea108-…`).
- `docs/evidence/02-validation-dashboard-gcp.md` (smoke 12×200 + fault
  injection 6×503 → panel 2 = 0.27; auditoría de series GMP).
- `docs/evidence/05-dashboards-local.md` (procedencia de nombres de métrica).
- Registro de sesión del 2026-10-12: put-dashboard rechazó `period:300` en la
  expresión y `comment` en el round-trip; fix con period válido y comentario
  movido a docs.
