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
