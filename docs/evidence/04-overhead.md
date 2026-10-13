# 04 — Overhead del instrumentación OTel (Fase 4, PLAN §4)

> Tabla numérica generada por `scripts/bench_report.py` desde los artefactos
> crudos de `bench/` (summaries k6, snapshots Prometheus, lecturas de coste).
> Metodología y limitaciones abajo, en español según convenciones.

## Metodología (decisiones del diseño experimental)

| Parámetro | Valor ejecutado | Nota |
|---|---|---|
| Escenario | **Stack local (compose)**, no sesión de cómputo en la nube | Misma imagen/digest para A y B; el coste por nube se lee del **ingest real de logs** de la variante B con los exporters de trazas apagados (§3.4 C2). Sin cómputo en la nube: solo APIs de lectura + ingest de logs. |
| Herramienta | k6 **en Docker** (`grafana/k6:2.0.0`), script `k6/orders.js` | Sin binario en host; `--summary-export` por run a `bench/k6/<run>.json` |
| Usuarios | rampa 30 s → 50 VU, rampa 30 s → 100 VU, **hold 100 VU × 5 min** | 1 min de warmup + 5 min steady (PLAN §4.A literal) |
| Orden | **ABBA**: A1 B1 B2 A2 A3 B3 (3 réplicas/variante) + atribución B(s=1.0) y B(s=0.0) | `scripts/benchmark.ps1 -Mode full` y `-Mode attrib` |
| Variante A | `OTEL_ENABLED=false` en ambos servicios | Mismo binario; el motor no crea exporters (§4.A) |
| Variante B | `OTEL_ENABLED=true`, sampler `parentbased_traceidratio` arg **0.25**, trazas gestionadas **OFF** | Flags de variante en `deploy/docker-compose.bench.yml`; el sampler se configura por env en `app/telemetry.py` (el SDK no lee `OTEL_TRACES_SAMPLER` por sí mismo) |
| Trazas durante el bench | Pipeline de trazas del collector → sink **`nop`** | Equivalente local a `OTEL_TRACES_XRAY=false` / `OTEL_TRACES_CLOUDTRACE=false` (§3.4 C2): ni X-Ray ni Cloud Trace reciben nada |
| Backends locales | **Jaeger y Grafana apagados**; **Prometheus local activo** (instrumento de medición) | §4.A literal; el orquestador solo sube `postgres otel-collector service-a service-b prometheus` |
| Aislamiento | `compose down -v` entre runs (seed idéntico), precalentamiento 60 s, cooldown 120 s, hostname + image-id registrados por run | `bench/windows.json` |
| Seed | Catálogo real de `init-scripts/01-init.sh`: **3 SKUs** (LAPTOP-001, MOUSE-001, KEYBOARD-001) | Desviación declarada del nominal "200 SKUs" del PLAN: se reuse el seed existente para mantener la BD idéntica al resto de fases; el payload de k6 rota los 3 SKUs de forma determinista |
| Inventario | Loop de reabastecimiento (`UPDATE inventory SET quantity=1000000` cada 5 s) durante cada run | Idéntico en A y B → se cancela en la diferencial; evita la tormenta de 409 al agotar el seed (10/20/15 uds) |
| think time | OFF (bucle cerrado por VU) | Carga máxima constante; mismo patrón en ambas variantes |

**Coste por nube (medición real, no estimación):** con la variante B y las
compuertas `OTEL_BENCH_AWS_LOGS_EXPORTER` / `OTEL_BENCH_GCP_LOGS_EXPORTER`
abiertas, los logs del pipeline se ingieren de verdad en CloudWatch (grupo
`/otel/labs/bench`, stream `otel-lab-bench` — ver nota de routing abajo) y en
Cloud Logging (log name `otel-labs-bench`). Por run se registran:

- **AWS**: contador del exporter `otelcol_exporter_sent_log_records_total{exporter="awscloudwatchlogs/bench"}` (el collector arranca por run, el contador es por-run) × media de bytes/registro **medida** con una página de `FilterLogEvents` (+26 B de overhead estructural por registro). `DescribeLogStreams storedBytes` se lee como comprobación, pero CloudWatch actualiza ese campo con mucho retardo y no sirve por ventana.
- **GCP**: suma de los puntos por minuto del metric interno `logging.googleapis.com/billing/bytes_ingested` en la ventana del run (REST **v3** — la superficie v1 `timeSeries.list` fue retirada; `interval.startTime/endTime`).
- **X-Ray / Cloud Trace: $0** — ningún trazo sale del `nop` local (§3.4 C2); el run de atribución con sampling 1.0 tampoco exporta trazas a la nube.

**Nota de routing por servicio (desviación observada):** la config declara el
atributo de recurso `aws.log.group.names` por servicio (`/otel/labs/bench/service-{a,b}`)
siguiendo la intención del diseño, pero en contrib 0.162 el `log_group_name` del
exporter resulta ganador en la práctica — toda la ingesta aterrizó en
`/otel/labs/bench` (verificado con `DescribeLogGroups` + `FilterLogEvents`:
86 814 eventos en la ventana de prueba, grupos por servicio nunca creados).
La medición de coste no se ve afectada: el contador es por exporter y la
muestra de bytes/registro se toma del grupo real que recibe.

Precios aplicados (fuente consultada 2026-10-12): CloudWatch Logs us-east-1
ingesta+indexación **$0.50/GB** (aws.amazon.com/cloudwatch/prices); Cloud
Logging: ingesta dentro de los primeros 50 GB/mes **sin cargo**
(cloud.google.com/logging/pricing) → coste GCP de la campaña ≈ $0.

## Limitaciones declaradas (obligatorias en el reporte)

1. **Perfil local ≠ Fargate 1 vCPU/2 GB ni nodo e2-small.** La campaña corre
   contra el stack compose en un portátiles con Docker Desktop: el dimensiona-
   miento real (4 procesos conviviendo en el task, pods en el nodo spot) cambia
   la magnitud del overhead — los porcentajes de esta tabla son una cota
   *con el collector y los dos servicios compartiendo el host*, no una medida
   del perfil de producción. (§4.A "cómo el dimensionamiento afecta a la
   medición en cada nube").
2. **k6 en Docker comparte host con lo medido.** El generador de carga compite
   por CPU con los servicios; el efecto es común a A y B y se cancela en la
   diferencial, pero infla las colas de latencia absolutas (p99 de A ya
   incluye contención).
3. **Sin sesión de cómputo en la nube.** El coste medido es el de **ingesta de
   logs**; el coste de cómputo de las nubes en esta fase es $0 por decisión
   (§3.4 C2 + la campaña no toca ECS/GKE). La lectura de `storedBytes` con
   retardo se documenta arriba; la medida por-run usa el contador del exporter
   anclado a bytes/registro reales.
4. **Colecta de métricas con 15 s de scrape + batch 5 s**: las ventanas de
   Prometheus son aproximadas al minuto; suficiente para ratios diferenciales.
5. **Sampling 0.0 no elimina el 100 % del coste de trazas**: el SDK sigue
   creando el contexto y los propagadores (eso es justo lo que la atribución
   separa).

<!-- BENCH:TABLE -->

<!-- /BENCH:TABLE -->

## Discusión (rúbrica: por qué el overhead es como es)

- **Trazas > métricas.** El pipeline de trazas genera varios spans por request
  (order.process, order.validate, inventory.reserve, payment.authorize,
  order.persist + hops HTTP), cada uno con atributos y `BatchSpanProcessor`;
  las métricas son un contador/histograma por request con export periódico de
  5 s. El run de atribución (s=1.0 vs s=0.0) cuantifica esa asimetría: la
  diferencia de p99 entre sampling 1.0 y 0.0 es el coste de generar+exportar
  spans; lo que queda en s=0.0 sobre A es contexto/propagación.
- **Batching.** `batch` (1024/5 s) amortigua el coste de red del OTLP: con
  100 VU el export es asíncrono (BatchSpanProcessor/BatchLogRecordProcessor) y
  el request no espera a la red — por eso el overhead medido es CPU/serializa-
  ción, no latencia de red hacia la nube (que además está apagada, §3.4 C2).
- **El sampling head al 25 % es la palanca dominante.** Reduce ~4× la
  generación de spans raíz y su export; la tabla de atribución muestra cuánto
  se gana exactamente respecto a s=1.0.
- **Dimensionamiento.** En Fargate (1 vCPU) la instrumentación compite con la
  app por el mismo core y el p99 real sería peor que el local; en el nodo
  e2-small de GKE convive con los pods del stack. Esta campaña deja el número
  local y declara la cota; la sesión de nube (§5.B presupuesto) queda fuera
  del alcance de coste de esta fase.
