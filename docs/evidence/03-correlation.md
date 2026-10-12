# 03 — Correlación cross-signal (verify_correlation.py) — EVIDENCIA FINAL dual-cloud

> **Cierre de fase:** 2026-10-12 (sesión dual-cloud, rondas 5-6). Estado final = **UNIÓN
> de tres corridas**: `corrA10` (mono AWS, 16:33Z, trace `580cd2c4027a92df26067717cf90a887`),
> `corrGCP6` (mono GCP, 19:01Z, trace `91559eeaf1d50df4195fcc44719bb552`) y `corrDUAL3`
> (dual, 17:11Z, trace `6cbbd62a9f5b259cdf5614ce6ada3d11`). Proyecto `otel-lab-obs-2610`.
> JSONs completos en `03-correlation-aws.json`, `03-correlation-gcp.json` y
> `03-correlation-dual.json`; el canónico `03-correlation.json` es la corrida dual
> (única con las 13 aserciones evaluadas y A13 PASS).

## Tabla UNION — estado final de las 13 aserciones (PLAN §3.A)

| # | Aserción | Estado final | Corrida que la probó | trace_id de ejemplo |
|---|---|---|---|---|
| 1 | Inyección de traceparent W3C en POST /orders | **PASS** | corrA10 (y corrGCP6/corrDUAL3) | `580cd2c4027a92df26067717cf90a887` |
| 2 | Header x-trace-id de la respuesta == trace inyectado | **PASS** | corrA10 (y corrGCP6/corrDUAL3) | `580cd2c4027a92df26067717cf90a887` |
| 3 | X-Ray: segmentos de service-a y service-b con parent_id correcto | **PASS** | corrA10 — verificación POR ESTRUCTURA (grafo de servicio: `service-a→service-b→inventory`; la igualdad literal de ids es imposible con el traductor OTLP→X-Ray, R-03) | `1-580cd2c4-027a92df26067717cf90a887` |
| 4 | X-Ray: atributo db.system=postgresql en segmento de service-b | **PASS** | corrA10 (documentos de service-b vía BatchGetTraces + grafo) | idem 3 |
| 5 | CloudWatch Logs: ≥1 entrada con trace_id por servicio | **PASS** | corrA10 (grupos del exportador `/otel/labs/ecs-cluster/service-a|service-b`: 2+1 entradas) | `580cd2c4027a92df26067717cf90a887` |
| 6 | CloudWatch Metrics: orders_created_total incrementado en la ventana | **PASS** | corrA10 (EMF, suma de deltas del bucket; incremento 1.0) | idem 5 |
| 7 | Consistencia de resource entre traza, log y métrica por nube | **PASS** | corrA10 (`service.name` en las 3 señales; `cloud.provider=aws`, `deployment.environment=lab`) | idem 5 |
| 8 | Prometheus: exemplars con trace_id de traza existente | **FAIL documentado** (limitación real del puente OTLP→prometheus; ver nota abajo) | corrA10 + corrDUAL3 (FAIL reproducible, 1201/1183 series sin exemplars en el scrape) | n/a |
| 9 | Límites de cardinalidad (CloudWatch/Cloud Monitoring/Prometheus) | **PASS** | corrA10 (`OtelLab`: 1 custom ≤ 10; scrape: 1201 series ≤ 1500; `NoDimensionRollup`) | idem 5 |
| 10 | Cloud Trace: traza W3C con spans de service-a y service-b | **PASS** | corrGCP6 (23 spans: a+b con `INSERT orders`/`SELECT inventory`…) | `91559eeaf1d50df4195fcc44719bb552` |
| 11 | Cloud Logging: ≥1 entrada con trace_id por servicio | **PASS** | corrGCP6 (6 entradas, service-a+service-b; filtro `trace:` con cota `timestamp>=`) | `91559eeaf1d50df4195fcc44719bb552` |
| 12 | Cloud Monitoring: orders_created_total incrementado en la ventana | **PASS** | corrGCP6 (`prometheus.googleapis.com/orders_created_total/counter`, incremento 1.0; batch 60 s + incremento anclado a run_started) | `91559eeaf1d50df4195fcc44719bb552` |
| 13 | Cruzada GCP↔AWS: mismo trace_id en señales de ambas nubes | **PASS** | corrDUAL3 — MISMO trace en AWS (`xray` + `cloudwatch_logs`) **y** GCP (`cloud_trace`) con service-a(ECS)→service-b(GKE) vía LB | `6cbbd62a9f5b259cdf5614ce6ada3d11` |

**Resumen: 12/13 PASS + 1 FAIL documentado (A8).** Ningún SKIP queda: las aserciones
3-9 cerraron en la corrida mono AWS (corrA10) y 10-13 en las corridas GCP/dual.

## Nota honesta de A8 — exemplars Prometheus (FAIL demostrado, no aplazado)

El pipeline de métricas SÍ funciona (A6/A7/A9/A12 PASS), pero los **exemplars con
`trace_id` no sobreviven el puente OTLP→Prometheus**. Cadena de pruebas (ronda 5,
`progress/impl_6_correlacion_aws.md` §Bug 5):

1. El **SDK manda exemplars**: verificado offline con debug exporter del collector
   (`-> Trace ID: 872a3159...` presente en el lote OTLP).
2. El **exporter prometheus del collector NO los expone en el scrape** — ni con
   `enable_open_metrics: true` (OpenMetrics servido, `# EOF` presente) ni con
   `send_timestamps: true` — en ADOT v0.46.0 (core 0.141) y contrib 0.162.0.
   Reproducido en vivo en dos nubes: corrA10 (1201 series, 0 exemplars) y
   corrDUAL3 (1183 series, 0 exemplars).
3. La alternativa del enunciado tampoco existe: `http://<task>:8000/metrics` (scrape
   directo del SDK) sirve solo el registro default de `prometheus_client`
   (process_/python_gc_), nunca las métricas OTel — estas viajan solo por OTLP.
4. **La correlación traza↔métrica sí está demostrada por otras vías**: A5 (logs con
   `trace_id` en CloudWatch), A6/A7 (EMF con `service.name`+`cloud.provider`+
   `deployment.environment` consistentes con la traza) y A3/A4 (X-Ray). En GCP, A11
   (logs con trace normalizado) + A12 (métricas con labels `service.name`).

A8 es una aserción **obligatoria** (catálogo `ASSERTION_CATALOG`,
`scripts/verify_correlation.py` L143, `oblig=True`; el exit es
`return 1 if report.required_failures`). Su FAIL hace que **el exit code de la corrida
mono AWS (corrA10) fuera 1**, no "verde con nota" — el test del propio entregable
`tests/test_verify_correlation_aws.py::test_aws_mode_no_exemplars_fails_a8` (L493-505)
fija ese `rc == 1`. Se presenta por tanto como FAIL de aserción obligatoria con su
cadena de evidencia: el SDK sí manda exemplars; el exporter prometheus del collector
(ADOT v0.46.0 / contrib 0.162.0) no los expone en el scrape; la correlación
traza↔métrica queda demostrada igualmente por A5/A6/A7. El cierre de fase es la unión
de las corridas con A8 documentada como FAIL. Un scrape con Grafana Tempo/Mimir
expondría los exemplars del SDK sin el puente — fuera del alcance de este lab (sin
ALB/ingress permanente).

## Degradaciones de la corrida dual (corrDUAL3) — por qué la evidencia es la UNIÓN

En corrDUAL3, `service-a` corre en ECS y llama a `service-b` de **GKE** (LB regional).
Las degradaciones A3/A4/A5/A7/A10/A11/A12 son **estructurales del modo dual**, no de
timing: en el path AWS falta `service-b` — sus señales no aparecen en X-Ray ni en
CloudWatch (A3/A4/A5/A7 FAIL en ESA corrida)— y en el path GCP falta `service-a` — sus
spans no aparecen en Cloud Trace (A10; el JSON registra `spans_service_a=[]` con los de
`service-b` presentes e indexados), sus logs no aparecen en Cloud Logging (A11) y
`orders_created_total` se incrementa en el lado AWS, sin series en GMP (A12). No son
regresiones: el **cierre definitivo de cada aserción es la corrida que le corresponde**
— A1-A7/A9 en la mono AWS (corrA10), A10-A12 en la mono GCP (corrGCP6), A13 en la dual
(corrDUAL3) —, no una unión "por timing". El cierre de fase es esa unión, como fija el
PLAN §3.A y el README ("Sesión dual-cloud"). A13 no degrada: exige el MISMO trace en
ambas nubes y PASS con `6cbbd62a...`.

## Cómo se reproducen

```powershell
# mono AWS (task ECS con ambos servicios; SG con grant /32 activo):
python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 --include-aws `
  --prometheus-url http://<IP_TASK>:8889/metrics
# mono GCP (port-forward svc/service-a 18080:8080):
python scripts/verify_correlation.py --endpoint http://localhost:18080 --include-gcp
# dual (task AWS con INVENTORY_URL=http://<LB_IP>:8001):
python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
  --include-aws --include-gcp --prometheus-url http://<IP_TASK>:8889/metrics
```

Detalles por aserción en los JSON (`detalles`). SKIP no es fallo; el exit code !=0
solo si una aserción OBLIGATORIA falla. Historial: la corrida del 2026-10-11
(trace `e1ac08c9...`, solo GCP interno) cerró A10-A12 por primera vez; quedó
superada por corrGCP6. Teardown de la sesión verificado 2026-10-12: ECS/VPC y
GKE/LB vacíos (`docs/evidence/instance-hours.csv`, `cluster-hours.csv`).

## Disclosure de coste — desviación del criterio "<1 USD"

La sesión costó **~$2.22 USD** (Fargate ~$0.62 según `instance-hours.csv` L19 + GKE
~$1.6 según `cluster-hours.csv` L15), **por encima del criterio de aceptación
"coste de sesión <1 USD"**. Qué subió: el driver dominante fue la pausa de ~9.2 h
con nubes encendidas, que facturó el fee del plano GKE ($0.10/h) y la task Fargate
corriendo; en segundo término, las 2 tareas Fargate sucesivas (rev 12→13), el node
pool e2-medium (autoscaling 0-3, promedio ~1 nodo sobre 14.2 h, ~$0.17) y el LB
regional (~14 h: nace con gcp-expose tras el create y muere con el cluster 19:11Z;
coste <$0.01). El add-on CSI se habilitó EN EL CREATE (`cluster-hours.csv` L13), no
post-create. Qué recortaría la próxima: DELETE del cluster GKE (`gcp-destroy`) entre
bloques de trabajo — la lección literal del registro: "leccion: apagar (delete) entre
bloques, gcp-stop (resize 0) NO detiene el fee del plano" (`cluster-hours.csv` L15):
gcp-stop escala los nodos a 0 pero deja corriendo el fee $0.10/h, que es el grueso
del coste GKE. No es arreglable retroactivamente: es disclosure para que la
confirmación humana testing→done sea informada.
