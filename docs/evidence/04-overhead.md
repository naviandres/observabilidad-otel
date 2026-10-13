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

### Tabla de replicas (por run)

| run | var | sampling | steady s | p50 ms | p95 ms | p99 ms | max ms | reqs | req/s | err % | CPU % | mem used MB | heap MB | queue | refused |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | A | 1.0 | 300 | 962.01 | 1,465.88 | 2,561.09 | 3,710.74 | 33,595 | 93.2 | 0.00 | 12.44 | 1,452.8 | 27.9 | — | — |
| B1 | B | 0.25 | 300 | 1,159.14 | 1,433.09 | 1,582.46 | 1,913.67 | 29,745 | 82.5 | 0.00 | 13.14 | 1,917.7 | 51.9 | 0 | 0 |
| B2 | B | 0.25 | 300 | 1,153.83 | 1,425.18 | 1,602.93 | 1,966.60 | 29,722 | 82.4 | 0.00 | 12.92 | 1,767.1 | 65.0 | 1 | 0 |
| A2 | A | 1.0 | 300 | 969.56 | 1,142.63 | 1,248.66 | 1,629.31 | 35,620 | 98.8 | 0.00 | 12.51 | 1,466.9 | 30.9 | — | — |
| A3 | A | 1.0 | 300 | 974.36 | 1,227.39 | 1,442.46 | 3,068.17 | 34,755 | 96.5 | 0.00 | 12.39 | 1,439.6 | 31.0 | — | — |
| B3 | B | 0.25 | 300 | 1,247.01 | 1,692.52 | 2,326.20 | 21,457.44 | 25,562 | 70.8 | 0.18 | 13.76 | 1,743.3 | 41.3 | 0 | 0 |
| attrib-s100 | B | 1.0 | 300 | 1,366.10 | 1,643.03 | 1,788.03 | 2,188.70 | 25,344 | 70.3 | 0.00 | 13.42 | 1,854.9 | 65.3 | 2 | 0 |
| attrib-s000 | B | 0.0 | 300 | 1,193.63 | 1,462.53 | 1,617.27 | 1,934.57 | 28,940 | 80.2 | 0.00 | 13.23 | 1,652.2 | 64.4 | 0 | — |

### Medias por variante y overhead (formulas 4.C)

| metrica | A (media) | B (media) | overhead % |
|---|---|---|---|
| p99 latencia (ms) | 1,750.74 | 1,837.20 | 4.94 |
| CPU host % | 12.45 | 13.27 | 6.62 |
| Memoria usada (MB) | 1,453.1 | 1,809.4 | 24.52 |
| req/s | 96.2 | 78.6 | — |
| error rate % | 0.00 | 0.06 | — |

Desviacion entre replicas: p99 A 708.45 ms, B 423.62 ms.

### Atribucion (sampling 1.0 vs 0.0)

- p99 sampling 1.0: 1,788.03 ms
- p99 sampling 0.0: 1,617.27 ms
- p99 baseline A:   1,750.74 ms
- generacion+export de spans: 9.75 % del p99 de A
- contexto/propagacion:       -7.62 % del p99 de A

### Coste incremental por nube (variante B)

| run | AWS records | AWS ingest est. GB | AWS USD | GCP ingestion bytes | GCP USD |
|---|---|---|---|---|---|
| B1 | 86,218 | 0.061 | 0.0306 | 70,458,902 | 0.0000 |
| B2 | 86,012 | 0.061 | 0.0305 | 71,179,688 | 0.0000 |
| B3 | 72,308 | 0.051 | 0.0256 | 59,091,399 | 0.0000 |
| attrib-s100 | 72,860 | 0.052 | 0.0258 | 61,060,032 | 0.0000 |
| attrib-s000 | 85,633 | 0.061 | 0.0304 | 71,764,393 | 0.0000 |

- Total campana AWS: 0.1430 USD · GCP: 0.0000 USD · fuente: real_ingest
- X-Ray / Cloud Trace: **$0** durante la campana — los exporters de trazas gestionadas estan apagados (OTEL_TRACES_XRAY / OTEL_TRACES_CLOUDTRACE / pipeline->nop, seccion 3.4 C2). Leido del ingest real
- overhead por dolar: 34.53 %p99/USD (coste total 0.1430 USD)

<!-- /BENCH:TABLE -->

## Lectura honesta de los números

- **El p99 de la variante A trae una réplica contaminada por el arranque**:
  A1 (primer run de la campaña, 2 561 ms) frente a A2/A3 (1 249 / 1 442 ms) —
  desviación entre réplicas de A = 708 ms, mayor que el overhead diferencial
  que se quiere medir. Por eso el `overhead_latencia_pct` sobre la **media de
  p99** (+4,94 %) queda subestimado y la atribución "contexto/propagación"
  sale negativa (−7,62 %): artefacto del outlier, no un resultado.
- **La señal limpia está en p50**: A 962/970/974 ms (desviación 6,2 ms — el
  túnel/stack es estable entre réplicas, como exige §4.A "dos A ≈ ruido")
  frente a B 1 159/1 154/1 247 ms (media 1 187 ms) → **overhead p50 ≈ +22,5 %**
  con OTel al 25 %. El p99 de B es *menor* que el de A1 porque la carga de B fue ~15 %
  inferior (82 vs 96 req/s: el coste de instrumentación reduce el throughput
  del bucle cerrado, no solo la latencia).
- **CPU**: 12,45 % → 13,27 % de tiempo no-idle del host (+6,6 % relativo); el
  collector pasa de ~0,001 a ~0,002–0,003 cores con el gate cerrado y el sink
  nop — el coste dominante de CPU está en la serialización OTLP dentro de los
  servicios, no en el collector.
- **Memoria**: +24,5 % de RAM usada en el host (1,45 → 1,81 GB): buffers del
  SDK (BatchSpanProcessor/BatchLogRecordProcessor) + heap del collector
  (28→52 MB). En el perfil Fargate (límite 2 GB) esto es lo que más cerca
  queda del ceiling — limitación 1 arriba.
- **Salud del pipeline**: queue 0–2 batches, refused/failed 0 en los 5 runs B
  (con sampling 1.0 incluido) → el batch 1024/5 s absorbe la carga sin
  descartar señales.
- **Coste**: AWS 0,143 USD por los 5 runs B (86k–73k registros/run × 762 B
  muestreados, $0.50/GB ingest+indexación). GCP: 15–72 MB por run vía
  records×bytes (ventana de billing como cross-check) — **$0**, dentro de los
  50 GB/mes libres. Campaña total ≈ 0,14 USD < 0,50 USD. X-Ray y Cloud Trace:
  $0 **demostrado**, no estimado: el pipeline de trazas del collector en modo
  bench drena a `nop` y ningún request salió hacia las nubes (C2).
- **Throughput**: A 96,2 req/s vs B 78,6 req/s (−18 %) a 100 VU cerrados: con
  think-time OFF, el overhead se manifiesta como caída de tasa además de
  latencia — coherente con que el cuello sea CPU en el bucle de instrumentación.

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
