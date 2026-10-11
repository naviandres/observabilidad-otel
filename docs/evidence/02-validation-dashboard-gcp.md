# 02 — Validación del dashboard de Cloud Monitoring (smoke + fault injection + auditoría de series GMP)

> **Fecha:** 2026-10-11 · **Proyecto:** `otel-lab-obs-2610` (número `797970839119`) ·
> **Operador CLI:** `luismiguelossaarias05@gmail.com` (gcloud SDK 580.0.0, ADC OK) ·
> **Base de la prueba:** commit `8e12f0c` del entregable (dashboard Cloud Monitoring + WI evidencia).
> **Alcance:** evidencia de la validación humana, tras la revisión, del dashboard
> `8ceea108-ca30-4d79-b18a-f4610bfc8f71`: se valida **en vivo** con carga de
> tráfico real, inyección de fallo y auditoría de series en Google Managed Prometheus (GMP).
> Formato tipo `00`/`01`: volcado CLI + datos declarados por el operador, sin capturas de pantalla.
> Coste de la sesión: filas `create/delete/summary` de 16:56Z–17:41Z en `cluster-hours.csv`
> (~0.75 h de fee plano ≈ $0.08–0.12 USD + spot, no incluido en las líneas de fee).

## 1. Topología de la sesión (clusters A y C)

Intento **A** (`make gcp-start`: zonal `us-central1-a`, spot e2-small, autoscaling 0-1) creado a
las **16:56Z**. A los ~20 min se detecta que los **system pods de GKE 1.36 consumen ~856m de CPU**
en e2-small (2 vCPU) y no dejan espacio para la carga con los requests por defecto del chart
(100m × 4 componentes) → **teardown inmediato** (regla de coste PLAN §3.5) a las **17:16Z**.

Intento de upgrade en la misma zona, **A2** (`e2-medium` en `us-central1-a`):

```powershell
gcloud --project otel-lab-obs-2610 container clusters create otel-lab-std `
  --location=us-central1-a --machine-type=e2-medium --num-nodes=1 --spot ...
# ERROR: does not have enough resources to fulfill the request...
# (capacidad spot agotada en el intento; el create quedó en ERROR y se borró)
```

Cluster definitivo **C** — zonal `us-central1-c`, **spot e2-medium** (4 vCPU, suficiente para
system pods + carga). El `make gcp-start` fija `--machine-type=e2-small` como literal (no es
variable `?=`), por lo que el create de C se hizo con el comando equivalente del Makefile,
mismos flags, cambiando zona y máquina:

```powershell
gcloud --project otel-lab-obs-2610 container clusters create otel-lab-std `
  --location=us-central1-c --release-channel=regular `
  --machine-type=e2-medium --num-nodes=1 --spot `
  --enable-autoscaling --min-nodes=0 --max-nodes=1 `
  --network=otel-lab --subnetwork=otel-lab-subnet `
  --workload-pool=otel-lab-obs-2610.svc.id.goog --no-enable-basic-auth --no-enable-legacy-authorization
gcloud container clusters get-credentials otel-lab-std --location=us-central1-c
# cluster C RUNNING ~17:2xZ

make deploy-gcp   # sincroniza collector/seed al chart + helm upgrade --install, MÁS:
helm upgrade --install otel-lab helm/otel-lab --namespace otel-lab `
  --set global.project=otel-lab-obs-2610 --set global.region=us-central1 `
  --set collector.env.OTEL_TRACES_EXPORTER=googlecloud `
  --set collector.resources.requests.cpu=20m --set serviceA.resources.requests.cpu=20m `
  --set serviceB.resources.requests.cpu=20m --set postgres.resources.requests.cpu=20m
```

**Salida (declarada):** 4/4 pods `Running` en el nodo e2-medium (`kubectl -n otel-lab get pods`).
El binding WI (`01-wi-binding-gcp.md`) es un recurso del proyecto y **no hubo que recrearlo**;
el cluster C hereda el mismo `otel-lab-deploy` SA.

## 2. Smoke 12×200 (`POST /orders`)

SKUs sembrados por `init-scripts/02-inventory-db.sql`: `LAPTOP-001` (10), `MOUSE-001` (20),
`KEYBOARD-001` (15). Payload según `service-a/app/models.py` (`{items:[{sku,quantity,unit_price}]}`).

```powershell
kubectl -n otel-lab port-forward svc/service-a 18080:8080
$body = '{"items":[{"sku":"LAPTOP-001","quantity":1,"unit_price":2500000}]}'
$r = 1..12 | ForEach-Object {
  (Invoke-WebRequest -Method Post -Uri http://localhost:18080/orders `
    -ContentType "application/json" -Body $body).StatusCode
}
```

**Datos declarados:**

| Métrica | Valor |
|---|---|
| Resultado | **12× HTTP 200** |
| `trace_id` representativo | `db021295291dfc56c4387c9e6a74083e` (cadena service-a → service-b → postgres) |
| Latencia media e2e | **205 ms** |

## 3. Señales en la self-telemetry del collector (scrape interno :8889)

```powershell
kubectl -n otel-lab get --raw "/api/v1/namespaces/otel-lab/pods/otel-lab-collector-<hash>:8889/proxy/metrics" |
  Select-String "otelcol_exporter_sent|otelcol_exporter_send_failed|otelcol_receiver_refused"
```

**Datos declarados (sesión de validación, cluster C):**

| Contador | Valor | Lectura |
|---|---|---|
| `otelcol_exporter_sent_spans` | **583** | export a Cloud Trace vía `googlecloud` |
| `otelcol_exporter_send_failed_spans` | **0** | sin fallos |
| `otelcol_exporter_sent_metric_points` | **2576** | suma `googlecloud` + `googlemanagedprometheus` |
| `otelcol_exporter_sent_log_records` | **130** | pipeline OTLP + `filelog` → Cloud Logging |

**Verificación del fix `1cab22c` en clúster:** en el smoke del despliegue GKE los `filelog` operators eran
inválidos para contrib 0.162.0 y el pipeline de logs **no emitió registros (sent_log_records=0)**.
Tras el fix (`parse_to: attributes` en `json_parser`; copia del chart re-sincronizada), el binario
`otel/opentelemetry-collector-contrib:0.162.0` desplegado en el cluster C emite **130 registros**
→ fix confirmado operativo en clúster, no solo en el YAML.

## 4. Fault injection (service-b → 0) y error rate del panel 2

```powershell
kubectl -n otel-lab scale deployment/service-b --replicas=0   # inyección
# 6× POST /orders -> 503 (service-a no alcanza a service-b)
kubectl -n otel-lab scale deployment/service-b --replicas=1   # recuperación
# 8× POST /orders -> 200
```

**Dato declarado:** el **panel 2 del dashboard** (error rate 5xx, umbral SLO 0,5%) marca
**0.27** en la ventana de la inyección — verificado consultando la misma query
PromQL del panel contra la API Prometheus de GMP (no por la consola):

```powershell
$tok = gcloud auth print-access-token
Invoke-RestMethod -Headers @{Authorization="Bearer $tok"} -Uri (
  "https://monitoring.googleapis.com/v1/projects/otel-lab-obs-2610/location/global/prometheus" +
  "/api/v1/query?query=" + [uri]::EscapeDataString(
    '(sum(rate(http_server_duration_milliseconds_count{http_status_code=~"5.."}[5m])) or vector(0)) / sum(rate(http_server_duration_milliseconds_count[5m]))')
)
# value declarado: 0.27 (ratio de 5xx en la ventana de 5m que cubre la inyeccion)
```

## 5. Auditoría de series en GMP (fuente de los 6 paneles)

```powershell
# inventario de nombres de serie del proyecto vía API Prometheus de GMP
Invoke-RestMethod -Headers @{Authorization="Bearer $tok"} -Uri (
  "https://monitoring.googleapis.com/v1/projects/otel-lab-obs-2610/location/global/prometheus/api/v1/label/__name__/values")
```

**Hallazgos declarados:**

| Serie | Estado | Impacto en dashboard |
|---|---|---|
| `http_server_duration_milliseconds` (tipo `/histogram`) | **SÍ existe**, con datos de la sesión | paneles 1–3 en vivo (rps, error rate, latencia avg) |
| `db_client_operation_duration_milliseconds_*` | **NO existe** | **desvío conocido del panel 4** (persiste desde la validación del dashboard: solo llega `db_client_connections_usage`; la query de duración queda preparada para revalidarse en las próximas sesiones del cluster) |
| `otelcol_*_total` (sent/refused) | **SÍ presentes** | panel 6 con datos |
| `otelcol_exporter_send_failed_*` | **sin descriptor** | esperado: contadores con valor 0 no se exportan = healthy (limitación 4 documentada en el propio dashboard) |

## 6. Teardown y estado final

```powershell
make gcp-destroy   # gcloud container clusters delete + verificacion lista
# Salida declarada: Deleted [projects/.../zones/us-central1-c/clusters/otel-lab-std];
#                   "cluster destruido y verificado (fee detenido)"
gcloud container clusters list   # -> vacio
```

- Clúster C teardown **17:41Z**; no queda cómputo corriendo (DoD §3.5). El dashboard
  (`8ceea108-...`) y las series GMP sobreviven al teardown (recursos del proyecto).
- Coste de la sesión registrado en `cluster-hours.csv` (filas 16:56Z–17:41Z): fee plano
  ~0.75 h × $0.10 ≈ **$0.08** (subtotal declarado de la fila summary; total sesión con el
  cluster A ≈ **$0.12**); nodo spot aparte, no incluido en las líneas de fee.

## 7. Desviaciones de la sesión (declaradas)

1. **Capacidad spot en `us-central1-a`:** un intento de create quedó en **ERROR por capacidad
   spot agotada** (zona A); la sesión migró a `us-central1-c`.
2. **e2-small insuficiente en GKE 1.36:** los system pods (~856m CPU) no dejan espacio para la
   carga con requests por defecto → cluster A efímero (~20 min, teardown inmediato) y cluster C
   en **e2-medium** con `--set *.resources.requests.cpu=20m` sobre los 4 componentes.
3. Ninguna de las dos toca el diseño del dashboard: los 6 paneles se validaron con los datos
   reales de esta sesión (panel 2 = 0.27 bajo fallo; paneles 1/3/5/6 en vivo; panel 4 con el
   desvío conocido de `db_client_operation_duration`).
