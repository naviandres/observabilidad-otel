# docs/evidence/img — figuras del reporte y README

**Estado: TOMADAS (2026-10-15).** Las 14 figuras son capturas reales del
operador en `.png`. Los antiguos placeholders `.svg` se borraron tras la
sustitución (cada PNG conserva el nombre base reservado, así que los enlaces
del reporte/README no cambiaron). La columna "Cómo capturarla" queda como
receta de re-captura para futuras sesiones.

Requisito del plan: 12 capturas como mínimo (6+ por nube). Las figuras L1-L2
cubren el modo local; A1-A6 el lado AWS; G1-G6 el lado GCP.

| # | Fichero (png) | Contenido esperado | Cómo capturarla (resumen) | Coste de la sesión |
|---|---|---|---|---|
| L1 | `fig-l1-jaeger-local-waterfall` | Waterfall Jaeger local: a→b→DB, spans de negocio + SQL | `docker compose up` + `POST /orders` + localhost:16686 | $0 |
| L2 | `fig-l2-grafana-slo-6-paneles` | Grafana `otel-lab-slo` con los 6 paneles y una ráfaga de 5xx visible | stack observability + 15 POST + matar service-b 3 s + localhost:3000 | $0 |
| A1 | `fig-a1-xray-service-map` | X-Ray service map (sesión Fargate) | `make aws-start` + smoke + consola X-Ray | ~$0.03 (task 1 h) |
| A2 | `fig-a2-xray-trace-detail` | Segmentos a/b con parent encadenado + `db.system` | ídem, pestaña Traces | ~$0.03 |
| A3 | `fig-a3-cloudwatch-logs-trace-id` | Logs del exportador con `trace_id` W3C literal | ídem, CloudWatch Logs | ~$0.03 |
| A4 | `fig-a4-cloudwatch-emf-dashboard` | Dashboard nativo `otel-lab-slo` (3 widgets EMF) | `make aws-dashboard` + consola | ~$0 |
| A5 | `fig-a5-ecs-service-tasks` | Task RUNNING con los 5 contenedores | consola ECS o `aws ecs describe-tasks` | ~$0.03 |
| A6 | `fig-a6-budgets-anomaly-alerts` | Budgets $1/día + $100/mes + anomaly monitor OK | consola Billing | $0 |
| G1 | `fig-g1-cloud-trace-waterfall` | Cloud Trace con spans de ambos servicios (ID preservado) | sesión GKE + smoke + consola Cloud Trace | ~$0.10 (fee 1 h) |
| G2 | `fig-g2-cloud-logging-trace-id` | Cloud Logging `jsonPayload.trace_id` | ídem, Logs Explorer | ~$0.10 |
| G3 | `fig-g3-cloud-monitoring-dashboard` | Dashboard Cloud Monitoring 6 paneles (mosaicLayout) | ídem, Monitoring → Dashboards | ~$0.10 |
| G4 | `fig-g4-jaeger-gke-waterfall` | Jaeger UI **en GKE** (fan-out del collector) | `make port-jaeger-gcp` + localhost:16686 | ~$0.10 |
| G5 | `fig-g5-gke-workloads` | `kubectl get pods,svc` — 5 workloads, cero LB | sesión GKE cualquiera | ~$0.10 |
| G6 | `fig-g6-gcp-budget-alerts` | Budget COP 964 461, umbrales 50-150 % | consola Billing (proyecto) | $0 |

Notas de sesión para las capturas de nube:

- Toda sesión de nube sigue el ciclo del README (modos 2-4) y **termina con
  teardown verificado** (`make aws-destroy` / `make gcp-destroy` + registro en
  `../instance-hours.csv` / `../cluster-hours.csv`).
- Las capturas de correlación (A2/A3/G1/G2) salen gratis de la misma sesión:
  hacedlas durante `make aws-verify` / la corrida `--include-gcp`.
- Referencias desde el reporte: §5 (correlación) y §7 (coste) de
  `../../report/report.md`; desde el README: sección "Verificación /
  evidencia" de cada modo.
