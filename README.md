# observabilidad-otel

Pipeline de observabilidad end-to-end con OpenTelemetry: `service-a` → `service-b`
→ PostgreSQL, con OTel Collector, Jaeger, Prometheus y Grafana en local ($0).

## Requisitos

- Docker + Docker Compose v2.
- Opcional (desarrollo sin Docker): Python 3.12 y `pip install -r requirements.txt`.

## Arranque rápido (stack completo)

Desde esta carpeta:

```powershell
# levanta los 7 servicios (postgres, collector, jaeger, prometheus, grafana, service-a, service-b)
docker compose up --build -d

# comprueba el estado
docker compose ps
```

El esquema de base de datos **se inicializa solo** con un volumen nuevo:
`init-scripts/01-orders-schema.sql` crea `orders`/`order_items` (DB `orders`) y
`init-scripts/02-inventory-db.sql` crea y siembra la DB `inventory`.

### Smoke test

```powershell
# 1) crear una orden (200)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'

# 2) métricas OTel expuestas por el Collector
curl.exe -s http://localhost:8889/metrics | Select-String http_server

# 3) log JSON con trace_id
docker compose logs service-a | Select-String trace_id

# 4) UIs
#   Jaeger     http://localhost:16686
#   Prometheus http://localhost:9090
#   Grafana    http://localhost:3000   (admin/admin; datasources y dashboards provisionados por código)
```

### Parar

```powershell
docker compose down          # conserva el volumen postgres_data
docker compose down -v       # borra el volumen (reinicia el esquema desde init-scripts)
```

## Arranque por capas (PLAN 1.A)

```powershell
# núcleo: postgres + collector + service-a + service-b
docker compose -f deploy/docker-compose.yml up -d
# observabilidad: jaeger + prometheus + grafana
docker compose -f deploy/docker-compose.observability.yml up -d
```

Ambos ficheros viven en `deploy/`, comparten proyecto y red por defecto, y se
resuelven por nombre de servicio (`otel-collector:4317`, `prometheus:9090`,
`jaeger:16686`).

## Imágenes (tags fijados, sin `:latest`)

| Servicio | Imagen |
|---|---|
| postgres | `postgres:16-alpine` |
| otel-collector | `otel/opentelemetry-collector-contrib:0.162.0` |
| jaeger | `jaegertracing/all-in-one:1.76.0` |
| prometheus | `prom/prometheus:v2.53.0` |
| grafana | `grafana/grafana:11.1.0` |

## Ejecución sin Docker (desarrollo)

```powershell
pip install -r requirements.txt
uvicorn app.main:app --app-dir service-a --host 0.0.0.0 --port 8000
uvicorn app.main:app --app-dir service-b --host 0.0.0.0 --port 8001
```

Docs de API: <http://localhost:8000/docs>. Métricas de proceso:
<http://localhost:8000/metrics> y <http://localhost:8001/metrics>.

## Despliegue en AWS (ECS Fargate)

La misma aplicación sobre **ECS Fargate** con el **Collector ADOT**: las 4
señales van a X-Ray (trazas), CloudWatch Logs (logs), CloudWatch EMF
(métricas) y un **CloudWatch Agent** sidecar (métricas de infraestructura ECS).
Igual que en GKE: sin ALB, sin ingress público — el acceso es por **túnel SSM
(`ECS Exec`)** al puerto localhost del contenedor.

### Requisitos

- `aws` CLI con perfil `otel-lab` (credenciales en `~/.aws/`, nunca en el repo),
  `docker`, `make` y el `session-manager-plugin` (AWS SSM Session Manager → ECS
  Exec, necesario para `make tunnel`).
- Creados **una sola vez por el humano** (este lab no tiene IAM de autómata):
  - Repos **ECR** `otel-lab/service-a`, `otel-lab/service-b` y
    `otel-lab/otel-collector`.
  - Roles **`otel-lab-exec-ecs`** (execution: ECR pull + logs) y
    **`otel-lab-task-ecs`** (task: X-Ray, CloudWatch y acceso SSM).
  - Parámetro **SSM** `/otel-lab/cloudwatch-agent/config` (config JSON del
    CloudWatch agent) y bucket S3 para el state (`scripts/guard_cost.py`).
- Imagen pública fijada del collector ADOT:
  `public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0` (tag fijo).

### Ciclo de vida

| # | Comando | Qué hace |
|---|---|---|
| 1 | `make validate-collector` | valida offline el YAML ADOT del modo AWS (arranca el binario localmente, sin coste) |
| 2 | `make aws-push` | login ECR + build/push de `service-a`, `service-b` y el **collector ADOT** con el YAML AWS horneado (tag `$(IMAGE_TAG)`, nunca `:latest`) |
| 3 | `make aws-start` | red (VPC + subnet pública + IGW, sin NAT) + cluster + task definition (render del template) + service FARGATE `desired_count=1` con `assignPublicIp` efímero y ECS Exec |
| 4 | `make tunnel` | port-forwarding **sin ingress público**: `service-a:8000`, `service-b:8001`, collector `4317` (vía debug) |
| 5 | `make aws-stop` | `desired_count=0`: la task Fargate deja de facturar (idempotente) |
| 6 | `make aws-destroy` | botón nuclear: borra service + cluster y **verifica** que no queda nada activo |
| 7 | `make aws-net-destroy` | borra la red tagueada (VPC, subnet, IGW, route table, SG) y comprueba que no quedan dependencias |

Como no hay ALB, el "backend" equivalente de cada servicio es **local** al
túnel (comparar con la tabla de backends de GKE):

| Ruta en la consola de AWS | Backend (CLI) |
|---|---|
| CloudWatch Logs → ECS | `aws logs tail /otel/labs/ecs-platform/<contenedor>` (grupos `service-a`, `service-b`, `postgres`, `otel-collector`, `cloudwatch-agent`) |
| X-Ray → Traces | trazas `service-a`/`service-b` correlacionadas por `trace_id` |
| CloudWatch → Métricas (EMF + agent) | dashboard **OTel Lab — AWS ECS** creado en la Fase 5 |
| ECS Exec → consola del contenedor | `make tunnel` + navegador local (http://localhost:8000 ← service-a) |

### Smoke en AWS

```powershell
make tunnel

# en otra terminal — el mismo smoke que local/GKE
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'
```

`200` = orden creada. Dónde ver las señales:

| Señal | Dónde |
|---|---|
| Trazas | **X-Ray** → consola (buscar el `trace_id` del log de la orden) |
| Logs | **CloudWatch Logs** → grupos `/otel/labs/ecs-platform/*` |
| Métricas | **CloudWatch** → el agente emite métricas ECS (`CpuUtilized`, `MemoryUtilized`…) con EMF del collector |

### Coste y teardown

Fargate factura por segundo mientras `desired_count=1`. **`make aws-stop`** lo
apaga sin borrar nada (vuelve a encenderse con `aws-start`, la red y la task
definition persisten). El botón nuclear es **`make aws-destroy`** + opcional
`make aws-net-destroy`. `scripts/guard_cost.py` vigila el coste acumulado del
proyecto (presupuesto $100 de AWS, PLAN §3.5) con el state en S3.

## Despliegue en GCP (GKE)

Modo nube del mismo pipeline sobre **GKE Standard** (node pool spot):
`service-a` → `service-b` → PostgreSQL con el Collector OTel exportando a
Cloud Trace, Cloud Logging y Cloud Monitoring (Managed Prometheus). El ciclo
de vida lo gobierna el `Makefile` de esta carpeta; solo cambian el cómputo y
el YAML del collector — la aplicación es idéntica al modo local.

### Requisitos

- `gcloud` con ADC con acceso a tu proyecto (`gcloud auth login` y
  `gcloud config set project <proyecto>`), `docker`, `kubectl` y `helm`.
- API de GKE habilitada: `gcloud services enable container.googleapis.com`.
- En el proyecto, creados una sola vez:
  - Repositorio **Artifact Registry** `otel-lab` (docker) en `us-central1`.
  - Secreto **Secret Manager** `pg-password` con la contraseña de PostgreSQL.
- `make` en el PATH. En Windows sin `make`, los comandos crudos equivalentes
  están en el propio `Makefile`, que es legible.

El `Makefile` y el chart fijan por defecto el proyecto `otel-lab-obs-2610`;
para usar otro, pasa `GCP_PROJECT_ID=<proyecto>` a los targets `make` y
ajusta los `image.repository` de `service-a`/`service-b` en
`helm/otel-lab/values.yaml` (o por `--set` en el deploy).

### Build & push de imágenes (Artifact Registry)

Las imágenes de app viven en AR; el Collector y postgres usan imágenes
públicas fijadas por el chart. Desde esta carpeta:

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev

docker build -t us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-a:0.1.0 service-a
docker build -t us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-b:0.1.0 service-b
docker push us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-a:0.1.0
docker push us-central1-docker.pkg.dev/otel-lab-obs-2610/otel-lab/service-b:0.1.0
```

El tag `0.1.0` es el que fija `values.yaml` (sin `:latest`).

### Ciclo de vida

| # | Comando | Qué hace |
|---|---|---|
| 1 | `make gcp-start` | VPC + subnet (private Google Access), clúster zonal `otel-lab-std` (`us-central1-a`), 1 nodo spot `e2-small` con autoscaling 0–1, Workload Identity y **binding del SA del collector incluido**, kubeconfig |
| 2 | `make deploy-gcp` | sincroniza la config del collector y el seed al chart, `helm upgrade --install` y espera el rollout |
| 3 | `make port-gcp` | port-forward autenticado, sin ingress público: `svc/service-a 8080:8080` + collector `4317`/`8889` |
| 4 | `make gcp-destroy` | borra el clúster y verifica que ya no aparece en la lista |

### Prerrequisitos del clúster (fuera del `Makefile`)

1. **Add-on CSI Secret Manager.** El chart monta `pg-password` con el driver
   `secrets-store-gke.csi.k8s.io` (add-on de GKE). Habilítalo en el create con
   `--enable-secret-manager` o sobre un clúster existente:

   ```powershell
   gcloud container clusters update otel-lab-std --location=us-central1-a --enable-secret-manager
   ```

   Tarda (hace `UPGRADE_NODES`); sin el driver los pods quedan en
   `ContainerCreating`.
2. **Workload Identity collector→SA.** Ya viene en `make gcp-start`
   (binding del KSA `otel-lab-collector` con el SA de GCP); no hay que
   repetir nada.

### Smoke en GKE

```powershell
kubectl -n otel-lab port-forward svc/service-a 18080:8080

# en otra terminal — SKUs sembrados: LAPTOP-001, MOUSE-001, KEYBOARD-001
Invoke-RestMethod -Method Post -Uri http://localhost:18080/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'
```

`200` = orden creada (repetir el mismo SKU puede dar `409`: stock ya
reservado; el seed se rehace al reiniciar postgres). Dónde ver las 3 señales:

| Señal | Dónde |
|---|---|
| Trazas | **Cloud Trace** → busca el `trace_id` que sale en el log de la orden |
| Logs | **Cloud Logging** → logs de `service-a`, campo `jsonPayload.trace_id` |
| Métricas | **Cloud Monitoring** → dashboard **OTel Lab — SLIs GCP (Managed Prometheus)** (6 paneles; se crea una vez con `gcloud monitoring dashboards create --config-from-file dashboards/cloud-monitoring-gcp.json`) |

### Coste y teardown

GKE Standard cobra una **fee de gestión por hora** mientras el clúster
existe, además del nodo. Levanta el clúster solo para trabajar y
**destrúyelo al terminar** (`make gcp-destroy`); `make gcp-stop` escala el
nodo a 0, pero la fee del clúster sigue corriendo.

### Nota de perfil (CPU con GKE ≥ 1.36)

El pool `e2-small` del `Makefile` puede quedarse corto: los system pods de
GKE 1.36 consumen ~856m de CPU y los pods de la app quedan `Pending` con
`Insufficient cpu`. Dos salidas:

- crear el clúster con `--machine-type=e2-medium` (comando crudo del
  `Makefile`, mismos flags, otra máquina), o
- bajar los requests a 20m en los 4 componentes, sustituyendo el
  `make deploy-gcp` por su `helm` equivalente:

```powershell
helm upgrade --install otel-lab helm/otel-lab --namespace otel-lab `
  --set global.project=otel-lab-obs-2610 --set global.region=us-central1 `
  --set collector.env.OTEL_TRACES_EXPORTER=googlecloud `
  --set collector.resources.requests.cpu=20m --set serviceA.resources.requests.cpu=20m `
  --set serviceB.resources.requests.cpu=20m --set postgres.resources.requests.cpu=20m
```

## Sesión dual-cloud (correlación cruzada de nubes, aserción 13 de PLAN §3.A)

El `trace_id` W3C cruza la frontera AWS↔GCP cuando **service-a corre en ECS y
llama al service-b de GKE** expuesto por su LoadBalancer regional. Orden
completo de la sesión (todo lo demás queda probado offline):

1. **GCP primero** (el LB debe existir antes de renderizar la task AWS):
   ```powershell
   make gcp-start                          # cluster zonal spot (fee $0.10/h)
   # build/push de service-a/b a Artifact Registry (tabla de arriba)
   make deploy-gcp                         # helm install (ClusterIP por defecto)
   make gcp-expose                         # UNICO LB del lab: service-b-lb regional
   kubectl -n otel-lab get svc service-b-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```
2. **AWS con la URL cruzada** (el token `__INVENTORY_URL__` de la task def):
   ```powershell
   make aws-push
   make aws-start INVENTORY_URL=http://<LB_IP>:8001   # service-a(ECS) -> service-b(GKE)
   ```
3. **Túneles AWS** (dos terminales o el modo Task de una sola vez):
   ```powershell
   pwsh -File scripts/tunnel.ps1 -Mode Task -TaskPorts 8000,8889
   # 8000 = service-a (endpoint de inyeccion) · 8889 = scrape Prometheus del collector
   ```
4. **Verificación dual** (boto3 profile `otel-lab` + ADC `gcloud` en la máquina):
   ```powershell
   make verify-correlation-dual VERIFY_ENDPOINT=http://localhost:8000
   # equivalente a mano:
   python scripts/verify_correlation.py --endpoint http://localhost:8000 `
     --include-aws --include-gcp
   ```
   A13 exige ambas banderas: el MISMO `trace_id` inyectado debe aparecer en una
   señal AWS (X-Ray por estructura — R-03 — o CloudWatch Logs del exportador
   `/otel/labs/ecs-cluster/*`) **y** en una GCP (Cloud Trace o Cloud Logging).
5. **Teardown en orden inverso** (DoD §3.5): `make aws-stop`/`aws-destroy` →
   `make gcp-destroy` (el LB muere con el cluster). Coste de la sesión ≈
   Fargate ($0.05/h) + fee GKE ($0.10/h) + LB regional ($0.025/h) + egress:
   una sesión de 2 h queda en ~$0.40 dentro de los créditos.

Para cerrar **A3–A9** (pipeline AWS completo: ambos servicios en la task) se
hace una corrida adicional con el default `INVENTORY_URL=http://localhost:8001`
y solo `--include-aws`; las aserciones GCP (A10–A12) ya se cerraron con la
corrida `--include-gcp` contra GKE interno. La evidencia final es la union de
las tres corridas documentada en `docs/evidence/03-correlation.md`.
