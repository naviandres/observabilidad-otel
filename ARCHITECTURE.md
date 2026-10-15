# ARCHITECTURE.md — arquitectura dual-cloud del pipeline OTel

> Documento de referencia (Diátaxis: *explanation*). Describe la arquitectura
> **como existe en este repositorio**, no aspiracional: cada afirmación enlaza
> con artefactos versionados o con evidencia en `docs/evidence/`. Las
> decisiones individuales están justificadas en [`docs/adr/`](docs/adr); el
> modelo de coste en [COST.md](COST.md); la ejecución en [README.md](README.md).

## 1. Visión general

Dos microservicios FastAPI (`service-a` → `service-b` → PostgreSQL 16)
instrumentados con el SDK de OpenTelemetry, un **Collector por nube** (ADOT en
AWS; contrib/GCP-distro en GCP y local) y los backends nativos de cada
proveedor. El `trace_id` W3C es el pivote canónico de correlación y **cruza la
frontera de nubes**: la misma trazada puede verse en X-Ray (AWS) y en Cloud
Trace (GCP) simultáneamente.

```
                        ┌─────────────────────────────────────────────┐
                        │                 MODO DUAL                   │
                        │   service-a (ECS Fargate, AWS)              │
                        │        │  HTTP /inventory/reserve            │
                        │        ▼  INVENTORY_URL → LB regional       │
                        │   service-b (GKE Standard, GCP) ── SQL ──► postgres (GKE)
                        └─────────────────────────────────────────────┘

  AWS (modo mono)                       GCP (modo mono)
  ───────────────                       ─────────────────
  service-a :8000                       service-a :8080 (ClusterIP)
    │ OTLP/gRPC :4317                     │ OTLP/gRPC
    ▼                                     ▼
  service-b :8001                       service-b :8001 (ClusterIP)
    │ SQL :5432                             │ SQL :5432
    ▼                                     ▼
  postgres (misma task)                 postgres (mismo pod)

  collector ADOT v0.46.0 (misma task)   collector contrib 0.162.0 (pod Helm)
    ├─ awsxray ──────────► X-Ray          ├─ googlecloud ──► Cloud Trace
    ├─ awsemf ───────────► CloudWatch     │                  (fan-out) ─► Jaeger UI
    ├─ awscloudwatchlogs ► Logs              │                              (pod en-cluster)
    └─ prometheus :8889                      ├─ googlemanagedprometheus ─► Cloud Monitoring
  sidecar cloudwatch-agent ► ECS metrics     ├─ (fluent-bit/GKE) ────────► Cloud Logging
                                             └─ prometheus exporter :8889
```

- **Un solo código, tres modos** (README: modos 1-4): el binario de aplicación
  no cambia entre local, AWS y GCP; solo el YAML del collector, las variables
  de entorno y el cómputo. Esto hace honesto el A/B del benchmark
  ([`docs/evidence/04-overhead.md`](docs/evidence/04-overhead.md)).
- Local: `docker compose` con Jaeger all-in-one + Prometheus + Grafana OSS
  ([`deploy/docker-compose.observability.yml`](deploy/docker-compose.observability.yml)).

## 2. Flujo de una request

1. El cliente (k6 o `curl`) inyecta `traceparent` W3C en `POST /orders`.
2. `service-a` propaga el contexto a `service-b` por httpx instrumentado y este
   a PostgreSQL (spans SQLAlchemy/psycopg2). Spans de negocio custom:
   `order.validate`, `inventory.reserve`, `payment.authorize`, `order.persist`
   (A) e `inventory.query`, `payment.gateway_call` (B).
3. El SDK exporta OTLP/gRPC al collector (`:4317`); logs structlog JSON
   llevan `trace_id`/`span_id` inyectados por el SDK; métricas (contadores +
   histogramas) viaen por OTLP al collector.
4. El collector normaliza recursos (`resourcedetection`, `resource`,
   `transform/logs`, `attributes/redact`) y abanica a los backends de su nube.
5. Correlación: el mismo `trace_id` es consultable en las 3 señales; los
   exemplars métrica→traza viven **en el scrape de la app** (`:8000/metrics`,
   OpenMetrics) — ver [ADR-007](docs/adr/007-exemplars-app-level.md).
6. En modo dual, el paso 2 salta de nube (ECS → LB regional → GKE) y el
   `trace_id` aparece en X-Ray **y** Cloud Trace (aserción A13,
   [`docs/evidence/03-correlation.md`](docs/evidence/03-correlation.md)).

## 3. Topología por nube (lo desplegado)

### AWS — ECS Fargate ([`deploy/aws/`](deploy/aws), [`infra/aws/`](infra/aws))

- **Una task** (`cpu 1024`/`memory 2048`, `awsvpc`) con 5 contenedores:
  service-a, service-b, postgres (imagen propia con seed horneado), collector
  ADOT (imagen propia con YAML horneado) y sidecar `cloudwatch-agent`
  (non-essential). Perfiles y contratos verificados en vivo:
  [`docs/aws-ecs.md`](docs/aws-ecs.md).
- Red: VPC + 1 subnet pública + IGW, **sin NAT ni VPC endpoints** (lista
  negra). SG **sin ingress permanente**; el acceso es una regla /32 temporal a
  la IP del operador ([ADR-003](docs/adr/003-acceso-sin-tunnel-ssm.md)).
- Identidad: roles `otel-lab-exec-ecs` / `otel-lab-task-ecs` least-privilege;
  secretos por **SSM Parameter Store** (nunca Secrets Manager).
- Ciclo de coste: `make aws-start` / `aws-stop` / `aws-destroy` (+ verificación
  de teardown). Terraform (`infra/aws`, state S3 con locking nativo,
  workspaces `lab|bench`) describe la infraestructura permanente — budgets,
  alarmas, buckets, registros — **no** gobierna la sesión de cómputo.

### GCP — GKE Standard ([`helm/otel-lab/`](helm/otel-lab), [`infra/gcp/`](infra/gcp))

- Cluster **zonal** (regular channel), node pool **spot** con autoscaling 0-3;
  perfil `e2-medium` (la escalada desde `e2-small` está justificada en
  [ADR-005](docs/adr/005-perfil-nodo-gke.md)).
- 5 workloads vía Helm: service-a, service-b, postgres, collector contrib y
  Jaeger all-in-one (`jaeger-ui-on-gcp`, ClusterIP) con fan-out de trazas a
  Cloud Trace **y** Jaeger
  ([`docs/evidence/04-jaeger-gcp.md`](docs/evidence/04-jaeger-gcp.md)).
- Red: VPC + subnet con **Private Google Access**, sin Cloud NAT, sin ingress
  público; el único LB (regional, service-b) existe solo para el modo dual y
  muere con el cluster.
- Identidad: **Workload Identity** para el SA del collector (cero claves JSON
  en pods); contraseña de Postgres montada desde **Secret Manager** vía add-on
  CSI, habilitado **en el create** del cluster.
- Ciclo de coste: fee de plano $0.10/h desde el create hasta el delete — por
  eso `gcp-destroy` (no `gcp-stop`) es el DoD entre bloques de trabajo
  ([`docs/evidence/cluster-hours.csv`](docs/evidence/cluster-hours.csv)).

## 4. Backends de señales

| Señal | Local | AWS | GCP |
|---|---|---|---|
| Trazas | Jaeger all-in-one | X-Ray (+Tempo opcional) | Cloud Trace + Jaeger UI en-cluster |
| Métricas | Prometheus (`:8889` scrape) | CloudWatch EMF (whitelist de 4 contadores) + Prometheus scrape | Cloud Monitoring + Managed Prometheus |
| Logs | stdout → collector | CloudWatch Logs (retención 3 d) | Cloud Logging (fluent-bit + pipeline OTLP) |
| Dashboards | Grafana OSS `uid otel-lab-slo` (6 paneles, queries del scrape real) | CloudWatch nativo (≤3 widgets) | Cloud Monitoring (6 paneles, `mosaicLayout`) |

Artefactos: [`dashboards/grafana-slo.json`](dashboards/grafana-slo.json),
[`dashboards/cloudwatch-slo.json`](dashboards/cloudwatch-slo.json),
[`dashboards/cloud-monitoring-gcp.json`](dashboards/cloud-monitoring-gcp.json).
Verificación en vivo: [`docs/evidence/05-dashboards-local.md`](docs/evidence/05-dashboards-local.md),
[`docs/evidence/02-validation-dashboard-gcp.md`](docs/evidence/02-validation-dashboard-gcp.md).

## 5. Modelo de acceso y seguridad

- **Sin HTTPS público en ninguna nube**: el path de entrada es la regla /32
  temporal (AWS) o `kubectl port-forward` autenticado (GCP). No hay ALB, ACM,
  cert-manager ni Cloud LB permanente (salvo el LB regional efímero del modo
  dual).
- SG/firewall en steady state: **0 ingress** (verificado post-revoke,
  `IpPermissions []`).
- Secretos: SSM Parameter Store (AWS) / Secret Manager + Workload Identity
  (GCP). `.env.example` sin secretos; los valores reales nunca se copian al
  repo (se referencian por nombre/ARN).
- Identidades de despliegue least-privilege (`otel-lab-deploy` en ambos lados,
  nunca root) — sondeo comportamental en
  [`docs/evidence/00-aws-baseline.md`](docs/evidence/00-aws-baseline.md).

## 6. Consecuencias de coste de la topología (resumen)

| Decisión de topología | Consecuencia |
|---|---|
| 5 contenedores en 1 task Fargate | 1 perfil (1 vCPU/2 GB ≈ $0.049/h) en vez de 4 tasks; coste por segundo, $0 fuera de sesión |
| Sin NAT/ALB/VPC endpoints (AWS) | egress por IGW con IP efímera; lista negra respetada |
| GKE Standard + spot (GCP) | fee plano $0.10/h es el coste dominante de sesión; el nodo spot es marginal (~$0.012/h) |
| Sin Cloud NAT, con PGA (GCP) | egress a APIs de Google sin coste de NAT |
| EMF whitelist de 4 contadores + `NoDimensionRollup` | métricas CloudWatch dentro del free tier (26→1 variantes de serie) |
| Trazas gestionadas OFF durante el benchmark | X-Ray y Cloud Trace demostraron $0 de coste de campaña ([ADR-009](docs/adr/009-sampling-y-trazas-off-benchmark.md)) |
| Retención 3 d / lifecycle 7 d / keep-last-5 | higiene de logs, evidencia y registros dentro de free tiers |

Detalle y guardarraíles: [COST.md](COST.md). Decisiones:
[ADR-008](docs/adr/008-modelo-de-coste-creditos-y-guardarrailes.md).

## 7. Pipelines de verificación (CI)

| Workflow | Cuándo | Qué |
|---|---|---|
| `terraform-plan.yml` | PR que toca `infra/aws/**` | fmt/validate/plan (workspace lab, `init -backend=false`) + `guard_cost.py --provider aws`; **nunca apply** |
| `terraform-plan-gcp.yml` | PR que toca `infra/gcp/**` | espejo GCP + `guard_cost.py --provider google`; **nunca apply** |
| `verify-correlation-local.yml` | PR que toca apps/scripts/deploy | `verify_correlation.py` en modo local sobre compose (aserciones de nube en SKIP honesto) |
| `deploy.yml` | **solo `workflow_dispatch`** con aprobación de GitHub Environments | jobs `deploy-aws` / `deploy-gcp`: apply del root correspondiente (workspace `lab`) tras gate `guard_cost`; el apply nunca dispara en push |

`make validate-collector` (local) replica la validación de ambos binarios
(ADOT por arranque limpio — no tiene `validate`—, contrib por `validate`).

## 8. Dónde vive cada pieza

| Carpeta | Rol |
|---|---|
| `service-a/`, `service-b/` | apps FastAPI + `app/telemetry.py` (motor OTel único, toggle `OTEL_ENABLED`, sampler env) + `app/latency.py` (exemplars app-level) |
| `deploy/collector/` | `otel-collector-{local,aws,gcp,bench}.yaml` + Dockerfile del collector |
| `deploy/aws/` | task definition (template con tokens), `postgres.Dockerfile`, políticas IAM |
| `helm/otel-lab/` | chart GKE (5 workloads + ConfigMap del collector + SecretProviderClass) |
| `infra/aws/`, `infra/gcp/` | roots Terraform (states S3/GCS, workspaces `lab|bench`) + `infra/modules/` |
| `dashboards/` | 3 JSON de dashboard (uno por backend) + `cloudwatch-alarms.json` |
| `k6/`, `bench/`, `scripts/` | carga, artefactos crudos del benchmark y orquestadores (`verify_correlation.py`, `guard_cost.py`, `cost_guard.ps1`, `access.ps1`, …) |
| `docs/adr/` | registro de decisiones (10 ADRs) |
| `docs/evidence/` | evidencia en vivo por fase + CSVs de horas + `img/` (figuras) |
| `docs/report/` | fuente markdown y PDF del reporte técnico |
