# ADR-006: Collector dual — ADOT v0.46.0 en AWS, contrib 0.162.0 en GCP/local

**Proveedores: AWS + GCP.** Status: aceptado (config validada con ambos
binarios; las asimetrías se descubrieron validando, no leyendo docs).

## Contexto

El pipeline necesita un Collector por nube con exporters nativos: `awsxray`,
`awsemf`, `awscloudwatchlogs` en AWS; `googlecloud`,
`googlemanagedprometheus` en GCP. Dos distribuciones oficiales los cubren:
**ADOT** (AWS Distro for OpenTelemetry, `aws-otel-collector`) y
**contrib/GCP-distro** (`otel/opentelemetry-collector-contrib`). El PLAN exige
validar cada YAML con su binario en CI (§2.C) y tags exactos (nunca `latest`).

## Opciones consideradas

- Un único binario para ambas nubes (contrib): tendría los exporters de AWS,
  pero el enunciado y el PLAN piden demostrar la distribución del proveedor en
  AWS (ADOT) — y viceversa en GCP.
- **ADOT `v0.46.0` (core v0.141) en AWS + contrib `0.162.0` en GCP/local** —
  elegida; es la configuración desplegada y verificada.
- AWS CloudWatch Agent como collector único: no habla OTLP; queda solo como
  sidecar de métricas ECS (`essential:false`).

## Decisión

- `deploy/collector/otel-collector-aws.yaml` corre sobre ADOT v0.46.0 (imagen
  propia con el YAML horneado, `otelcol-contrib.dockerfile`);
  `otel-collector-{gcp,local}.yaml` sobre contrib 0.162.0.
- **Validación asimétrica por capacidades del binario** (`make
  validate-collector`): ADOT **no tiene subcomando `validate`** → se valida por
  arranque limpio ("Everything is ready. Begin running and processing data." =
  exit 0); contrib sí → `validate --config=…`.
- Los operadores que ADOT v0.46.0 no conoce (`hostmetrics`, `transform`,
  `awslogresourcewithattr`) **no** se usan en el YAML de AWS: viven en la config
  local y las métricas de infraestructura ECS las aporta el sidecar
  cloudwatch-agent (desviación R-09 documentada). El pipeline de AWS mantiene
  la cadena `memory_limiter → resourcedetection → resource → attributes/redact
  → batch` con los componentes que el binario soporta.

## Consecuencias

- La CI valida **dos binarios y cuatro YAMLs** — el costo es una imagen extra
  en el job, el beneficio es que una config inválida para el binario del
  proveedor no llega a la nube.
- Dos superficies de features distintas es fricción real (el hallazgo de
  exemplars, ADR-007, solo se entiende sobre este fondo: ni ADOT 0.46/core 141
  ni contrib 0.162 propagan exemplars desde el exporter `prometheus` del
  collector).
- El pin de ADOT es viejo a propósito: es el tag con el que se validó la
  sesión; subirlo es una decisión con prueba en vivo, no un bump automático.

## Evidencia

- `Makefile` `validate-collector` (comentarios con el hallazgo del subcomando
  ausente y el arranque limpio como gate).
- `deploy/collector/otel-collector-aws.yaml` (7 exporters: awsxray, awsemf,
  awscloudwatchlogs, prometheus, prometheusremotewrite, otlphttp/tempo,
  otlphttp/loki; `filter/cloudwatch-metrics` con whitelist de 4 contadores).
- `deploy/collector/otel-collector-gcp.yaml` (`googlecloud`,
  `googlemanagedprometheus`, `otlphttp/jaeger-gcp`).
- `docs/evidence/02-aws-ecs-session.md` (collector ADOT corriendo en la task
  de la sesión real del 2026-10-12). Hotfix filelog del 2026-10-11
  (operadores inválidos en 0.162.0 detectados en GKE: el parche del smoke no
  se había validado nunca con `validate`).
