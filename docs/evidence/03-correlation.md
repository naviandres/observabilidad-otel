# 03 — Correlación cross-signal (verify_correlation.py)

> **Fecha:** 2026-10-11T18:59:24Z · **Proyecto:** `otel-lab-obs-2610` · **Endpoint:** `http://localhost:18080` · **trace_id inyectado:** `e1ac08c9fcf8b0bafdb95528cc084903`

| # | Aserción | Estado | Razón |
|---|---|---|---|
| 1 | Inyección de traceparent W3C en POST /orders | **PASS** | — |
| 2 | Header x-trace-id de la respuesta == trace inyectado | **PASS** | — |
| 3 | X-Ray: segmentos de service-a y service-b con parent_id correcto | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 4 | X-Ray: atributo db.system=postgresql en segmento de service-b | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 5 | CloudWatch Logs: ≥1 entrada con trace_id por servicio | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 6 | CloudWatch Metrics: orders_created_total incrementado en la ventana | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 7 | Consistencia de resource entre traza, log y métrica por nube | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 8 | Prometheus: exemplars con trace_id de traza existente | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 9 | Límites de cardinalidad (CloudWatch/Cloud Monitoring/Prometheus) | **SKIP** | la pata AWS de la correlacion (despliegue AWS aun no disponible) |
| 10 | Cloud Trace: traza W3C con spans de service-a y service-b | **PASS** | — |
| 11 | Cloud Logging: ≥1 entrada con trace_id por servicio | **PASS** | — |
| 12 | Cloud Monitoring: orders_created_total incrementado en la ventana | **PASS** | — |
| 13 | Cruzada GCP↔AWS: mismo trace_id en señales de ambas nubes | **SKIP** | despliegue AWS no disponible (cuenta en sandbox) |

Detalles por aserción en `03-correlation.json` (campo `detalles`).
SKIP no es fallo; el exit code !=0 solo si una aserción OBLIGATORIA falla (PLAN §3.A).
