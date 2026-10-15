# 03 — Correlación cross-signal (verify_correlation.py) — EVIDENCIA FINAL dual-cloud

> **Cierre de fase:** 2026-10-12 (sesión dual-cloud, rondas 5-6, + mini-sesión
> corrA8). Estado final = **UNIÓN de corridas**: `corrA8` (mono AWS FINAL,
> 20:33Z, trace `d718dedd68181d5d61ee30afcfb2c911`, task ECS
> `149e6bcef2f04912851acdd19980f6b1`, rev `otel-lab:14`, imágenes 0.1.4 —
> **A1-A9 TODAS PASS, exit code 0**), `corrA10` (mono AWS previa, 16:33Z, trace
> `580cd2c4027a92df26067717cf90a887`, supersada por corrA8: su A8 falló por la
> vía collector `:8889`, ver nota abajo), `corrGCP6` (mono GCP, 19:01Z, trace
> `91559eeaf1d50df4195fcc44719bb552`) y `corrDUAL3` (dual, 17:11Z, trace
> `6cbbd62a9f5b259cdf5614ce6ada3d11`). Proyecto `otel-lab-obs-2610`.
> JSONs completos en `03-correlation-aws.json` (= corrA8), `03-correlation-gcp.json`
> y `03-correlation-dual.json`; el canónico `03-correlation.json` es la corrida dual
> (única con las 13 aserciones evaluadas y A13 PASS).

## Tabla UNION — estado final de las 13 aserciones (PLAN §3.A)

| # | Aserción | Estado final | Corrida que la probó | trace_id de ejemplo |
|---|---|---|---|---|
| 1 | Inyección de traceparent W3C en POST /orders | **PASS** | corrA8 (y corrA10/corrGCP6/corrDUAL3) | `d718dedd68181d5d61ee30afcfb2c911` |
| 2 | Header x-trace-id de la respuesta == trace inyectado | **PASS** | corrA8 (y corrA10/corrGCP6/corrDUAL3) | `d718dedd68181d5d61ee30afcfb2c911` |
| 3 | X-Ray: segmentos de service-a y service-b con parent_id correcto | **PASS** | corrA8 — verificación POR ESTRUCTURA (grafo de servicio: `service-a→service-b→inventory`; 1+1 segmentos, 5 ids de service-a; la igualdad literal de ids es imposible con el traductor OTLP→X-Ray, R-03) | `1-d718dedd-68181d5d61ee30afcfb2c911` |
| 4 | X-Ray: atributo db.system=postgresql en segmento de service-b | **PASS** | corrA8 (documentos de service-b vía BatchGetTraces + grafo) | idem 3 |
| 5 | CloudWatch Logs: ≥1 entrada con trace_id por servicio | **PASS** | corrA8 (grupos del exportador `/otel/labs/ecs-cluster/service-a|service-b`: 2+1 entradas) | `d718dedd68181d5d61ee30afcfb2c911` |
| 6 | CloudWatch Metrics: orders_created_total incrementado en la ventana | **PASS** | corrA8 (EMF, suma de deltas del bucket; incremento 2.0) | idem 5 |
| 7 | Consistencia de resource entre traza, log y métrica por nube | **PASS** | corrA8 (`service.name` en las 3 señales; `cloud.provider=aws`, `deployment.environment=lab`) | idem 5 |
| 8 | Prometheus: exemplars con trace_id de traza existente | **PASS** | corrA8 — scrape **app-level** `http://<task>:8000/metrics` (OpenMetrics): ejemplar en `http_server_duration_milliseconds_bucket` con el `trace_id` inyectado, validado contra BatchGetTraces en X-Ray. En la vía collector `:8889` sigue sin propagarse (hallazgo real; ver nota abajo) | `d718dedd68181d5d61ee30afcfb2c911` |
| 9 | Límites de cardinalidad (CloudWatch/Cloud Monitoring/Prometheus) | **PASS** | corrA8 (`OtelLab`: 1 custom ≤ 10; scrape app: 31 series ≤ 1500; `NoDimensionRollup`. corrA10 midió además 1201 series en el collector `:8889`, también ≤ 1500) | idem 5 |
| 10 | Cloud Trace: traza W3C con spans de service-a y service-b | **PASS** | corrGCP6 (23 spans: a+b con `INSERT orders`/`SELECT inventory`…) | `91559eeaf1d50df4195fcc44719bb552` |
| 11 | Cloud Logging: ≥1 entrada con trace_id por servicio | **PASS** | corrGCP6 (6 entradas, service-a+service-b; filtro `trace:` con cota `timestamp>=`) | `91559eeaf1d50df4195fcc44719bb552` |
| 12 | Cloud Monitoring: orders_created_total incrementado en la ventana | **PASS** | corrGCP6 (`prometheus.googleapis.com/orders_created_total/counter`, incremento 1.0; batch 60 s + incremento anclado a run_started) | `91559eeaf1d50df4195fcc44719bb552` |
| 13 | Cruzada GCP↔AWS: mismo trace_id en señales de ambas nubes | **PASS** | corrDUAL3 — MISMO trace en AWS (`xray` + `cloudwatch_logs`) **y** GCP (`cloud_trace`) con service-a(ECS)→service-b(GKE) vía LB | `6cbbd62a9f5b259cdf5614ce6ada3d11` |

**Resumen: 13/13 PASS en la unión. 0 FAIL, 0 SKIP.** Ningún SKIP queda: las
aserciones 3-9 cerraron en la corrida mono AWS (corrA8, exit code **0**), 10-12
en la mono GCP (corrGCP6) y 13 en la dual (corrDUAL3).

## Nota técnica de A8 — hallazgo del puente OTLP→Prometheus y cierre app-level

A8 **PASÓ en vivo** (corrA8, mini-sesión 2026-10-12T20:33:05Z, task
`149e6bcef2f04912851acdd19980f6b1`, `--prometheus-url http://<IP_TASK>:8000/metrics`).
El **hallazgo del collector sigue siendo válido** y es la razón por la que el
puente vive en la app (ADR completo en `docs/aws-ecs.md` §7). Cadena de pruebas
(ronda 5 de la sesión de correlación del 2026-10-12):

1. El **SDK manda exemplars**: verificado offline con debug exporter del collector
   (`-> Trace ID: 872a3159...` presente en el lote OTLP).
2. El **exporter prometheus del collector NO los propaga en el scrape `:8889`** —
   ni con `enable_open_metrics: true` (OpenMetrics servido, `# EOF` presente) ni
   con `send_timestamps: true` — en ADOT v0.46.0 (core 0.141) y contrib 0.162.0.
   Reproducido en vivo en dos nubes: corrA10 (1201 series, 0 exemplars) y
   corrDUAL3 (1183 series, 0 exemplars).
3. El **cierre** (ronda 10, commit `b7fdb1b`): cada servicio instrumenta una
   métrica de latencia de negocio con `prometheus_client` **nativo**
   (`service-*/app/latency.py`), histograma `http_server_duration_milliseconds`
   (labels solo `service_name`+`outcome`) con `exemplar={"trace_id","span_id"}`
   leído del span OTel activo, y `/metrics` negocia `Accept:
   application/openmetrics-text` — la única exposición que conserva exemplars
   (el texto plan los descarta). El scrape de la app (`:8000/metrics`) los sirve
   INLINE (`bucket{...} 1.0 # {span_id="...",trace_id="..."} ...`) y corrA8 los
   validó contra BatchGetTraces en X-Ray.
4. La **correlación traza↔métrica** queda además demostrada por otras vías: A5
   (logs con `trace_id` en CloudWatch), A6/A7 (EMF con `service.name`+
   `cloud.provider`+`deployment.environment` consistentes con la traza) y A3/A4
   (X-Ray). En GCP, A11 (logs con trace normalizado) + A12 (métricas con labels
   `service.name`).

Contexto honesto del exit code: A8 es aserción **obligatoria** (catálogo
`ASSERTION_CATALOG`, `scripts/verify_correlation.py`, `oblig=True`; el exit es
`return 1 if report.required_failures`). Por eso la corrida corrA10 (vía
collector `:8889`) salió con **exit 1** — su FAIL es lo que motivó el ADR §7.
Con la vía app-level, **corrA8 salió con exit code 0** (A1-A9 PASS; A10-A13
SKIP por bandera, no computan). El test del entregable
`tests/test_verify_correlation_aws.py::test_aws_mode_no_exemplars_fails_a8`
sigue fijando `rc == 1` para el escenario sin exemplars (regresión protegida).
El sub-check externo Tempo queda SKIP honesto en el JSON (Grafana Cloud Tempo no
configurado; la validación real es BatchGetTraces).

## Degradaciones de la corrida dual (corrDUAL3) — por qué la evidencia es la UNIÓN

En corrDUAL3, `service-a` corre en ECS y llama a `service-b` de **GKE** (LB regional).
Las degradaciones A3/A4/A5/A7/A10/A11/A12 son **estructurales del modo dual**, no de
timing: en el path AWS falta `service-b` — sus señales no aparecen en X-Ray ni en
CloudWatch (A3/A4/A5/A7 FAIL en ESA corrida)— y en el path GCP falta `service-a` — sus
spans no aparecen en Cloud Trace (A10; el JSON registra `spans_service_a=[]` con los de
`service-b` presentes e indexados), sus logs no aparecen en Cloud Logging (A11) y
`orders_created_total` se incrementa en el lado AWS, sin series en GMP (A12). No son
regresiones: el **cierre definitivo de cada aserción es la corrida que le corresponde**
— A1-A9 en la mono AWS (corrA8; corrA10 quedó supersada al cerrar también A8),
A10-A12 en la mono GCP (corrGCP6), A13 en la dual (corrDUAL3) —, no una unión "por
timing". El cierre de fase es esa unión, como fija el PLAN §3.A y el README ("Sesión
dual-cloud"). A13 no degrada: exige el MISMO trace en ambas nubes y PASS con
`6cbbd62a...`.

## Cómo se reproducen

```powershell
# mono AWS (task ECS con ambos servicios; SG con grant /32 activo) — A8 exige
# el scrape APP-LEVEL :8000 (OpenMetrics); :8889 queda para medir series A9 en
# el collector o para local:
python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 --include-aws `
  --prometheus-url http://<IP_TASK>:8000/metrics
# mono GCP (port-forward svc/service-a 18080:8080):
python scripts/verify_correlation.py --endpoint http://localhost:18080 --include-gcp
# dual (task AWS con INVENTORY_URL=http://<LB_IP>:8001):
python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
  --include-aws --include-gcp --prometheus-url http://<IP_TASK>:8000/metrics
```

Detalles por aserción en los JSON (`detalles`). SKIP no es fallo; el exit code !=0
solo si una aserción OBLIGATORIA falla. Historial: la corrida del 2026-10-11
(trace `e1ac08c9...`, solo GCP interno) cerró A10-A12 por primera vez; quedó
superada por corrGCP6. Teardown de la sesión verificado 2026-10-12: ECS/VPC y
GKE/LB vacíos (`docs/evidence/instance-hours.csv`, `cluster-hours.csv`). **La
mini-sesión corrA8 (20:33Z) cerró con teardown verificado también: clusters 0,
VPC 0, y revoke del grant /32 confirmado (`IpPermissions []`).**

## Disclosure de coste — desviación del criterio "<1 USD"

La sesión costó **~$2.27 USD** (Fargate ~$0.67 según `instance-hours.csv` —
L19 $0.62 + mini-sesión corrA8 ~$0.025 (L20-22) + rev 6 $0.027 (L4)— y GKE ~$1.6
según `cluster-hours.csv` L15), **por encima del criterio de aceptación "coste
de sesión <1 USD"**. Qué subió: el driver dominante fue la pausa de ~9.2 h con
nubes encendidas, que facturó el fee del plano GKE ($0.10/h) y la task Fargate
corriendo; en segundo término, las 2 tareas Fargate sucesivas (rev 12→13), el
node pool e2-medium (autoscaling 0-3, promedio ~1 nodo sobre 14.2 h, ~$0.17) y
el LB regional (~14 h: nace con gcp-expose tras el create y muere con el cluster
19:11Z; coste <$0.01). La mini-sesión corrA8 (rev 14, imágenes 0.1.4) añadió
~0.5 h de task ≈ $0.025 — barata y necesaria para cerrar A8 en vivo. El add-on
CSI se habilitó EN EL CREATE (`cluster-hours.csv` L13), no post-create. Qué
recortaría la próxima: DELETE del cluster GKE (`gcp-destroy`) entre bloques de
trabajo — la lección literal del registro: "leccion: apagar (delete) entre
bloques, gcp-stop (resize 0) NO detiene el fee del plano" (`cluster-hours.csv`
L15): gcp-stop escala los nodos a 0 pero deja corriendo el fee $0.10/h, que es
el grueso del coste GKE. No es arreglable retroactivamente: es disclosure para
que la confirmación humana testing→done sea informada.
