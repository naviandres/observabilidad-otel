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
