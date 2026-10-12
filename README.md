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
Igual que en GKE: sin ALB. El acceso NO es por túnel SSM — SSM no soporta
port-forwarding a tasks Fargate (verificado en vivo 2026-10-12; ver
`docs/aws-ecs.md` §5): es una **regla ingress temporal /32** para la IP del
operador, con `make aws-access-grant` / `aws-access-revoke`.

### Requisitos

- `aws` CLI con perfil `otel-lab` (credenciales en `~/.aws/`, nunca en el repo),
  `docker` y `make`. Opcional para shell dentro de la task: `aws ecs
  execute-command` necesita el `session-manager-plugin` en el `PATH` (ECS Exec
  solo da shell, no reenvío de puertos).
- Creados **una sola vez por el humano** (este lab no tiene IAM de autómata):
  - Repos **ECR** `otel-lab/service-a`, `otel-lab/service-b`,
    `otel-lab/otel-collector` y `otel-lab/postgres` (tag INMUTABLE, mismo
    patrón; el de postgres alimenta `deploy/aws/postgres.Dockerfile`).
  - Roles **`otel-lab-exec-ecs`** (execution: ECR pull + logs) y
    **`otel-lab-task-ecs`** (task: X-Ray, CloudWatch y acceso SSM).
  - Parámetro **SSM** `/otel-lab/cloudwatch-agent/config` (config JSON del
    CloudWatch agent) y bucket S3 para el state (`scripts/guard_cost.py`).
  - Parámetros **SSM** de la DB: `/otel-lab/postgres/password`,
    `/otel-lab/database-url/service-a` =
    `postgresql://postgres:<pw>@localhost:5432/orders` y
    `/otel-lab/database-url/service-b` =
    `postgresql://postgres:<pw>@localhost:5432/inventory` (service-b usa la DB
    `inventory` que crea `init-scripts/01-init.sh`).
- Imagen pública fijada del collector ADOT:
  `public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0` (tag fijo).

### Ciclo de vida

| # | Comando | Qué hace |
|---|---|---|
| 1 | `make validate-collector` | valida offline el YAML ADOT del modo AWS (arranca el binario localmente, sin coste) |
| 2 | `make aws-push` | login ECR + build/push de `service-a`, `service-b`, el **collector ADOT** con el YAML AWS horneado y **postgres con el seed horneado** (tag `$(IMAGE_TAG)`, nunca `:latest`) |
| 3 | `make aws-start` | red (VPC + subnet pública + IGW, sin NAT) + cluster + task definition (render del template) + service FARGATE `desired_count=1` con `assignPublicIp` efímero y ECS Exec |
| 4 | `make aws-access-grant` | regla ingress **temporal TCP /32** (IP pública del operador, autodetectada) para 8000/8889 en el SG; imprime la IP pública del ENI de la task |
| 4b | `make aws-verify` | corre `verify_correlation.py --include-aws` contra la IP pública del ENI de la task (`:8000` + `--prometheus-url :8889/metrics`) |
| 4c | `make aws-access-revoke` | revoca la regla temporal; el SG vuelve a **0 ingress** (steady-state) |
| 5 | `make aws-stop` | `desired_count=0`: la task Fargate deja de facturar (idempotente) |
| 6 | `make aws-destroy` | botón nuclear: borra service + cluster y **verifica** que no queda nada activo |
| 7 | `make aws-net-destroy` | borra la red tagueada (VPC, subnet, IGW, route table, SG) y comprueba que no quedan dependencias |

Como no hay ALB, el "backend" equivalente de cada servicio es la **IP pública
del ENI de la task** (visible tras `make aws-access-grant`; `ecs describe-tasks`
no la lista: sale en `ec2 describe-network-interfaces →
Association.PublicIp`), comparando con la tabla de backends de GKE:

| Ruta en la consola de AWS | Backend (CLI) |
|---|---|
| CloudWatch Logs → ECS | `aws logs tail /otel/labs/ecs-platform/<contenedor>` (grupos `service-a`, `service-b`, `postgres`, `otel-collector`, `cloudwatch-agent`) |
| X-Ray → Traces | trazas `service-a`/`service-b` correlacionadas por `trace_id` |
| CloudWatch → Métricas (EMF + agent) | dashboard **OTel Lab — AWS ECS** creado en la Fase 5 |
| ECS Exec → consola del contenedor | `aws ecs execute-command --cluster otel-lab --task <taskId> --container service-a --command "/bin/sh" --interactive` (solo shell; el reenvío de puertos por SSM **no existe** en Fargate, §`docs/aws-ecs.md`) |

### Smoke en AWS

```powershell
make aws-access-grant   # abre /32 del operador + imprime la IP publica de la task

# en otra terminal — el mismo smoke que local/GKE (sustituye <IP_TASK>):
Invoke-RestMethod -Method Post -Uri http://<IP_TASK>:8000/orders `
  -ContentType 'application/json' `
  -Body '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'

make aws-access-revoke  # teardown del acceso al terminar la sesion

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
| 1 | `make gcp-start` | VPC + subnet (private Google Access), clúster zonal `otel-lab-std` (`us-central1-a`), nodo spot `e2-medium` con autoscaling 0–3 y **add-on Secret Manager CSI en el create** (`--enable-secret-manager`), Workload Identity y **binding del SA del collector incluido**, kubeconfig |
| 2 | `make deploy-gcp` | sincroniza la config del collector y el seed al chart, `helm upgrade --install` y espera el rollout |
| 3 | `make port-gcp` | port-forward autenticado, sin ingress público: `svc/service-a 8080:8080` + collector `4317`/`8889` |
| 4 | `make gcp-destroy` | borra el clúster y verifica que ya no aparece en la lista |

### Prerrequisitos del clúster

1. **Add-on CSI Secret Manager.** El chart monta `pg-password` con el driver
   `secrets-store-gke.csi.k8s.io` (add-on de GKE). **`make gcp-start` ya lo
   crea con el clúster** (`--enable-secret-manager`, rondando desde la ronda 2
   de la sesión viva 2026-10-12: sin el driver los pods quedan en
   `ContainerCreating`). Si reutilizas un clúster existente sin el add-on,
   aplícalo antes de `helm`:

   ```powershell
   gcloud container clusters update otel-lab-std --location=us-central1-a --enable-secret-manager
   ```

   Sobre cluster vivo esto tarda (~40 min, hace `UPGRADE_NODES`) — por eso va
   en el create.
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

`make gcp-start` crea el pool en **`e2-medium` spot con autoscaling 0–3**: el
perfil original `e2-small` (0.5 CPU compartida) no cabe — los system pods de
GKE 1.36 consumen ~856m de CPU, el add-on CSI pide su propio espacio y los pods
de la app se quedan `Pending` con `Insufficient cpu` (observado en vivo;
decisión registrada también en `COST.md` y en el Terraform, que ya usaba
`e2-medium`). El autoscaler sube hasta 3 nodos spot si el add-on + workloads
no caben en uno y vuelve a 0 en idle; el fee de mgmt ($0.10/h) no depende del
perfil. Alternativa si quieres apurar un perfil menor: bajar los requests a 20m
en los 4 componentes, sustituyendo el `make deploy-gcp` por su `helm`
equivalente:

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
completo de la sesión (el que se ejecutó en vivo el 2026-10-12; todo lo demás
queda probado offline):

1. **GCP primero** (el LB debe existir antes de renderizar la task AWS):
   ```powershell
   make gcp-start   # cluster zonal spot: pool e2-medium autoscaling 0-3 + add-on
                    # CSI Secret Manager EN EL CREATE (fee $0.10/h desde el create)
   # build/push de service-a/b a Artifact Registry (tabla de arriba)
   make deploy-gcp                         # helm install (ClusterIP por defecto)
   make gcp-expose                         # UNICO LB del lab: service-b-lb regional
   kubectl -n otel-lab get svc service-b-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```
   Prerequisito SSM (humano, Fase 0): `/otel-lab/database-url/service-a` =
   `.../orders` y `/otel-lab/database-url/service-b` = `.../inventory` (sin este
   segundo valor service-b da 500 — bug 3 de la ronda 2).
2. **AWS mono** (pipeline completo en la task, cierra A1-A9):
   ```powershell
   make aws-push    # 4 imagenes, incluida otel-lab/postgres con seed horneado
   make aws-start   # INVENTORY_URL default http://localhost:8001 (a+b en la task)
   make aws-access-grant   # regla /32 temporal 8000+8889; imprime <IP_TASK> del ENI
                           # (si el CLI no la imprime, resolver Association.PublicIp
                           # con boto3 — ver docs/aws-ecs.md §2 nota camelCase)
    python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
      --include-aws --prometheus-url http://<IP_TASK>:8000/metrics
    #   (A8 exige el scrape APP-LEVEL :8000 — los exemplars solo existen en la
    #    exposicion OpenMetrics de la app; el exporter prom del collector :8889
    #    no los propaga, ver docs/aws-ecs.md §7)
    ```
3. **Cruce dual** (cierra A13 con el MISMO trace en ambas nubes):
   ```powershell
   make aws-start INVENTORY_URL=http://<LB_IP>:8001   # update-service: service-a(ECS)->service-b(GKE)
    python scripts/verify_correlation.py --endpoint http://<IP_TASK>:8000 `
      --include-aws --include-gcp --prometheus-url http://<IP_TASK>:8000/metrics
   ```
   A13 exige ambas banderas: el MISMO `trace_id` inyectado debe aparecer en una
   señal AWS (X-Ray por estructura — R-03 — o CloudWatch Logs del exportador
   `/otel/labs/ecs-cluster/*`) **y** en una GCP (Cloud Trace o Cloud Logging).
   En la corrida dual A3-A7/A10-A12 pueden degradar honestamente (service-b ya
   no está en el path AWS); el cierre de fase es la **unión** de las corridas.
4. **Corrida GCP dedicada** (cierra A10-A12 contra el cluster interno):
   ```powershell
   kubectl -n otel-lab port-forward svc/service-a 18080:8080
   python scripts/verify_correlation.py --endpoint http://localhost:18080 --include-gcp
   ```
5. **Revoke + teardown en orden inverso** (DoD §3.5): `make aws-access-revoke`
   (el SG vuelve a 0 ingress) → `make aws-stop`/`aws-destroy` → `make gcp-destroy`
   (el LB muere con el cluster). Coste de la sesión ≈ Fargate ($0.049/h) + fee
   GKE ($0.10/h) + nodo spot e2-medium (~$0.012/h) + LB regional (regla de
   reenvío sin cargo horario; se factura por datos procesados, <$0.01 en el
   lab): una sesión de 2 h queda en ~$0.35 dentro de los créditos. **Ojo:** el
   fee GKE corre desde el create hasta el delete — las pausas largas se pagan
   (`gcp-stop` escala nodos a 0 pero NO detiene el fee del plano; ver
   `docs/evidence/cluster-hours.csv`, sesión del 2026-10-12: 14.2 h ≈ $1.6).

La evidencia final de la fase es la **unión** de las tres corridas (mono AWS,
mono GCP, dual) documentada en `docs/evidence/03-correlation.md`, con sus JSONs
(`03-correlation-aws.json`, `03-correlation-gcp.json`, `03-correlation-dual.json`).
