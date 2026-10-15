# ADR-009: Benchmark — sampling parent-based 0.25 y trazas gestionadas OFF (decisión C2)

**Proveedores: AWS + GCP (efecto de coste en ambos).** Status: aceptado
(campaña ejecutada 2026-10-12 con orden ABBA y atribución 1.0/0.0).

## Contexto

El PLAN §3.4 (C2) fija dos apalancamientos de coste para la fase de benchmark:
muestreo `parentbased_traceidratio` con arg **0.25** en la variante
instrumentada, y **export de trazas gestionadas OFF** durante la campaña (ni
X-Ray ni Cloud Trace reciben nada), para que medir no gaste el presupuesto.
El sampler del SDK Python no se configura solo por variable de entorno: había
que cablearlo.

## Opciones consideradas

- Sampling 1.0 en el benchmark — medía el peor caso pero inflaba la ingestión
  de trazas y no representaba el diseño declarado (C2).
- Sampling en el collector (`tailsampling`) — ADOT v0.46.0 lo incluye pero
  ADS/extra: cambiaría el punto de decisión (el coste de *generación* de spans
  en la app ya se habría pagado) y rompería la paridad con el modo local.
- **Head sampling 0.25 en el SDK, env-configurable** — elegido:
  `OTEL_TRACES_SAMPLER=parentbased_traceidratio` +
  `OTEL_TRACES_SAMPLER_ARG` leídos en `app/telemetry.py` (default 1.0 intacto
  para correlación/desarrollo; 0.25 solo en la variante B del bench).
- Trazas OFF vía apagar el SDK — falsearía la A/B (el coste de contexto y
  propagadores desaparece del todo). OFF real: el pipeline de trazas del
  collector drena a sink **`nop`** (`otel-collector-bench.yaml`); la app sigue
  generando y exportando a su collector local.

## Decisión

Variante B = `OTEL_ENABLED=true` + sampler 0.25 + pipeline de trazas del
collector → `nop`; logs de nube SÍ reales (compuertas `OTEL_BENCH_{AWS,GCP}_LOGS`)
para medir el coste incremental por proveedor sin sesión de cómputo. Orden
ABBA con 3 réplicas por variante, precalentamiento 60 s, cooldown 120 s, `down
-v` entre runs, mismos digests A/B, seed idéntico; runs de atribución con
sampling 1.0 y 0.0 para separar generación+export de spans vs
contexto/propagación.

## Consecuencias

- **X-Ray y Cloud Trace: $0 demostrado, no estimado** — el ingest de trazas de
  la campaña fue cero (leído del propio pipeline `nop` y de las facturas de
  ingestión). El coste incremental medido fue solo logs: AWS $0.1430 por los 5
  runs B; GCP $0.0000 (dentro de los 50 GB/mes libres).
- El resultado principal (p99 +4.94 %) queda **contaminado por un outlier de
  arranque en la variante A** (A1 = 2561 ms vs A2/A3 ≈ 1249/1442 ms; desviación
  708 ms > overhead diferencial) y por la física del bucle cerrado (B hizo ~15
  % menos req/s): la lectura honesta —p50 +22.5 %, CPU +6.6 %, RAM +24.5 %,
  throughput −18 %— está escrita en la evidencia.
- Sampling 0.0 no elimina el 100 % del coste de trazas (el SDK sigue creando
  contexto y propagadores): justo lo que la atribución separa, declarado como
  limitación.

## Evidencia

- `docs/evidence/04-overhead.md` (metodología, tabla por run, atribución,
  limitaciones, lectura honesta).
- `k6/orders.js`, `k6/{baseline,otel}.json`, `scripts/benchmark.ps1`,
  `scripts/bench_report.py`, `bench/` (artefactos crudos).
- `service-a/app/telemetry.py` (sampler env-configurable; mismo patrón en
  service-b), `deploy/collector/otel-collector-bench.yaml` (sink `nop`).
