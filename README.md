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

## Arranque por capas (PLAN §1.A)

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

## Coste y política de sesiones (PLAN §3.5)

**Regla operativa:** el ciclo de desarrollo diario ocurre en **modo local**
(`docker compose up`, coste $0). La sesión de nube —task Fargate en AWS, cluster
GKE en GCP— se **enciende para trabajar y se destruye al terminar**: el teardown y
la verificación de gasto diario ≈ $0 tras el destroy son parte del DoD de cada fase
de nube. Los créditos ($100 AWS / $300 GCP) cubren sesiones de ~5 h/día en ambas
nubes dentro de la ventana del lab.

### Ciclo start / stop / destroy (previsto en Fase 2)

Los targets del `Makefile` **aún no existen**: aterrizan con el despliegue (AWS:
features #5 y #9; GCP: #21 y #24). La política se fija ya, antes que el cómputo:

| Nube | Comando | Efecto |
|---|---|---|
| AWS | `make aws-start` | `terraform apply` con `desired_count=1` del service Fargate |
| AWS | `make aws-stop` | `terraform apply` con `desired_count=0` — botón de parada al terminar la sesión |
| AWS | `make aws-destroy` | `terraform destroy` del root AWS + verificación de gasto diario ≈ 0 |
| GCP | `make gcp-start` | Cluster **GKE Standard** + node pool **spot `e2-small`** (región `us-central1`) |
| GCP | `make gcp-stop` | Node pool escalado a 0. ⚠ El fee de gestión del cluster ($0.10/h) sigue corriendo: gobernarlo con `gcp-destroy` |
| GCP | `make gcp-destroy` | `terraform -chdir=infra/gcp destroy` + borrado del cluster + verificación de gasto ≈ 0 |

### Guardarraíles de coste (PLAN §3.5)

- **AWS Budget**: umbral diario de **$1** con alertas 80 %/100 % + Cost Anomaly
  Detection; tope mensual dentro de los $100 de crédito.
- **GCP Budget**: alertas al 50 %/80 %/100 % de los $300 de crédito + alertas de
  cuota (Cloud Trace / Cloud Logging / Cloud Monitoring).
- **Registro de horas de cómputo**: `docs/evidence/instance-hours.csv` (horas de
  task Fargate) y `cluster-hours.csv` (node pool + fee GKE), vía
  `scripts/instance_hours.ps1` y `cost_guard.ps1 --provider google` (previstos con
  #9/#24). Un cluster o una task vivos fuera de sesión cuentan como falla del
  guardarraíl, no como descuido opcional.
- **Lista negra por nube**: `scripts/guard_cost.py` falla si el plan Terraform de
  cualquier root contiene un recurso vetado (PLAN §3.2: sin ALB/NAT/EIP/EKS/RDS/
  Secrets Manager en AWS; sin Cloud NAT/GKE Autopilot/Cloud LB/Cloud SQL ni node
  pools no-spot en GCP).
- **Higiene de datos**: retención de logs 3 días en ambas nubes, lifecycle 7 días
  en S3/GCS, keep-last-5 en ECR/Artifact Registry.

### Estado actual de la política

Fase 0 en curso. **GCP:** budget corregido vía API el 2026-10-11 —
`creditTypesTreatment=EXCLUDE_ALL_CREDITS` (mide el gasto bruto, i.e. el consumo de
créditos) con umbral 80 % añadido; umbrales finales 50/80/90/100/150 %. Facturado en
**COP 964.461 (≈ USD 240)** porque el billing account factura en COP y no admite USD;
el valor es deliberadamente conservador (la alerta del 100 % suena *antes* de agotar
los $300). **AWS:** sigue en *sandbox mode* (SCP que bloquea ecs/ecr/xray) hasta que se
propague el método de pago; presupuesto *free-tier* de $100 con alertas 50/80/100 ya
activo (verificado por CLI el 2026-10-10). Ninguna condición cambia la regla de sesiones.

> El ciclo de despliegue paso a paso (local, ECS Fargate, GKE) está documentado en
> `docs/deployment.md` del harness (ruta `../../docs/deployment.md`, fuera de este
> repo entregable).
