# 03 — Correlación cross-signal (verify_correlation.py)

> **Fecha:** 2026-10-11T18:59:24Z · **Proyecto:** `otel-lab-obs-2610` · **Endpoint:** `http://localhost:18080` · **trace_id inyectado:** `e1ac08c9fcf8b0bafdb95528cc084903`

| # | Aserción | Estado | Razón |
|---|---|---|---|
| 1 | Inyección de traceparent W3C en POST /orders | **PASS** | — |
| 2 | Header x-trace-id de la respuesta == trace inyectado | **PASS** | — |
| 3 | X-Ray: segmentos de service-a y service-b con parent_id correcto | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 4 | X-Ray: atributo db.system=postgresql en segmento de service-b | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 5 | CloudWatch Logs: ≥1 entrada con trace_id por servicio | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 6 | CloudWatch Metrics: orders_created_total incrementado en la ventana | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 7 | Consistencia de resource entre traza, log y métrica por nube | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 8 | Prometheus: exemplars con trace_id de traza existente | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 9 | Límites de cardinalidad (CloudWatch/Cloud Monitoring/Prometheus) | **SKIP** | la verificación del lado AWS (despliegue AWS aun no disponible) |
| 10 | Cloud Trace: traza W3C con spans de service-a y service-b | **PASS** | — |
| 11 | Cloud Logging: ≥1 entrada con trace_id por servicio | **PASS** | — |
| 12 | Cloud Monitoring: orders_created_total incrementado en la ventana | **PASS** | — |
| 13 | Cruzada GCP↔AWS: mismo trace_id en señales de ambas nubes | **SKIP** | despliegue AWS no disponible (cuenta en sandbox) |

Detalles por aserción en `03-correlation.json` (campo `detalles`).
SKIP no es fallo; el exit code !=0 solo si una aserción OBLIGATORIA falla (PLAN §3.A).

## Cómo se cerrarán los SKIPs (sesión dual-cloud — pendiente de ejecución)

Las aserciones 3-9 ya NO son un aplazamiento: se ejecutan con `--include-aws`
(boto3, perfil `otel-lab`, región `us-east-1`) y quedan PASS o FAIL con razón
de API. El cierre requiere la sesión de nube en el orden del README
(§"Sesión dual-cloud"):

| # | Qué la desbloquea | Corrida | Lecturas (todas $0 free-tier) |
|---|---|---|---|
| 3 | task ECS con service-a+service-b | `--include-aws` (INVENTORY_URL default localhost) | `xray:GetTraceSummaries` + `xray:BatchGetTraces`; verificación POR ESTRUCTURA (segmentos + `parent_id`), nunca igualdad de ids (R-03); conversión W3C→X-Ray `1-<8hex>-<24hex>` solo para el lookup |
| 4 | idem | `--include-aws` | `xray:BatchGetTraces` → `db.system=postgresql` en un documento de service-b |
| 5 | collector ADOT exportando logs OTLP | `--include-aws` | `logs:FilterLogEvents` en `/otel/labs/ecs-cluster/service-a|service-b` (grupos del EXPORTADOR, no los del driver `ecs-platform`), ventana + parseo en Python, sin Logs Insights |
| 6 | EMF del contador | `--include-aws` | `cloudwatch:GetMetricStatistics` (namespace `OtelLab`, period 300, Sum; NUNCA `GetMetricData` — R-05) |
| 7 | resource normalizado en las 3 señales AWS | `--include-aws` | traza+log+métrica: `service.name`, `cloud.provider=aws`, `deployment.environment=lab` consistentes |
| 8 | túnel `:8889` + exemplars activos | `--include-aws` | scrape Prometheus del collector vía `tunnel.ps1 -Mode Task`; el `trace_id` del exemplar se valida contra `BatchGetTraces`; el sub-check externo contra Tempo de Grafana Cloud queda como placeholder SKIP honesto en `detalles.tempo` si no hay Tempo real |
| 9 | idem | `--include-aws` | `cloudwatch:ListMetrics` ≤10 custom en `OtelLab` + series del scrape ≤1500 |
| 13 | tráfico cruzando nubes | `--include-aws --include-gcp` con `INVENTORY_URL=http://<LB_IP>:8001` | mismo `trace_id` en AWS (X-Ray o CloudWatch Logs) **y** GCP (Cloud Trace o Cloud Logging); requiere ambas banderas |

Las corridas 10-12 ya cerraron en verde contra GKE (tabla superior). La
evidencia final de la fase es la unión de las tres corridas de esta tabla.
