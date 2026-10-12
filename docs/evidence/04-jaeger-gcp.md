# 04 — Jaeger UI en GKE (waterfall cross-service por trace_id)

> **Fecha:** 2026-10-11T22:49:37Z · **Proyecto:** `otel-lab-obs-2610` · **Cluster:** `otel-lab-std` (zonal `us-central1-c`) · **UI:** `svc/jaeger-ui-on-gcp:16686` (port-forward)

## Qué se valida

El Collector desplegado en GKE hace **fan-out** de la señal de trazas: además del
exporter `googlecloud` (Cloud Trace), envía las mismas trazas por OTLP/HTTP a un
Jaeger *all-in-one* dentro del cluster (`otlphttp/jaeger-gcp` →
`http://jaeger-ui-on-gcp:4318`). Así se obtiene un **waterfall** navegable en la
UI de Jaeger y se comprueba la propagación de contexto W3C entre los dos
microservicios y su acceso a base de datos.

## Topología de la sesión

| Componente | Imagen / detalle |
|---|---|
| Cluster | `otel-lab-std`, zonal `us-central1-c`, e2-medium **spot**, `--enable-secret-manager` |
| Namespace | `otel-lab` |
| Collector | `otelcol-contrib 0.162.0` (Helm release `otel-lab`) |
| Jaeger | `jaegertracing/all-in-one:1.76.0` (Deployment + Service **ClusterIP** `jaeger-ui-on-gcp`) |
| Workload Identity | binding `otel-lab-obs-2610.svc.id.goog[otel-lab/otel-lab-collector]` |
| Autoscaling | node pool 0-2 (resize a **2 nodos** por `Insufficient cpu` con 1 solo nodo) |

## Comando de captura

```
kubectl -n otel-lab port-forward svc/service-a 8080:8080
# 5x POST /orders  {items:[{sku:LAPTOP-001, quantity:2, unit_price:2500000}]}
kubectl -n otel-lab port-forward svc/jaeger-ui-on-gcp 16686:16686
GET http://localhost:16686/api/services
GET http://localhost:16686/api/traces?service=service-a&limit=1
```

## Evidencia: servicios registrados

```json
{"data":["jaeger-all-in-one","service-a","service-b"],"total":3}
```

## Evidencia: waterfall de una traza real

- **trace_id:** `fcd4631a76d0ef0e8626f84ca0bbba85`
- **spans:** 23 · **duración raíz (`service-a POST /orders`):** 250245 µs (~250 ms)

| Servicio | Operación | Duración | Relación |
|---|---|---|---|
| service-a | `POST /orders` | 250.2 ms | span **raíz** (entrada HTTP) |
| service-a | `order.process` | 130.4 ms | hijo de la raíz |
| service-a | `order.validate` | 18 µs | hijo de `order.process` |
| service-a | `inventory.reserve` | 75.1 ms | hijo de `order.process` (cliente HTTP) |
| service-b | `POST /inventory/reserve` | 57.5 ms | hijo de `inventory.reserve` (**salto de servicio**) |
| service-b | `inventory.query` | 25.0 ms | hijo de `POST /inventory/reserve` |
| service-b | `SELECT inventory` | 732 µs | hijo de `inventory.query` (**DB**) |
| service-b | `UPDATE inventory` | 448 µs | hijo de `inventory.query` (**DB**) |
| service-b | `payment.gateway_call` | 30.4 ms | hijo de `POST /inventory/reserve` |
| service-a | `payment.authorize` | 50.4 ms | hijo de `order.process` |
| service-a | `order.persist` | 4.1 ms | hijo de `order.process` |
| service-a | `INSERT orders` | 548 µs | hijo de `order.persist` (**DB**) |

La cadena **`service-a POST /orders` → `inventory.reserve` → `service-b
POST /inventory/reserve` → `SELECT/UPDATE inventory` → `order.persist` →
`INSERT orders`** confirma la propagación de contexto W3C entre servicios y el
mismo `trace_id` en todos los spans (`parent` encadenado, sin huérfanos).

## Coste

Ver `cluster-hours.csv` (filas *sesion C*): uptime 13m18s = **0.22 h** ×
$0.10/h (fee plano de control de GKE) ≈ **$0.02 USD**; el nodo spot no se
incluye en esa línea. Teardown completado (`clusters list` = vacío).
