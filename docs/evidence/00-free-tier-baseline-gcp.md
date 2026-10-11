# 00 — Línea base free tier / créditos GCP (Fase 0, B.1 — PLAN tarea 0.10)

> **Fecha de auditoría:** 2026-10-11 · **Proyecto:** `otel-lab-obs-2610` (número `797970839119`) ·
> **Billing account:** `013D56-6929B9-3AC57A` (billingEnabled: `true`) ·
> **Operador CLI:** `luismiguelossaarias05@gmail.com` (gcloud SDK 580.0.0, ADC OK, project/region `us-central1` en config).
> **Créditos:** $300 USD, ventana de 90 días; **quedan 31 días, expira ≈ 2026-11-11** —
> *dato declarado por el operador el 2026-10-11 (sin captura — enmienda 2026-10-11; no hay API
> listable de saldo/expiración)*.
> **Alcance:** re-verificación de los importes marcados con ⚠ en PLAN §3.1 (tarea 0.10) y
> estado de recursos de Fase 0. Fuentes: salidas CLI de `gcloud` (reproducibles, §2 y §3) y
> documentación oficial de Google Cloud citada con URL (consultada 2026-10-11).
> **Regla anti-invento:** todo número sin fuente CLI o URL verificable está marcado como
> "dato declarado por el operador" (sin capturas — enmienda 2026-10-11).

---

## 1. Lista blanca GCP (PLAN §3.3, L218) — cobertura free tier por recurso

| # | Recurso | Cobertura créditos / free tier | Límite conocido | Estado en el proyecto | Verificación |
|---|---|---|---|---|---|
| 1 | VPC | $0: tráfico interno intra-región y egress intra-Google sin cargo; ingress gratis | Sin cargo por la red en sí ([vpc/pricing](https://cloud.google.com/vpc/pricing): "No charge" en tablas de data transfer interno e inbound) | **pendiente fase 2 (#21)** — existe solo la red `default` auto-creada | CLI: `gcloud compute networks list` → solo `default` |
| 2 | Subnet | $0 (subnet normal; solo las *Hybrid Subnets* se cobran) | $0 por subnet estándar; Hybrid $0.315/h ([vpc/pricing](https://cloud.google.com/vpc/pricing)) | pendiente #21 | CLI: `gcloud compute networks subnets list` (tras #21) |
| 3 | Firewall (sin ingress público) | $0: las reglas básicas de firewall VPC no aparecen tarifadas (Cloud NGFW es producto aparte) | Sin cargo listado para reglas básicas ([vpc/pricing](https://cloud.google.com/vpc/pricing), sección "Firewall rules" → remite a NGFW) | pendiente #21 | CLI: `gcloud compute firewall-rules list` (tras #21) |
| 4 | Private Google Access | $0 según PLAN §3.1 L169; la página de pricing VPC no lista cargo para PGA | dato declarado: no tarifado explícitamente en la doc consultada (sin captura — enmienda 2026-10-11) | pendiente #21 (flag de subnet) | volcado CLI tras #21 (`gcloud compute networks subnets describe`) / plan TF de #21 |
| 5 | GKE Standard (node pool spot e2-small) | Fee de gestión **$0.10/h/cluster** ([gke/pricing](https://cloud.google.com/kubernetes-engine/pricing)); free tier **$74.40/mes por billing account = 1 cluster zonal Standard gratis** (solo el fee, no los nodos) | 744 h/mes cubiertas por free tier; nodos spot e2-small **se pagan de créditos** | **pendiente #21** — 0 clusters | CLI: `gcloud container clusters list` → "Listed 0 items." |
| 6 | Workload Identity | Sin cargo (función de GKE/IAM; no aparece tarifada en gke/pricing ni en free-cloud-features) | n/a | **pendiente #21** (WI del cluster); WIF de GitHub **aplazado a CI (B.8)** | n/a hoy; tras #21: `gcloud container clusters describe --show-workload-identity-config` |
| 7 | Artifact Registry | Free tier **0.5 GiB/mes**; luego **$0.10/GiB·mes** ($0.000136986/GiB·h) ([artifact-registry/pricing](https://cloud.google.com/artifact-registry/pricing), [free-cloud-features](https://cloud.google.com/free/docs/free-cloud-features)) | 0.5 GiB/mes por billing account; egress intra-región $0 | **creado** (repo `otel-lab`, 0.000 MB) | CLI: `gcloud artifacts repositories describe otel-lab --location=us-central1 --format=yaml` |
| 8 | GCS (evidencia + state) | Free tier **5 GB·mes Standard en regiones US** (us-central1 aplica) + 5 000 ops Clase A + 50 000 Clase B ([free-cloud-features](https://cloud.google.com/free/docs/free-cloud-features)) | Excedente ≈$0.02/GB·mes (PLAN §3.1; confirmar en [storage/pricing](https://cloud.google.com/storage/pricing)) | **creados 2 buckets**; evidence con lifecycle 7 d; **tfstate SIN lifecycle (desviación, §3.3)** | CLI: `gcloud storage buckets describe … --raw` (×2) |
| 9 | Secret Manager (2 secretos previstos) | Free tier **6 versiones activas/mes + 10 000 accesos/mes**; luego **$0.06/versión·mes** y **$0.03/10 000 accesos** ([secret-manager/pricing](https://cloud.google.com/secret-manager/pricing)) | 2 secretos × 1 versión ≪ free tier → coste ≈ $0 | **1 de 2 creado** (`pg-password`); token Grafana **aplazado** con decisión local-only (current.md 2026-10-11) | CLI: `gcloud secrets list` |
| 10 | Cloud Trace | Free allotment **2,5 M spans/mes por billing account**; luego **$0.20/M spans** ([observability/pricing](https://cloud.google.com/products/observability/pricing)) | Cuotas del proyecto por CLI: ingestión **3 000 000 spans/día**, writes 4 800/min, reads 300/min (§2) | API habilitada; ingestión real **pendiente fase 2-3** (#22/#24). Diseño C2: export a Cloud Trace **apagado en benchmark** | CLI: `gcloud quotas info list --service=cloudtrace.googleapis.com …` (§2) |
| 11 | Cloud Monitoring (+ Managed Prometheus) | Free: **todas las métricas de Google Cloud no tarifadas**; **150 MiB/mes** de métricas tarifadas por billing account; **1 M time series de lectura/mes** por billing account; uptime checks 1 M ejecuciones/proyecto. **Managed Prometheus: sin allotment gratis, $0.06/M samples** ([observability/pricing](https://cloud.google.com/products/observability/pricing)) | Cuotas CLI: ingestión 30 000 req/min, queries 6 000/min, custom descriptors 10 000, dashboards 1 000 (§2) | API habilitada; dashboards **pendiente fase 4-5 (#25)** | CLI: `gcloud quotas info list --service=monitoring.googleapis.com …` (§2) |
| 12 | Cloud Logging (dentro de cuota) | Free: **primeros 50 GiB/proyecto/mes**; luego **$0.50/GiB**; retención >30 d **$0.01/GiB·mes**; logs en bucket `_Required` sin cargo ([observability/pricing](https://cloud.google.com/products/observability/pricing), [free-cloud-features](https://cloud.google.com/free/docs/free-cloud-features)) | Cuota de tasa CLI: write_bytes **4,8 GB/min en us-central1** (rate, no free tier); reads 60/min; sinks 200; buckets 100 (§2). Retención por diseño C3: 3 d | API habilitada; ingestión **pendiente fase 2-3**. Volumen estimado del ciclo ABBA ≈450 MB ≪ 50 GiB (PLAN §3.4 C3) | CLI: `gcloud quotas info list --service=logging.googleapis.com …` (§2) |
| 13 | IAM / Service Accounts | Sin cargo (IAM no tiene pricing; no aparece en free tier) | n/a | **creada** SA `otel-lab-deploy@…` con **6 roles** least-privilege (get-iam-policy) | CLI: `gcloud iam service-accounts list` + `gcloud projects get-iam-policy` (§3.4) |
| 14 | GCP Budgets + Quota Alerts | Budgets: medición + alerta, **sin cargo** (el budget no cobra; current.md 2026-10-11). Quota alerts: alert policies sobre métricas Billing/Quota/Uptime **no tarifadas** (el cobro de alertas empieza 2027-09-01, [observability/pricing](https://cloud.google.com/products/observability/pricing)) | umbrales por budget: 50/80/90/100/150 % | **budget creado y corregido** (§3.6); **quota alerts aún NO activadas** (B.2 paso 2, pendiente consola) | CLI: `gcloud billing budgets describe …` (§3.6) |

**Conclusión de cobertura:** ningún recurso de la lista blanca queda fuera de créditos/free tier
en el volumen del lab. Los únicos consumos reales esperados de los $300 son: **nodos spot e2-small**
(fee del cluster lo absorbe el free tier GKE si ≤1 cluster zonal), **ingestión de logs/trazas/métricas**
dentro de allotments, y **egress mínimo** (ver discrepancia D2 abajo).

---

## 2. Cuotas del proyecto (datos REALES vía CLI)

**Comando usado** (el sugerido en el encargo `gcloud quotas list` **no existe** en gcloud 580;
el grupo válido es `gcloud quotas info list` y `--service` es obligatorio, una llamada por servicio;
el filtro OR multi-servicio no aplica — los valores están en `dimensionsInfos[].details.value`):

```powershell
gcloud quotas info list --service=cloudtrace.googleapis.com --project=otel-lab-obs-2610 `
  --format='table(metric, dimensionsInfos.details.value, refreshInterval, metricDisplayName)'
# repetir con: logging.googleapis.com, monitoring.googleapis.com
```

### 2.1 Cloud Trace (`cloudtrace.googleapis.com`)

| Métrica | Límite | Ventana |
|---|---|---|
| read_config_requests | 100 | minuto |
| read_requests | 300 | minuto |
| write_config_requests | 100 | minuto |
| write_requests | 4 800 | minuto |
| quota/ingested_spans | **3 000 000** | **día** |

### 2.2 Cloud Logging (`logging.googleapis.com`)

| Métrica | Límite | Ventana |
|---|---|---|
| write_bytes (Bytes ingested) | 4 800 000 000 B/min (≈4,8 GB/min) × 11 regiones (incl. us-central1); 300 000 000 B/min (≈300 MB/min) resto | minuto |
| read_requests (entries.list) | 60 | minuto |
| control_requests | 600 | minuto |
| daily_control_requests | 1 000 | día |
| log_buckets_count / log_sinks_count / log_scopes_count / log_metrics_count | 100 / 200 / 100 / 500 | global |
| tail_sessions_count | 10 | global |

> Nota: `write_bytes` es un **rate de ingestión**, no la cuota gratuita mensual. El allotment
> free (50 GiB/proyecto/mes) es de facturación y **no es listable por CLI → dato declarado con
> fecha + URL de free-cloud-features como fuente** (sin captura — enmienda 2026-10-11; el
> consumo por SKU puede consultarse en Billing→Reports si se necesita puntualmente).

### 2.3 Cloud Monitoring (`monitoring.googleapis.com`)

| Métrica | Límite | Ventana |
|---|---|---|
| ingestion_requests (Time series ingestion) | 30 000 | minuto |
| service_ingestion_requests | 240 000 | minuto |
| query_requests (Time series queries) | 6 000 | minuto |
| default_requests (Total requests) | 9 223 372 036 854 775 807 (sin efecto) | minuto |
| customMetricDescriptors/count | 10 000 | global |
| prometheusMetricDescriptors/count | 25 000 | global |
| workloadMetricDescriptors/count | 25 000 | global |
| dashboards/count · widgets/count | 1 000 · 100 | global |
| alertPolicies/count · activeConditions | 2 000 · 80 000 | global |
| uptimeChecks/count | 100 | global |
| monitoredProjects/count | 375 | global |

(20 quota-infos en total; tabla resumida con las relevantes al diseño. Listado completo
reproducible con el comando de arriba.)

---

## 3. Estado de recursos Fase 0 (capturas CLI del 2026-10-11)

### 3.1 APIs habilitadas

`gcloud services list --enabled` devuelve **38** APIs. Las **9 del runbook B.3 (+billingbudgets)**
están todas presentes: `container`, `cloudtrace`, `logging`, `monitoring`, `artifactregistry`,
`secretmanager`, `storage`, `iam`, `billingbudgets`. Las ~29 restantes son **APIs de gestión
habilitadas por dependencia** (p. ej. `compute`, `pubsub`, `storage-component`, el clúster BigQuery
de Analytics, etc.) — GCP las enciende automáticamente; no se conceden permisos extra al habilitarlas.
La distinción "explícita vs dependencia" no es exportable por CLI (campo no publicado); la lista
completa es reproducible con `gcloud services list --enabled --format 'value(config.name)'`.

### 3.2 Artifact Registry

`gcloud artifacts repositories describe otel-lab --location=us-central1 --format=yaml`:
- format DOCKER, mode STANDARD_REPOSITORY, ubicación us-central1, tamaño **0.000 MB**.
- cleanupPolicies: `delete-old` (action DELETE, olderThan 604800 s = 7 d, tagState ANY) +
  `keep-minimum-versions` (action KEEP, mostRecentVersions.keepCount 5). ✓ coincide con B.5.
- createTime 2026-10-11T01:00:12Z (UTC). Vulnerability scanning: `SCANNING_DISABLED`
  (API `containerscanning.googleapis.com` no habilitada — decisión de coste, fuera de lista blanca).

### 3.3 Buckets GCS (`gcloud storage buckets describe … --raw`)

| Bucket | lifecycle | softDelete | location/class | creado (UTC) |
|---|---|---|---|---|
| `gs://otel-lab-evidence-otel-lab-obs-2610` | **rule Delete age 7 d** ✓ | 604 800 s (7 d, default GCS) | US-CENTRAL1 / STANDARD | 2026-10-11T01:02:36Z |
| `gs://otel-lab-tfstate-otel-lab-obs-2610` | **sin lifecycle** (desviación deliberada) | 604 800 s (7 d, default GCS) | US-CENTRAL1 / STANDARD | 2026-10-11T01:02:40Z |

> **Desviación anotada (tfstate sin lifecycle):** el runbook B.6 aplicaba Delete a 7 d también al
> bucket de state; borrar el state de Terraform a los 7 días dejaría el backend huérfano. Decisión
> registrada en `progress/current.md` (2026-10-11): el tfstate **no** recibe lifecycle y se destruye
> manualmente en el teardown final. El `softDeletePolicy` de 7 d es el default de GCS (recuperación
> accidental) y **no** sustituye a la regla lifecycle.

### 3.4 Service accounts e IAM

`gcloud iam service-accounts list`: 2 SAs — la `Compute Engine default service account`
(auto-creada por la API de compute) y **`otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com`**.

`gcloud projects get-iam-policy otel-lab-obs-2610` filtrada a la SA → **6 bindings**:
`roles/artifactregistry.writer`, `roles/cloudtrace.agent`, `roles/container.developer`,
`roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/secretmanager.secretAccessor`. ✓ least privilege B.4 (sin `container.admin`, sin `storage.objectAdmin` aún — se restringirá a buckets en #21).

### 3.5 Secret Manager

`gcloud secrets list` → **1 secreto: `pg-password`** (creado 2026-10-11T01:16:23, replicación
`automatic`; el valor vive solo en SM, nunca en repo/chat). El 2º secreto previsto (token Grafana)
queda **aplazado** por la decisión hub-local (current.md 2026-10-11, sección C cerrada local-only).

### 3.6 Budget

`gcloud billing budgets describe 6260de7a-d122-476a-bc6d-2c8e03aa086e --billing-account=013D56-6929B9-3AC57A`:

```yaml
displayName: $300 Alerta de presupuesto mensual
amount: { specifiedAmount: { currencyCode: COP, units: '964461' } }   # ≈ USD 300 al cambio conservador del día
budgetFilter: { calendarPeriod: MONTH, creditTypesTreatment: EXCLUDE_ALL_CREDITS }
thresholdRules: [ CURRENT_SPEND 0.5, 0.8, 0.9, 1.0, 1.5 ]
notificationsRule: {}        # -> emails por defecto a los admins del billing account
```

- `EXCLUDE_ALL_CREDITS` = el budget mide **gasto bruto** (el consumo de créditos dispara las alertas;
  con `INCLUDE_ALL_CREDITS` nunca habrían sonado). Corrección ejecutada 2026-10-11 vía API (current.md).
- **Pendiente:** quota alerts al 80 % en Trace/Logging/Monitoring (B.2 paso 2) — no creadas aún;
  ruta en §4.

### 3.7 Cómputo y red (gate "sin computo creado" de la sección D del runbook)

- `gcloud container clusters list` → **0 clusters** ✓ (el cómputo GCP se crea en #21).
- `gcloud compute networks list` → solo `default` (auto-creada); la VPC del diseño, pendiente #21.
- `gcloud billing projects describe otel-lab-obs-2610` → `billingEnabled: true`, billing account
  `013D56-6929B9-3AC57A` vinculado ✓.

---

## 4. Evidencia de estado (sin capturas — enmienda 2026-10-11)

Sustituye a las antiguas "capturas pendientes del humano" (`credits-gcp.png`, `budget-gcp.png`,
`quotas-gcp.png`), eliminadas por decisión humana 2026-10-11 (ver PLAN, Fase 0):

| Dato | Evidencia adoptada |
|---|---|
| Créditos: saldo $300 USD, 31 días restantes al 2026-10-11, expira ~2026-11-11 | **Dato declarado por el operador** (2026-10-11) — no hay API listable de saldo/expiración |
| Budget "6260de7a…" con **EXCLUDE_ALL_CREDITS**, moneda COP 964 461, umbrales 50/80/90/100/150 % | **Volcado CLI §3.6** (ya documentado arriba) |
| Cuotas de Cloud Trace / Cloud Logging / Cloud Monitoring del proyecto | **Volcado CLI §2** (ya documentado arriba) |
| Quota alerts al 80 % (B.2 paso 2) | **Pendiente**: no creadas al 2026-10-11 (§3.6); al crearlas, registrar como dato declarado con fecha — gcloud 580 no publica estado de quota alerts por CLI |

---

## 5. Discrepancias detectadas vs PLAN §3.1 (para actualización del PLAN)

| # | PLAN §3.1 decía | Verificado 2026-10-11 | Impacto |
|---|---|---|---|
| D1 | GKE Standard: fee $0.10/h "se paga de créditos" | Free tier GKE: **$74.40/mes por billing account = fee de 1 cluster zonal Standard gratis** ([gke/pricing](https://cloud.google.com/kubernetes-engine/pricing)) | El fee NO debería consumir créditos mientras haya ≤1 cluster zonal; confirmar en volcado CLI §3.6/reports |
| D2 | Network egress "≈200 GB/mes gratis a internet (premium tier)" | [vpc/pricing](https://cloud.google.com/vpc/pricing): **Premium Tier solo 1 GiB gratis por destino**; el free de 200 GiB es del **Standard Tier** (y "Always Free limits do not apply" a Standard) | El egress OTLP a backends de Google en us-central1 es $0 (transfer intra-Google); el riesgo real es egress a internet (Grafana Cloud aplazada) → coste ≈0 |
| D3 | Secret Manager "$0.05/secreto·mes + $0.40/10k accesos" | [secret-manager/pricing](https://cloud.google.com/secret-manager/pricing): **$0.06/versión·mes** (tras 6 gratis) y **$0.03/10k accesos** (tras 10k gratis) | Más barato de lo supuesto; céntimos incluso con rotación |
| D4 | Cloud Trace "cuota mensual de ingestión (re-verificar)" | **2,5 M spans/mes gratis por billing account** + $0.20/M spans ([observability/pricing](https://cloud.google.com/products/observability/pricing)); cuota del proyecto 3 M spans/**día** (CLI) | Con C2 (export apagado en benchmark) el consumo queda ≪ allotment |
| D5 | Cloud Logging "≈50 GB (re-verificar)" | **50 GiB/proyecto/mes** confirmado ([free-cloud-features](https://cloud.google.com/free/docs/free-cloud-features)) | C3 (~450 MB/ciclo) con margen ~100× |
| D6 | Cloud Monitoring "cuota gratuita de series y lecturas" | **150 MiB de métricas tarifadas + 1 M time series de lectura por billing account**; Managed Prometheus **sin allotment** ($0.06/M samples) | El `googlemanagedprometheus` del diseño consume samples de pago → mantener cardinalidad baja (C1/C5) |

---

## 6. Trazabilidad

- Comandos y salidas de §2–§3 ejecutados el 2026-10-11 desde la máquina del operador
  (gcloud 580.0.0, cuenta `luismiguelossaarias05@gmail.com`, project `otel-lab-obs-2610`).
- Docs citadas (consultadas 2026-10-11): [free](https://cloud.google.com/free),
  [free-cloud-features](https://cloud.google.com/free/docs/free-cloud-features),
  [observability/pricing](https://cloud.google.com/products/observability/pricing),
  [logging/quotas](https://cloud.google.com/logging/quotas),
  [gke/pricing](https://cloud.google.com/kubernetes-engine/pricing),
  [artifact-registry/pricing](https://cloud.google.com/artifact-registry/pricing),
  [secret-manager/pricing](https://cloud.google.com/secret-manager/pricing),
  [vpc/pricing](https://cloud.google.com/vpc/pricing).
- Informe de implementación: `progress/impl_evidence_b1_gcp.md` (harness).
