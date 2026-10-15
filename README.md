# observabilidad-otel — Manual de ejecución

Pipeline de observabilidad **end-to-end con OpenTelemetry dual-cloud**:
`service-a` → `service-b` → PostgreSQL instrumentados con el SDK de OTel, un
**Collector** (contrib en local/GCP, ADOT en AWS) que enruta trazas, métricas y
logs a los backends de cada nube, y **correlación cross-signal por `trace_id`
W3C que cruza AWS↔GCP**. El código de aplicación es idéntico en los cuatro
modos; solo cambian el cómputo, el YAML del collector y las variables de
entorno.

Este README es el **único manual de ejecución**: un evaluador externo puede
reproducir los cuatro modos solo con este documento. Las cifras de coste y los
guardarraíles están en [COST.md](COST.md); el detalle del despliegue AWS en
[docs/aws-ecs.md](docs/aws-ecs.md); la evidencia en vivo de cada fase en
[`docs/evidence/`](docs/evidence).

## Índice

- [Los cuatro modos](#los-cuatro-modos-resumen)
- [Modo 1 — Local (docker compose)](#modo-1--local-docker-compose)
- [Modo 2 — AWS (ECS Fargate)](#modo-2--aws-ecs-fargate)
- [Modo 3 — GCP (GKE Standard)](#modo-3--gcp-gke-standard)
- [Modo 4 — Dual-cloud (AWS + GCP simultáneo)](#modo-4--dual-cloud-aws--gcp-simultáneo)
- [Secciones comunes](#secciones-comunes)
  - [Dashboards SLO](#dashboards-slo-provisioning-por-código-nunca-a-mano)
  - [Benchmark de overhead](#benchmark-de-overhead)
  - [Imágenes (tags fijados)](#imágenes-tags-fijados-sin-latest)
- [Tabla de coste / créditos y guardarraíles por nube](#tabla-de-coste--créditos-y-guardarraíles-por-nube)
- [Troubleshooting](#troubleshooting)
- [Mapa de documentación del repo](#mapa-de-documentación-del-repo)

## Los cuatro modos (resumen)

| | **1. Local** | **2. AWS** | **3. GCP** | **4. Dual-cloud** |
|---|---|---|---|---|
| Dónde corre | `docker compose` en tu PC | ECS **Fargate** (task única, `awsvpc`) | **GKE Standard** zonal (pool spot, Helm) | service-a en ECS → service-b en GKE |
| Collector | contrib `0.162.0` (`otel-collector-local.yaml`) | **ADOT** `v0.46.0` (`otel-collector-aws.yaml`) | contrib (`otel-collector-gcp.yaml`) | el de cada nube, simultáneamente |
| Trazas | Jaeger | X-Ray | Cloud Trace | **el mismo `trace_id` en X-Ray y Cloud Trace** |
| Logs | stdout → collector → Jaeger/Prom | CloudWatch Logs | Cloud Logging | ambos backends |
| Métricas | Prometheus + Grafana OSS | CloudWatch EMF (+ dashboard nativo) | Cloud Monitoring / Managed Prometheus | ambos |
| Coste | **$0** | dentro de $100 de créditos, con teardown | dentro de $300 de créditos (90 días), con teardown | ≈ $0.35 por sesión de 2 h |
| Toolchain | Docker + Compose v2 | aws CLI, docker, make, pwsh | gcloud, docker, kubectl, helm, make | ambas |
| Ciclo | `up` / `down` | `aws-push → aws-start → … → aws-destroy` | `gcp-start → deploy-gcp → … → gcp-destroy` | GCP primero, AWS después, teardown inverso |

**Regla operativa (aplica a los modos 2-4):** la sesión de nube se enciende
para trabajar y **se destruye al terminar**; el teardown y la verificación de
coste son parte del DoD. El ciclo diario de desarrollo es el modo local ($0).

---

## Modo 1 — Local (docker compose)

### Prerrequisitos / toolchain

- **Docker + Docker Compose v2** (`docker compose version`).
- Para los targets `make` de verificación y el benchmark: `make` (o los
  comandos crudos equivalentes, que están en el propio [Makefile](Makefile),
  legible) y **Python 3.12** con `pip install -r service-a/requirements.txt`
  (los scripts leídos por los targets solo usan stdlib + lo que traen los
  servicios).
- PowerShell (el repo se opera en Windows; los equivalentes bash son triviales).

### Credenciales y variables de entorno

**Ninguna.** El stack local no necesita credenciales: las contraseñas de
Postgres son locales de desarrollo (`postgres:postgres`, fijadas en
[docker-compose.yml](docker-compose.yml)) y los exporters apuntan al collector
en la red del compose. `.env` no se usa en este modo; las variables de
`service-a`/`service-b` viajan inline en el compose.

### Comandos (deploy)

```powershell
# stack completo: postgres, collector, jaeger, prometheus, grafana, service-a, service-b
docker compose up --build -d
docker compose ps

# arranque por capas (núcleo primero, observabilidad después)
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.observability.yml up -d
```

El esquema de BD **se inicializa solo** con un volumen nuevo:
`init-scripts/01-orders-schema.sql` crea `orders`/`order_items` (DB `orders`) y
`init-scripts/02-inventory-db.sql` crea y siembra la DB `inventory` (SKUs
`LAPTOP-001`, `MOUSE-001`, `KEYBOARD-001`).

### Verificación / evidencia

```powershell
# 1) smoke: crear una orden (200)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'

# 2) métricas OTel expuestas por el Collector
curl.exe -s http://localhost:8889/metrics | Select-String http_server

# 3) log JSON con trace_id
docker compose logs service-a | Select-String trace_id

# 4) correlacion cross-signal (aserciones A1-A2; el resto se SKIP por no ir
#    activas las banderas de nube — no rompen el target). El default del
#    Makefile (8080) es el port-forward de GKE; en compose service-a escucha
#    en 8000:
make verify-correlation VERIFY_ENDPOINT=http://localhost:8000

# 5) UIs: Jaeger http://localhost:16686 · Prometheus http://localhost:9090
#          Grafana http://localhost:3000 (admin/admin)
```

Evidencia de la verificación local de dashboards:
[`docs/evidence/05-dashboards-local.md`](docs/evidence/05-dashboards-local.md);
salida tipo de la correlación: [`docs/evidence/03-correlation.json`](docs/evidence/03-correlation.json).

### Coste y teardown

**$0** — no hay recursos de nube. Teardown:

```powershell
docker compose down          # conserva el volumen postgres_data
docker compose down -v       # borra el volumen (el seed se rehace al subir)
```

### Ejecución sin Docker (desarrollo)

```powershell
pip install -r service-a/requirements.txt
uvicorn app.main:app --app-dir service-a --host 0.0.0.0 --port 8000
uvicorn app.main:app --app-dir service-b --host 0.0.0.0 --port 8001
```

Docs de API: <http://localhost:8000/docs>. Métricas de proceso:
<http://localhost:8000/metrics> y <http://localhost:8001/metrics>.

---

## Modo 2 — AWS (ECS Fargate)

La misma aplicación sobre **una única task Fargate** (`awsvpc`) con 5
contenedores: `service-a` → `service-b` → `postgres` + el **Collector ADOT** +
el sidecar `cloudwatch-agent`. Las 4 señales van a X-Ray (trazas), CloudWatch
Logs (logs), CloudWatch EMF (métricas) y métricas de infraestructura ECS.
**Sin ALB**: el acceso es una regla ingress **temporal /32** a la IP pública del
ENI de la task (SSM **no** soporta port-forwarding a Fargate — desviación
verificada en vivo y documentada en [docs/aws-ecs.md](docs/aws-ecs.md) §5).

### Prerrequisitos / toolchain

- `aws` CLI v2 con un **perfil de despliegue** (nunca root), `docker`, `make`,
  `pwsh` (los targets `aws-access-*`, `aws-alarms`, `cost-guard`,
  `budget-*` invocan scripts PowerShell).
- Opcional para shell dentro de la task (`aws ecs execute-command`):
  `session-manager-plugin` en el `PATH` (ECS Exec solo da shell, no reenvío de
  puertos).
- Para la verificación con boto3 (`--include-aws`): `pip install boto3` en el
  Python que lanza `scripts/verify_correlation.py`.

### Credenciales y variables de entorno

- **Credenciales AWS**: solo en `~/.aws/` (perfil `otel-lab`). Nunca en el repo
  ni en `.env`. El perfil lo fija `AWS_PROFILE` (default del Makefile:
  `otel-lab`).
- **SSM Parameter Store** — la task los resuelve todos vía `valueFrom` por ARN;
  **si falta uno, la task no arranca**. Creados **una sola vez por el humano**
  (Fase 0; el valor nunca se copia a este README — se referencia por nombre).
  Lista canónica con el shape de cada valor:
  [`deploy/aws/README.md`](deploy/aws/README.md) (tabla SSM). Son **10**:
  - `/otel-lab/postgres/password` (SecureString — `POSTGRES_PASSWORD`)
  - `/otel-lab/database-url/service-a` = `postgresql://postgres:<pw>@localhost:5432/orders`
  - `/otel-lab/database-url/service-b` = `postgresql://postgres:<pw>@localhost:5432/inventory`
  - `/otel-lab/grafana/prom-url` y `/otel-lab/grafana/prom-auth` (exporter
    `prometheusremotewrite` del collector ADOT)
  - `/otel-lab/grafana/tempo-endpoint` y `/otel-lab/grafana/tempo-auth`
    (`otlphttp/tempo`)
  - `/otel-lab/grafana/loki-endpoint` y `/otel-lab/grafana/loki-auth`
    (`otlphttp/loki`)
  - `/otel-lab/cloudwatch-agent/config` (JSON del agente — `CW_CONFIG_CONTENT`)
- **Recursos permanentes creados una sola vez**: repos ECR
  `otel-lab/{service-a,service-b,otel-collector,postgres}` (tag immutable),
  roles `otel-lab-exec-ecs` (ECR pull + logs + lectura SSM) y
  `otel-lab-task-ecs` (X-Ray + CloudWatch + SSM/EC2 read) con las políticas de
  [`deploy/aws/iam/`](deploy/aws/iam), y el bucket S3 del state de Terraform.
- Las variables de build/run que consumen los targets (`AWS_REGION`,
  `AWS_ACCOUNT_ID`, `AWS_PROFILE`, `IMAGE_TAG`, `ECS_*`, `TASK_ROLE`,
  `EXEC_ROLE`, `NET_NAME`, `VPC_CIDR`, `SUBNET_CIDR`, `INVENTORY_URL`) tienen
  default en el [Makefile](Makefile) y plantilla en [.env.example](.env.example)
  — **sin secretos embebidos**.

### Comandos y pasos de despliegue

| # | Comando | Qué hace |
|---|---|---|
| 1 | `make validate-collector` | valida offline el YAML ADOT (arranca el binario local, sin coste; ADOT no tiene subcomando `validate`) |
| 2 | `make aws-push` | login ECR + build/push de las 4 imágenes (collector con el YAML horneado; postgres con `init-scripts/` horneado vía [`deploy/aws/postgres.Dockerfile`](deploy/aws/postgres.Dockerfile); tag `$(IMAGE_TAG)`, nunca `:latest`) |
| 3 | `make aws-start` | red (VPC + subnet pública + IGW, sin NAT) + cluster + render/registro de la [task definition](deploy/aws/task-definition.json) + service FARGATE `desired_count=1` con `assignPublicIp` efímero y ECS Exec |
| 4 | `make aws-access-grant` | regla ingress **temporal TCP /32** a la IP pública del operador (autodetectada) para 8000/8889; revela la IP pública del ENI de la task |
| 5 | `make aws-verify` | corre `verify_correlation.py --include-aws` contra esa IP (`:8000` + `--prometheus-url :8889/metrics`) |
| 6 | `make aws-access-revoke` | revoca la regla temporal; el SG vuelve a **0 ingress** (steady-state) |
| 7 | `make aws-stop` | `desired_count=0`: la task deja de facturar (idempotente, no borra nada) |
| 8 | `make aws-destroy` | botón nuclear: borra service + cluster y **verifica** que no queda nada activo |
| 9 | `make aws-net-destroy` | borra la red tagueada (VPC, subnet, IGW, route table, SG) |

Smoke manual (equivalente a `aws-verify`):

```powershell
make aws-access-grant   # imprime <IP_TASK> publica del ENI
Invoke-RestMethod -Method Post -Uri http://<IP_TASK>:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'
make aws-access-revoke  # teardown del acceso al terminar
```

IaC complementaria (no crea la sesión de cómputo — el cómputo lo gobierna el
Makefile): `make tf-validate-aws` y `make tf-plan-aws` (plan + gate de lista
negra con [`scripts/guard_cost.py`](scripts/guard_cost.py)); el flujo con state
real y los comandos de bootstrap están en [infra/README.md](infra/README.md).

### Verificación / evidencia

- **Dónde mirar las señales**: X-Ray → trazas `service-a`/`service-b`
  correlacionadas por `trace_id`; CloudWatch Logs → grupos
  `/otel/labs/ecs-platform/*` (`service-a`, `service-b`, `postgres`,
  `otel-collector`, `cloudwatch-agent`); CloudWatch → métricas EMF del namespace
  `OtelLab` + dashboard `make aws-dashboard` y alarmas `make aws-alarms`.
- **Qué verifica el framework**: `make aws-verify` (o la corrida manual de
  `verify_correlation.py --include-aws`) evalúa A1-A9; A8 (exemplars) exige
  apuntar el scrape a la app: `--prometheus-url http://<IP_TASK>:8000/metrics`
  (el exporter del collector en `:8889` no propaga exemplars,
  [docs/aws-ecs.md](docs/aws-ecs.md) §7).
- Evidencia en vivo: [`docs/evidence/02-aws-ecs-session.md`](docs/evidence/02-aws-ecs-session.md),
  [`docs/evidence/03-correlation-aws.json`](docs/evidence/03-correlation-aws.json),
  línea base de coste [`docs/evidence/00-aws-baseline.md`](docs/evidence/00-aws-baseline.md).

### Coste estimado

Task Fargate **1 vCPU / 2 GB** (los `cpu: "1024"` / `memory: "2048"` de
[`deploy/aws/task-definition.json`](deploy/aws/task-definition.json)) ≈
**$0.049/h** mientras `desired_count=1`; fuera de sesión ≈ $0. Presupuesto AWS: **$100 de créditos** con umbral diario $1
(`aws_budget` en IaC) y gate de **150 h de task** (`make budget-hours`,
[`docs/evidence/instance-hours.csv`](docs/evidence/instance-hours.csv)). El
modelo completo, por concepto, en [COST.md](COST.md) §1.

### Teardown (obligatorio)

`make aws-stop` (pausa sin borrar) → `make aws-destroy` (nuclear + verificación)
→ `make aws-net-destroy` (red). Higiene: retención de logs 3 días, ECR
keep-last-5. Nunca dejar `desired_count=1` fuera de sesión.

---

## Modo 3 — GCP (GKE Standard)

Modo nube del mismo pipeline sobre **GKE Standard** (cluster zonal
`us-central1-a`, node pool **spot** con autoscaling 0–3): `service-a` →
`service-b` → PostgreSQL con el Collector OTel exportando a Cloud Trace, Cloud
Logging y Cloud Monitoring (Managed Prometheus). El ciclo de vida lo gobierna el
[Makefile](Makefile); solo cambian el cómputo y el YAML del collector.

### Prerrequisitos / toolchain

- `gcloud` con ADC con acceso a tu proyecto (`gcloud auth login` +
  `gcloud config set project <proyecto>`), `docker`, `kubectl`, `helm`, `make`.
- API de GKE habilitada: `gcloud services enable container.googleapis.com`.
- Para la verificación (`--include-gcp`): ADC operativa —
  `gcloud auth application-default login` (el script habla con las APIs vía
  ADC + urllib; **no** requiere instalar ningún SDK de Google en Python).

### Credenciales y variables de entorno

- **ADC de `gcloud`** para el operador; en el cluster los pods **no llevan
  claves**: el collector se suplantó con el SA de GCP vía **Workload Identity**
  (binding incluido en `make gcp-start`), y la contraseña de Postgres se monta
  desde **Secret Manager** con el add-on CSI.
- **Secret Manager** (creado una sola vez, Fase 0): secreto **`pg-password`** —
  el chart lo referencia por nombre, el valor nunca va al repo ni a values.
- Variables de los targets (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_ZONE`,
  `GCP_CLUSTER`, `GCP_NAMESPACE`, `COLLECTOR_K8S_SA`, `COLLECTOR_GCP_SA`,
  `OTEL_TRACES_EXPORTER`) con default en el Makefile (proyecto
  `otel-lab-obs-2610`); override sin editar: `make deploy-gcp
  OTEL_TRACES_EXPORTER=nop`.

### Build & push de imágenes (Artifact Registry)

Las imágenes de app viven en AR; collector, postgres y jaeger usan imágenes
públicas fijadas por el chart. Creados una sola vez: repositorio **Artifact
Registry** `otel-lab` (docker) en `us-central1`. Desde esta carpeta:

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev

docker build -t us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-a:0.1.0 service-a
docker build -t us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-b:0.1.0 service-b
docker push us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-a:0.1.0
docker push us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-b:0.1.0
```

El tag `0.1.0` es el que fija
[`helm/otel-lab/values.yaml`](helm/otel-lab/values.yaml) (sin `:latest`).

### Comandos y pasos de despliegue

| # | Comando | Qué hace |
|---|---|---|
| 1 | `make gcp-start` | VPC + subnet (Private Google Access, sin NAT) + clúster zonal `otel-lab-std` (regular channel, nodo spot `e2-medium` autoscaling 0–3, **add-on Secret Manager CSI en el create** `--enable-secret-manager`), binding Workload Identity del SA del collector, kubeconfig |
| 2 | `make deploy-gcp` | sincroniza la config del collector ([`deploy/collector/otel-collector-gcp.yaml`](deploy/collector/otel-collector-gcp.yaml)) y el seed al chart, `helm upgrade --install` y espera el rollout |
| 3 | `make port-gcp` | port-forward autenticado **sin ingress público**: `svc/service-a 8080:8080` + collector `4317`/`8889` |
| 4 | `make port-jaeger-gcp` | Jaeger UI dentro del cluster (`svc/jaeger-ui-on-gcp 16686`), waterfall `service-a→service-b→DB` |
| (5) | `make gcp-expose` | **solo para el modo dual**: habilita el Service type LoadBalancer regional de `service-b` (default OFF) |
| 6 | `make gcp-stop` | escala el pool a 0 (deja de correr el nodo; el fee de plano sigue) |
| 7 | `make gcp-destroy` | botón nuclear: borra el clúster y **verifica** que no aparece en la lista |

Prerrequisitos del clúster: el add-on CSI va **en el create** (sin el driver los
pods quedan `ContainerCreating`); sobre un clúster existente, `gcloud container
clusters update otel-lab-std --location=us-central1-a --enable-secret-manager`
tarda ~40 min (`UPGRADE_NODES`). El binding Workload Identity ya viene incluido
en `gcp-start` — no hay que repetir nada.

### Verificación / evidencia

```powershell
kubectl -n otel-lab port-forward svc/service-a 18080:8080

# en otra terminal — SKUs sembrados: LAPTOP-001, MOUSE-001, KEYBOARD-001
Invoke-RestMethod -Method Post -Uri http://localhost:18080/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'

# aserciones A10-A12 (Cloud Trace/Logging/Monitoring) via ADC:
python scripts/verify_correlation.py --endpoint http://localhost:18080 --include-gcp
# (equivale a: make verify-correlation-gcp con VERIFY_ENDPOINT apuntando al port-forward)
```

`200` = orden creada (repetir el mismo SKU puede dar `409`: stock ya reservado;
el seed se rehace al reiniciar postgres). Dónde ver las 3 señales:

| Señal | Dónde |
|---|---|
| Trazas | **Cloud Trace** → busca el `trace_id` que sale en el log de la orden |
| Logs | **Cloud Logging** → logs de `service-a`, campo `jsonPayload.trace_id` |
| Métricas | **Cloud Monitoring** → dashboard **OTel Lab — SLIs GCP (Managed Prometheus)** (6 paneles; provisionado por su IaC — `google_monitoring_dashboard` en [`infra/gcp/dashboard.tf`](infra/gcp/dashboard.tf), que aplica el JSON tal cual). Si prefieres la CLI: `gcloud monitoring dashboards create` exige el envoltorio `dashboard:` alrededor del JSON, que el artefacto versionado NO lleva — usa la vía IaC |

Evidencia en vivo: [`docs/evidence/02-validation-dashboard-gcp.md`](docs/evidence/02-validation-dashboard-gcp.md),
[`docs/evidence/03-correlation-gcp.json`](docs/evidence/03-correlation-gcp.json),
[`docs/evidence/04-jaeger-gcp.md`](docs/evidence/04-jaeger-gcp.md), binding WI
[`docs/evidence/01-wi-binding-gcp.md`](docs/evidence/01-wi-binding-gcp.md),
línea base free-tier [`docs/evidence/00-free-tier-baseline-gcp.md`](docs/evidence/00-free-tier-baseline-gcp.md).

### Coste estimado

Fee de plano GKE Standard **$0.10/h** (corre desde el create hasta el delete,
aunque el pool esté a 0) + nodo spot e2-medium (~$0.012/h por nodo medido en
sesión; autoscaler baja a 0 en idle — el `~$0.04/h` del comentario del Makefile
es el peor caso de 3 nodos en total). Presupuesto GCP: **$300 de créditos** con
ventana de 90 días
y alertas de budget al 50/80/90/100/150 %. Modelo completo en
[COST.md](COST.md) §1; horas reales de sesión en
[`docs/evidence/cluster-hours.csv`](docs/evidence/cluster-hours.csv).

> **Nota de perfil (GKE ≥ 1.36):** el pool es `e2-medium` (no `e2-small`): los
> system pods consumen ~856m y el add-on CSI pide su propio espacio — con
> `e2-small` los pods quedan `Pending` con `Insufficient cpu` (observado en
> vivo). El chart **ya fija requests de 20m/pod por defecto** en los 5
> componentes (limits de 1 CPU intactos, ver
> [`helm/otel-lab/values.yaml`](helm/otel-lab/values.yaml)) — no hay que bajar
> nada; el `helm upgrade --install` con `--set …requests.cpu=20m` solo sirve
> como ejemplo si se quiere forzar el override desde otra base de values:
>
> ```powershell
> helm upgrade --install otel-lab helm/otel-lab --namespace otel-lab `
>   --set global.project=otel-lab-obs-2610 --set global.region=us-central1 `
>   --set collector.env.OTEL_TRACES_EXPORTER=googlecloud `
>   --set collector.resources.requests.cpu=20m --set serviceA.resources.requests.cpu=20m `
>   --set serviceB.resources.requests.cpu=20m --set postgres.resources.requests.cpu=20m
> ```

### Teardown (obligatorio)

`make gcp-destroy` (borra clúster + node pool + el LB si estaba activo, y
verifica) → registrar la línea `delete` en `docs/evidence/cluster-hours.csv`.
La infra permanente ($0 en reposo — VPC, AR, buckets, SA/IAM, secreto, budget)
no se toca. `make gcp-stop` **no** detiene el fee de plano: solo para pausas
dentro de la misma sesión de trabajo.

---

## Modo 4 — Dual-cloud (AWS + GCP simultáneo)

El punto fuerte del proyecto: el `trace_id` W3C **cruza la frontera de nubes**
cuando `service-a` corre en ECS (AWS) y llama al `service-b` de GKE expuesto
por su LoadBalancer regional. La aserción **A13** exige ver el MISMO `trace_id`
en una señal de AWS (X-Ray o CloudWatch Logs) **y** en una de GCP (Cloud Trace o
Cloud Logging). Orden ejecutado en vivo el 2026-10-12 y verificado:

### Prerrequisitos / toolchain

La **unión** de los modos 2 y 3: `aws` CLI + perfil, `gcloud` + ADC, `docker`,
`kubectl`, `helm`, `make`, `pwsh`, y `pip install boto3` (para `--include-aws`;
`--include-gcp` solo necesita la ADC de `gcloud`).

### Credenciales y variables de entorno

Como en los modos 2 y 3 (perfiles `~/.aws/` + ADC; SSM SecureString y Secret
Manager referenciados por nombre — sin secretos locales). Extra del modo dual:
`INVENTORY_URL` apunta al LB de GKE:

```powershell
make aws-start INVENTORY_URL=http://<LB_IP>:8001
```

### Pasos de despliegue (GCP primero — el LB debe existir antes de renderizar la task AWS)

1. **Lado GCP**:

   ```powershell
   make gcp-start      # fee $0.10/h corre desde el create
   # build/push de service-a/b a AR (bloque del Modo 3)
   make deploy-gcp     # helm install (ClusterIP por defecto)
   make gcp-expose     # UNICO LB del lab: service-b-lb regional (default OFF)
   kubectl -n otel-lab get svc service-b-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```

   Prerrequisito SSM (humano, Fase 0): `/otel-lab/database-url/service-a` =
   `.../orders` **y** `/otel-lab/database-url/service-b` = `.../inventory` (sin
   este segundo valor service-b da 500).

2. **Lado AWS mono** (cierra A1-A9 en la task):

   ```powershell
   make aws-push
   make aws-start      # INVENTORY_URL default http://localhost:8001 (a+b en la task)
   make aws-access-grant
   python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
     --include-aws --prometheus-url http://<IP_TASK>:8000/metrics
   ```

3. **Cruce dual** (cierra A13 con el MISMO trace en ambas nubes):

   ```powershell
   make aws-start INVENTORY_URL=http://<LB_IP>:8001   # service-a(ECS) -> service-b(GKE)
   python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
     --include-aws --include-gcp --prometheus-url http://<IP_TASK>:8000/metrics
   # (el target `make verify-correlation-dual` activa ambas banderas pero NO pasa
   #  --prometheus-url; para cerrar A8 en la corrida dual lanza el script a mano
   #  como arriba, con --prometheus-url apuntando al scrape app-level de la task)
   ```

4. **Corrida GCP dedicada** (cierra A10-A12 contra el cluster interno):

   ```powershell
   kubectl -n otel-lab port-forward svc/service-a 18080:8080
   python scripts/verify_correlation.py --endpoint http://localhost:18080 --include-gcp
   ```

En la corrida dual A3-A7/A10-A12 pueden degradar honestamente (service-b ya no
está en el path AWS); el cierre de fase es la **unión** de las corridas.

### Verificación / evidencia

- A13: mismo `trace_id` visible en X-Ray (por estructura — R-03) o CloudWatch
  Logs del exportador **y** en Cloud Trace/Cloud Logging.
- Evidencia final de la fase: [`docs/evidence/03-correlation.md`](docs/evidence/03-correlation.md)
  con sus JSONs [`03-correlation-aws.json`](docs/evidence/03-correlation-aws.json),
  [`03-correlation-gcp.json`](docs/evidence/03-correlation-gcp.json),
  [`03-correlation-dual.json`](docs/evidence/03-correlation-dual.json).

### Coste estimado

Sesión de 2 h ≈ **$0.35**: Fargate $0.049/h + fee GKE $0.10/h + nodo spot
e2-medium ~$0.012/h + LB regional (regla de reenvío sin cargo horario; se
factura por datos procesados, <$0.01 en el lab). Dentro de los créditos ($100
AWS / $300 GCP). El fee GKE corre desde el create hasta el delete: las pausas
largas se pagan (sesión del 2026-10-12: 14.2 h ≈ $1.6,
[`docs/evidence/cluster-hours.csv`](docs/evidence/cluster-hours.csv)).

### Teardown (obligatorio, orden inverso)

`make aws-access-revoke` (el SG vuelve a 0 ingress) → `make aws-stop` /
`make aws-destroy` → `make gcp-destroy` (el LB muere con el clúster) → registrar
las líneas de coste en los CSV de `docs/evidence/`. Verifica con
`make cost-guard` que no quedan recursos facturando.

---

## Secciones comunes

### Dashboards SLO (provisioning por código, nunca a mano)

Tres artefactos versionados en [`dashboards/`](dashboards), uno por backend:

| Archivo | Backend | Contenido |
|---|---|---|
| `grafana-slo.json` | Grafana OSS local (uid `otel-lab-slo`) | los 6 paneles leídos TODOS de Prometheus con los nombres REALES del scrape; variables `service`/`env` y data links de exemplar a Tempo (cloud) y Jaeger (local) |
| `cloudwatch-slo.json` | CloudWatch (AWS, nativo) | máx. 3 widgets sobre los únicos contadores que existen (EMF `OtelLab`); desviaciones documentadas en el `comment` del JSON |
| `cloud-monitoring-gcp.json` | Cloud Monitoring (GCP, nativo) | los 6 equivalentes GCP (Managed Prometheus/contadores), provisionado por su IaC |

**Local**: el provider de Grafana
(`deploy/grafana/provisioning/dashboards/dashboards.yml`) aplica el JSON en
calor al subir el stack. Verificación en vivo:
[`docs/evidence/05-dashboards-local.md`](docs/evidence/05-dashboards-local.md).

```powershell
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml up -d
# 30 s despues, el tablero ya esta servido:
curl -u admin:admin http://localhost:3000/api/dashboards/uid/otel-lab-slo
```

**AWS (mini-sesión, coste ~$0 — dashboard y alarmas no facturan cómputo)**:

```bash
make aws-dashboard   # aws cloudwatch put-dashboard --dashboard-name otel-lab-slo (idempotente)
make aws-alarms      # 2 put-metric-alarm desde dashboards/cloudwatch-alarms.json (idempotente)
```

Las alarmas (`otel-lab-error-ratio` > 5 %; `otel-lab-db-degradation` > 5
errores de DB/5 min) cablean acción SNS si pasas `SNS_TOPIC_ARN` (output
`alerts_topic_arn` de `infra/aws`). Grafana **nunca** consulta CloudWatch
(`GetMetricData` se cobra siempre): el dashboard de 6 paneles vive en Prometheus
y el de CloudWatch es el complemento nativo de 3 widgets.

### Benchmark de overhead

La campaña ABBA corre contra el stack **LOCAL** (compose) con k6 en Docker y
Prometheus local como instrumento; jaeger/grafana permanecen apagados. El coste
por nube se lee del ingest REAL de logs de la variante B (trazas OFF, decisión
C2), sin sesión de cómputo en ninguna nube.

```powershell
powershell -File scripts/benchmark.ps1 -Mode full -CloudLogs      # A/B (6 runs x ~7 min)
powershell -File scripts/benchmark.ps1 -Mode attrib -CloudLogs    # atribucion (2 runs)
powershell -File scripts/benchmark.ps1 -Mode quick                # smoke corto
powershell -File scripts/benchmark.ps1 -Mode cloudprobe           # probes de ingesta
python scripts/bench_report.py    # agrega -> k6/baseline.json, k6/otel.json y docs/evidence/04-overhead.md
```

- Variante A = `OTEL_ENABLED=false`; B = `true` con
  `OTEL_TRACES_SAMPLER=parentbased_traceidratio` arg 0.25 (1.0/0.0 para la
  atribución) y el pipeline de trazas del collector en sink `nop`
  ([`deploy/collector/otel-collector-bench.yaml`](deploy/collector/otel-collector-bench.yaml)).
- Artefactos crudos por run en `bench/`; el orquestador hace teardown completo al
  terminar o fallar.
- Limitaciones declaradas: [`docs/evidence/04-overhead.md`](docs/evidence/04-overhead.md).

### Imágenes (tags fijados, sin `:latest`)

| Servicio | Imagen |
|---|---|
| postgres | `postgres:16-alpine` |
| otel-collector | `otel/opentelemetry-collector-contrib:0.162.0` |
| collector ADOT (AWS) | `public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0` |
| jaeger | `jaegertracing/all-in-one:1.76.0` |
| prometheus | `prom/prometheus:v2.53.0` |
| grafana | `grafana/grafana:11.1.0` |
| k6 (benchmark) | `grafana/k6:2.0.0` |
| service-a / service-b (cloud) | ECR `otel-lab/service-*:$(IMAGE_TAG)` · AR `otel-lab/service-*:0.1.0` |

## Tabla de coste / créditos y guardarraíles por nube

Presupuesto: **$100 de créditos AWS + $300 de créditos GCP** (ventana de 90
días). Los modos nube operan **dentro de créditos con guardarraíles**, no
"$0/mes". Detalle completo: [COST.md](COST.md).

| Guardarráil | AWS | GCP |
|---|---|---|
| Techo de gasto | **$1/día**: budget diario creado por CLI en Fase 0 con alertas 80/100 % (evidencia `00-aws-baseline.md`); el IaC `aws_budget` (`infra/aws/budget.tf`) añade 100 % ACTUAL + 100 % FORECASTED vía SNS | Budget COP **$300** con umbrales **50/80/90/100/150 %** + alertas de cuota |
| Gasto diario | `make cost-guard` (`ce get-cost-and-usage` + barrido de recursos) | `make cost-guard` — el mismo script cubre GCP automáticamente si `gcloud` está en el PATH (VMs RUNNING y clusters GKE); sin gcloud, avisa y sigue |
| Horas de cómputo | `make budget-hours` / `budget-sync` → [`docs/evidence/instance-hours.csv`](docs/evidence/instance-hours.csv) (gate 150 h de task) | registro en [`docs/evidence/cluster-hours.csv`](docs/evidence/cluster-hours.csv) (fee $0.10/h desde create) |
| Recursos prohibidos | `make tf-plan-aws` → `scripts/guard_cost.py` (lista negra: ALB, NAT, VPC endpoints, EKS, RDS, Secrets Manager…) | `guard_cost.py --provider google` (Cloud NAT, Autopilot, LB gestionado, Cloud SQL, pools no-spot…) |
| Botón de parada | `make aws-stop` (desired_count=0) | `make gcp-stop` (pool a 0; **el fee de plano sigue**) |
| Botón de destrucción (DoD) | `make aws-destroy` + verificación | `make gcp-destroy` + verificación |
| Cómputo en sesión | Fargate 1 vCPU/2 GB ≈ $0.049/h | fee $0.10/h + spot e2-medium ≈ $0.012/h/nodo (autoscaling 0–3; peor caso 3 nodos ≈ $0.04/h total) |
| Fuera de sesión | ≈ $0 | ≈ $0 (solo si el cluster está **destruido**) |

**Decisión de billing (2026-10-11):** la cuenta del lab es **miembro** de una
organización AWS; el gasto se consolida en la cuenta pagadora, que posee el
pool real de créditos. Los budgets por-cuenta son proxy diario; el techo real
es el pool del pagador. El teardown obligatorio aplica igualmente.

## Troubleshooting

| Síntoma | Causa / solución |
|---|---|
| `make aws-verify` no resuelve la IP de la task | Con aws-cli ≥2.32/botocore ≥1.43 los `details` de los attachments salen en camelCase y el JMESPath `.Value` falla. Resuelve `Association.PublicIp` con `ec2 describe-network-interfaces` (o boto3) y lanza `verify_correlation.py` a mano. ([docs/aws-ecs.md](docs/aws-ecs.md) §2) |
| A8 (exemplars) FAIL en la corrida AWS | El scrape debe ir a la **app** (`:8000/metrics`, OpenMetrics), no al exporter del collector (`:8889`): los exemplars no se propagan por el collector. ([docs/aws-ecs.md](docs/aws-ecs.md) §7) |
| `Invalid target ID format` con `ssm start-session` | Esperado: SSM **no** soporta port-forwarding a tasks Fargate. El acceso es `make aws-access-grant` (regla /32 temporal). ([docs/aws-ecs.md](docs/aws-ecs.md) §5) |
| Pods GKE en `ContainerCreating` | Falta el add-on CSI Secret Manager en el clúster. `make gcp-start` lo crea con el cluster; sobre cluster existente, `gcloud container clusters update … --enable-secret-manager` (~40 min). |
| Pods GKE `Pending: Insufficient cpu` | GKE ≥ 1.36 no cabe en `e2-small` (system pods ~856m). El Makefile ya fija `e2-medium`; alternativa de requests en la nota de perfil del Modo 3. |
| service-b devuelve 500 en el cruce dual | Falta el parámetro SSM `/otel-lab/database-url/service-b` (debe apuntar a la DB `inventory`). |
| `409` al repetir el smoke | Stock ya reservado para ese SKU — esperado. El seed se rehace reiniciando postgres (o `docker compose down -v` en local). |
| Coste GCP inesperado con el pool a 0 | `gcp-stop` escala el nodo pero **no** detiene el fee de plano ($0.10/h). Destruye con `gcp-destroy` fuera de sesión. |
| `helm`/`kubectl` apuntan a otro clúster | `make gcp-start` reescribe el kubeconfig; si usas otro proyecto, pasa `GCP_PROJECT_ID=…` a todos los targets. |
| La task AWS no puede llamar al service-b de GKE | El LB debe existir ANTES de renderizar la task: orden dual = GCP primero, `make gcp-expose`, y luego `make aws-start INVENTORY_URL=http://<LB_IP>:8001`. |
| `terraform` falla en `init` | El backend S3/GCS requiere credenciales reales; el gate de PR usa `init -backend=false` (`make tf-validate-aws` / `tf-plan-aws`). Bootstrap con state: [infra/README.md](infra/README.md). |
| `make` no existe en Windows | Los comandos crudos equivalentes están en el propio [Makefile](Makefile) (legible); o usa `wsl`/`choco install make`. |

## Mapa de documentación del repo

| Documento | Qué cubre |
|---|---|
| [README.md](README.md) (este) | manual de ejecución de los 4 modos |
| [COST.md](COST.md) | modelo de coste dual-cloud, listas blanca/negra, guardarraíles, procedimiento de apagado |
| [docs/aws-ecs.md](docs/aws-ecs.md) | despliegue AWS/ECS Fargate en detalle: task def, contratos verificados, acceso, ADR de exemplars (A8) |
| [docs/evidence/](docs/evidence) | evidencia en vivo por fase: línea base de coste (00), WI binding (01), sesiones de despliegue (02), correlación (03), overhead (04), dashboards (05) |
| [deploy/aws/README.md](deploy/aws/README.md) | task definition, Dockerfile de postgres y políticas IAM (tokens y permisos) |
| [infra/README.md](infra/README.md) | Terraform dual-root: bootstrap exacto por nube, backends remotos, workspaces |
| [Makefile](Makefile) | ciclo de vida completo (targets `aws-*`, `gcp-*`, `verify-correlation*`, `tf-*`, `cost-*`, `budget-*`) |
| [.env.example](.env.example) | plantilla de variables sin secretos |
