# CONTRIBUTING.md

Guía para contribuir a este repositorio. Léela entera antes del primer PR.

## 0. Regla de oro: destruye la sesión de nube cuando termines

El proyecto opera **dentro de créditos con guardarraíles** ($100 AWS / $300
GCP). La sesión de nube (task Fargate, cluster GKE) **se enciende para
trabajar y se destruye al terminar**, con verificación:

```bash
make aws-stop && make aws-destroy && make aws-net-destroy   # AWS
make gcp-destroy                                            # GCP
make cost-guard                                             # nada facturando
```

`gcp-stop` escala el nodo a 0 pero **no** detiene el fee de plano ($0.10/h):
entre bloques de trabajo se destruye el cluster, no se pausa. Registra cada
ciclo en `docs/evidence/instance-hours.csv` / `cluster-hours.csv`.

## 1. Cómo levantar el stack local (modo $0)

```bash
docker compose up --build -d          # postgres, collector, jaeger, prometheus, grafana, a, b
# smoke
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'
make verify-correlation VERIFY_ENDPOINT=http://localhost:8000
```

El esquema y el seed se inicializan solos (`init-scripts/`). El desarrollo
quotidiano ocurre aquí: los modos nube (README, modos 2-4) son para evidencia.

## 2. Toolchain fijado (sin `latest`)

| Herramienta | Versión | Dónde se fija |
|---|---|---|
| Python (apps) | 3.12 | Dockerfiles de `service-a/`, `service-b/` |
| Dependencias Python | pins exactos | `service-*/requirements.txt`, `requirements-dev.txt` |
| Collector local/GCP | `otel/opentelemetry-collector-contrib:0.162.0` | compose, chart, Makefile |
| Collector AWS | `public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0` | `deploy/collector/otelcol-contrib.dockerfile` |
| Jaeger / Prometheus / Grafana / k6 | `1.76.0` / `v2.53.0` / `11.1.0` / `2.0.0` | compose y README (tabla de imágenes) |
| Terraform (root AWS) | 1.10.5 (`~> 1.10`) | `infra/aws/versions.tf`, workflow, `infra/README.md` |
| Terraform (root GCP) | **docker `hashicorp/terraform:1.9.8`** (sin binario local; el entrypoint ya es `terraform`) | `infra/README.md` §GCP |
| aws CLI | 2.32.31 (+ session-manager-plugin 1.2.835.0) | `docs/evidence/00-aws-baseline.md` |
| gcloud SDK | 580.0.0 | `docs/evidence/00-free-tier-baseline-gcp.md` |
| helm / kubectl | los del contexto del cluster GKE | `make deploy-gcp` / `port-gcp` |
| pandoc (reporte) | imagen `pandoc/latex` (tag fijado en el Makefile) | target `report` |
| PowerShell | 7 (`pwsh`) para `scripts/*.ps1` | targets del Makefile |

k6 **no** se instala en el host: corre como imagen dentro de
`scripts/benchmark.ps1`.

## 3. Referencia del Makefile (target → qué hace)

### Ciclo de vida local / validación

| Target | Qué hace |
|---|---|
| `validate-collector` | valida offline ambos binarios: ADOT v0.46.0 por arranque limpio (no tiene `validate`) y contrib 0.162.0 con `validate` sobre los YAMLs |
| `verify-correlation` | framework de correlación en modo local (A1/A2; aserciones de nube en SKIP honesto) |
| `verify-correlation-gcp` | idem + banderas Cloud Trace/Logging/Monitoring (requiere ADC y port-forward) |
| `verify-correlation-aws` | idem + X-Ray/CloudWatch (boto3, perfil `otel-lab`; A8 exige `PROM_URL` app-level) |
| `verify-correlation-dual` | ambas banderas: evalúa la aserción cruzada A13 (mismo `trace_id` en las dos nubes) |
| `report` | regenera `docs/report/report.pdf` desde `docs/report/report.md` con pandoc/latex en Docker (ver §6) |

### AWS — ECS Fargate

| Target | Qué hace |
|---|---|
| `aws-net-create` / `aws-net-destroy` | VPC + subnet pública + IGW + route table + SG **sin ingress** (idempotente por tag) / destrucción |
| `aws-push` | login ECR + build/push de las 4 imágenes (app×2, collector con YAML horneado, postgres con seed horneado) |
| `aws-start` | red + cluster + render/registro de task definition + service FARGATE `desired_count=1` con ECS Exec |
| `aws-stop` | `desired_count=0` (la task deja de facturar; no borra nada) |
| `aws-destroy` | borra service + cluster y **verifica** que no queda nada activo |
| `aws-access-grant` / `aws-access-revoke` | regla ingress TCP **/32 temporal** a la IP del operador y su revocación simétrica; el SG vuelve a 0 ingress verificado |
| `aws-verify` | `verify_correlation.py --include-aws` contra la IP pública del ENI de la task |
| `aws-dashboard` | dashboard CloudWatch nativo (≤3 widgets, `put-dashboard` idempotente) |
| `aws-alarms` | 2 alarmas de guardarraíl; cablea `AlarmActions` SNS si pasas `SNS_TOPIC_ARN=…` |

### GCP — GKE Standard

| Target | Qué hace |
|---|---|
| `gcp-start` | VPC + subnet (PGA, sin NAT) + cluster zonal spot `e2-medium` autoscaling 0-3 con add-on CSI **en el create** + binding Workload Identity + kubeconfig |
| `deploy-gcp` | sincroniza config del collector y seed al chart, `helm upgrade --install` y espera rollout |
| `gcp-expose` | Service LoadBalancer **regional** de service-b (solo modo dual; default OFF) |
| `port-gcp` | port-forward autenticado: service-a `8080`, collector `4317`/`8889` (sin ingress público) |
| `port-jaeger-gcp` | port-forward de la Jaeger UI en-cluster (`16686`) |
| `gcp-stop` | escala el pool a 0 — **ojo**: el fee de plano sigue corriendo |
| `gcp-destroy` | borra el cluster y **verifica** que no aparece en la lista (DoD entre bloques) |

### Terraform y guardarraíles

| Target | Qué hace |
|---|---|
| `tf-validate-aws` | `init -backend=false` + `fmt -check` + `validate` del root AWS (réplica del job de PR; no toca state) |
| `tf-plan-aws` | plan del root AWS + gate `guard_cost.py --provider aws` sobre el JSON |
| `cost-guard` | guardarrail en vivo: Cost Explorer diario + barrido de recursos que facturan ahora (AWS; GCP si `gcloud` está en PATH) |
| `budget-hours` | gate de 150 h de task Fargate sobre `docs/evidence/instance-hours.csv` |
| `budget-sync` | refresca ese CSV desde las tasks del cluster y re-aplica el gate |

Variables override sin editar el archivo (`VAR ?=`): `IMAGE_TAG`,
`OTEL_TRACES_EXPORTER`, `PROM_URL`, `VERIFY_ENDPOINT`, `SNS_TOPIC_ARN`,
`INVENTORY_URL`, `GCP_*`, `AWS_*` — ver cabecera del [Makefile](Makefile).

## 4. Benchmark

```powershell
powershell -File scripts/benchmark.ps1 -Mode full -CloudLogs      # ABBA 6 runs
powershell -File scripts/benchmark.ps1 -Mode attrib -CloudLogs    # atribución s=1.0/0.0
python scripts/bench_report.py    # agrega k6/*.json -> docs/evidence/04-overhead.md
```

Corre contra el stack **local** (orden ABBA, warmup + 5 min steady, mismo
digest A/B). Nunca levantes una sesión de nube para el benchmark: el coste por
nube se lee del ingest real de logs (decisión C2,
`docs/adr/009-sampling-y-trazas-off-benchmark.md`).

## 5. Estilo de documentación y lenguaje

- **Documentación, ADRs, comentarios de diseño: español.** Código, comandos,
  nombres de recurso y mensajes de commit: **inglés**.
- Cuatro géneros, no los mezcles (Diátaxis): README = manual de ejecución;
  `ARCHITECTURE.md`/`COST.md`/tabla del Makefile = referencia;
  `docs/adr/` = explicación/decisión; `docs/evidence/` = registro verificable
  con fecha y fuente.
- **Regla anti-invento:** todo número sin salida CLI, JSON de evidencia o URL
  citada se marca como "dato declarado". No cites un comando que no has
  ejecutado; no documentes un target que no existe.
- Un ADR no se reescribe: se supersa con otro que lo enlace.

## 6. Gate anti-jerga interna (obligatorio en PR)

Este repositorio es **entregable público**: no debe nombrar el proceso interno
que lo produce (tareas de planificación, ramas de trabajo, herramientas del
proyecto madre, bitácoras externas, ni "lado/pata" como sustantivo de fase).
Antes de cada PR:

```bash
git grep -nE 'feature #|feat/[0-9]+|#[0-9]+|harness|sub-tarea|leader spec|pata|progress/|feature_manager' -- '*.md' '*.yaml' '*.yml' '*.json' '*.tf' 'Makefile'
# esperado: 0 coincidencias (el exit code de git grep debe ser 1)
```

Excepciones admitidas: `#[0-9]+` dentro de fragmentos de URL legítimos y el
vocabulario propio del dominio (p. ej. "aserción A8", "decisión C2"); los
artefactos binarios (p. ej. `docs/report/report.pdf`) quedan fuera del gate por
su contenido de texto — el gate se aplica a los fuentes legibles (el texto del
PDF se comprueba sobre `report.md`, su fuente única).

## 7. Commits y ramas

- **Conventional Commits** en inglés: `docs(report): …`, `ci(deploy): …`,
  `fix(collector): …`, `feat(aws): …`, `chore(env): …`. Un commit lógico por
  cambio; el cuerpo explica el *por qué* con evidencia (archivo/sesión), no el
  *qué* (eso lo dice el diff).
- Ramas por unidad mergeable: `feat/<nombre-corto>`, `fix/<nombre-corto>`;
  rebase sobre main antes del merge; sin commits directos a main.
- Cada cambio de contrato (puertos, flags, JSON de dashboard, config de
  collector) va **acompañado de su test** en `tests/` (pytest, offline).

## 8. Checklist del PR

1. `pytest tests/ -q` en verde (suite del entregable, sin red).
2. `make validate-collector` (si tocaste `deploy/collector/`).
3. `make tf-validate-aws` + workflow `terraform-plan-gcp` en verde (si tocaste
   `infra/`); recuerda: **el CI no aplica nada** — `apply` solo manual con
   aprobación de Environment (`.github/workflows/deploy.yml`).
4. Gate anti-jerga (§6) vacío.
5. Sin secretos: `git grep -nE 'AKIA[0-9A-Z]{16}|password.*=.*[^< ]' ` y
   revisión de `.env` (nunca se commitea; solo `.env.example`).
6. Si tocaste la narrativa de diseño: ADR nuevo o enlazado.
7. Si tocaste `docs/report/report.md`: regenera el PDF (§9) y commitea ambos.

## 9. Regenerar el reporte PDF

La fuente única es `docs/report/report.md`; el PDF es un artefacto:

```bash
make report    # docker run pandoc/latex (tag fijado) -> docs/report/report.pdf
```

Requisitos: Docker en marcha y la imagen del tag fijado (se descarga sola).
Las figuras referenciadas viven en `docs/evidence/img/`; los ficheros marcados
`PLACEHOLDER-HUMAN-REQUIRED` esperan capturas reales del operador (ver la
tabla en `docs/evidence/img/README.md`). Verifica el conteo de páginas tras
regenerar (p. ej. `python -c "from pypdf import PdfReader; print(len(PdfReader('docs/report/report.pdf').pages))"`).
