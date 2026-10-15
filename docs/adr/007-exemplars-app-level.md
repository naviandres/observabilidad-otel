# ADR-007: Exemplars métrica→traza (aserción A8) se cierran a nivel de aplicación, no en el collector

**Proveedores: AWS + GCP (la limitación es del collector, no de la nube).**
Status: aceptado (2026-10-12; PASS en vivo con exit code 0 tras la
mini-sesión corrA8).

## Contexto

PLAN §3.A pide demostrar el enlace métrica→traza vía exemplars (A8, aserción
**obligatoria**: su FAIL da exit ≠ 0 al framework). La cadena esperada era:
SDK → collector → exporter `prometheus` (`:8889`) → scrape → exemplar
visible. La verificación en vivo la rompió:

1. El SDK **sí** manda exemplars con `trace_id`/`span_id` en los histogramas
   (confirmado offline con el debug exporter del collector: `Trace ID:
   872a3159…` presente en el lote OTLP).
2. El exporter `prometheus` del collector **no los propaga al scrape**: probado
   con ADOT v0.46.0 (core 0.141) y contrib 0.162.0, con y sin
   `enable_open_metrics: true` y `send_timestamps: true`. Reproducido en vivo
   en dos nubes: 1201 series / 0 exemplars (collector AWS `:8889`) y 1183
   series / 0 exemplars (corrida dual).
3. `opentelemetry-exporter-prometheus` del SDK tampoco los mapea; y servir las
   métricas OTel por dos vías rompería la guarda de cardinalidad (A9).

## Decisión

Cada servicio instrumenta **una** métrica de latencia de negocio con
`prometheus_client` nativo (`service-*/app/latency.py`, módulos idénticos):

- Histograma `http_server_duration_milliseconds`, labels solo `service_name` y
  `outcome` (cardinalidad mínima), buckets 5 ms…10 s.
- En cada operación se observa la duración con
  `exemplar={"trace_id","span_id"}` leído del **span OTel activo**; sin span
  válido no se emite exemplar (nada de ceros engañosos).
- `GET /metrics` negocia `Accept: application/openmetrics-text` → exposición
  **OpenMetrics**, la única que conserva exemplars (el texto plano los descarta,
  incluida la variante 1.0.0 de prometheus_client 0.26).
- `verify_correlation.py` pide OpenMetrics y, para cerrar A8, el scrape se
  apunta a la **app** (`--prometheus-url http://<task>:8000/metrics`); `:8889`
  queda para A9/local.

## Consecuencias

- A8 PASS en vivo (corrA8, 2026-10-12T20:33:05Z, exit 0): ejemplar inline
  validado contra `BatchGetTraces` en X-Ray. El formato exacto emitido está
  fijado en `docs/aws-ecs.md` §7.
- El puente exemplar→traza es **complemento, no sustituto**: las métricas OTel
  de negocio (`orders_created_total`, …) siguen viajando OTLP → collector →
  EMF/GMP para A6/A7/A12 sin cambios.
- A8 depende ahora del scrape de la app, accesible por el mismo grant /32 del
  ADR-003 (puertos 8000 y 8889).
- Paridad service-a/service-b fijada por test byte-a-byte
  (`tests/test_exemplar_metrics.py`); la regresión "sin exemplars → A8 FAIL →
  exit 1" queda protegida (`test_aws_mode_no_exemplars_fails_a8`).

## Evidencia

- `docs/evidence/03-correlation.md` §"Nota técnica de A8" (cadena completa de
  pruebas y formato inline real).
- `docs/aws-ecs.md` §7 (este ADR en su forma original, con el runbook de la
  mini-sesión, coste ~$0.05).
- `service-a/app/latency.py`, `service-b/app/latency.py`.
