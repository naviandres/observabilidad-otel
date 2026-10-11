# 01 — Workload Identity binding (evidencia dura heredada de #21, tomada en #22)

> **Fecha de auditoría:** 2026-10-11 · **Proyecto:** `otel-lab-obs-2610` (número `797970839119`) ·
> **Operador CLI:** `luismiguelossaarias05@gmail.com` (gcloud SDK 580.0.0, ADC OK).
> **Alcance:** evidencia dura del binding Workload Identity que habilita la autenticación de los
> pods del lab (KSA → GCP SA) sin llaves de servicio. Heredada de #21 (PLAN §2.B-GCP): el deploy
> del smoke funcionó con exporters autenticados (binding ok *de facto*), pero quedaba pendiente
> la captura del `get-iam-policy` como evidencia dura (nota de `progress/impl_21b_gke_deploy.md`,
> línea "WI binding del KSA … re-verificar con get-iam-policy en #22"). Formato tipo
> `00-free-tier-baseline-gcp.md`: volcado CLI + fecha, sin capturas.

## 1. Volcado CLI

**Comando:**

```powershell
gcloud iam service-accounts get-iam-policy `
  otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com --project=otel-lab-obs-2610
```

**Salida (2026-10-11):**

```yaml
bindings:
- members:
  - serviceAccount:otel-lab-obs-2610.svc.id.goog[otel-lab/default]
  - serviceAccount:otel-lab-obs-2610.svc.id.goog[otel-lab/otel-lab-collector]
  role: roles/iam.workloadIdentityUser
etag: BwZdiI2_fBM=
version: 1
```

## 2. Lectura

- El GCP SA **`otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com`** (creado en #20 con
  6 roles least-privilege) tiene el rol `roles/iam.workloadIdentityUser` concedido a **dos KSA
  del namespace `otel-lab`**:
  - `otel-lab/otel-lab-collector` — el Collector GCP-distro (exporters `googlecloud` +
    `googlemanagedprometheus` autenticados vía WI; verificado en el smoke #21:
    `otelcol_exporter_sent_spans{googlecloud}=262`, `send_failed_spans=0`).
  - `otel-lab/default` — el KSA default del namespace (los pods de servicio corren con él;
    en el diseño actual las apps **no** llaman a APIs de GCP directamente — hablan OTLP con el
    collector — por lo que este binding es de conveniencia del pool por defecto).
- La anotación del lado Kubernetes (`iam.gke.io/gcp-service-account` en el KSA) la gestiona el
  chart Helm (`helm/otel-lab/templates/serviceaccount.yaml`, verificada por
  `tests/test_collector_gcp_config.py::test_helm_values_pinned_and_secret_free`).

## 3. Estado del cluster y vigencia

- El cluster `otel-lab-std` **ya fue teardown** (~14:16Z 2026-10-11, DoD de coste §3.5 del PLAN:
  no dejar cómputo fuera de sesión; coste de sesión #21 ≈ $1.04 + spot).
- **El binding IAM sobrevive al cluster**: es un recurso del proyecto, no del cluster. Al
  redesplegar GKE en #23/#24 (o `make gcp-start`), la parte que hay que re-verificar es la
  **anotación del KSA sobre el cluster nuevo** (`kubectl get sa otel-lab-collector -n otel-lab
  -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}'`), no este binding.
- Zero coste: `gcloud iam` y las políticas WI no se cobran (lista blanca PLAN §3.3 #13).
