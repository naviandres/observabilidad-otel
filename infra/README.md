# infra/ — Terraform dual-cloud

Dos roots independientes (nunca uno único para las dos nubes):

- `infra/aws/` — provider `aws`, state en S3 con locking nativo. **Aún no
  implementado**: llega con la pata AWS de la fase 5; este README documenta hoy
  la pata GCP y se actualizará con el bootstrap exacto de AWS cuando exista.
- `infra/gcp/` — providers `google` + `google-beta` (solo budget) + `helm`,
  state en GCS con locking nativo (object versioning del bucket de state).

---

## infra/gcp — bootstrap exacto (verificado 2026-10-11)

### Requisitos

- Docker con la imagen `hashicorp/terraform:1.9.8` (no hace falta binario local).
- ADC de `gcloud` válida Y con quota project fijado (billingbudgets lo exige):

```powershell
gcloud auth application-default set-quota-project otel-lab-obs-2610
```

### Patrón de ejecución

Desde `D:\Projects\lab_obs` (rutas de mount en forma unix para Docker Desktop):

```powershell
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -e TF_VAR_gcp_project=otel-lab-obs-2610 `
  -e TF_VAR_gcp_region=us-central1 `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 <comando>
```

`gcp_project` y `gcp_region` se pasan SIEMPRE por `-var`/`TF_VAR_*` (sin
default a propósito; ver la nota de riesgo de desvío de región en el plan del
proyecto).

### Workspaces

```powershell
... terraform workspace new lab    # ya creado
... terraform workspace new bench  # ya creado
... terraform workspace select lab
```

- `lab` (el activo): todo el inventario, incluido budget + dashboard.
- `bench`: mismos recursos SIN budget ni dashboard (los recursos llevan
  `count = terraform.workspace == "lab" ? 1 : 0`). Los umbrales de coste no
  deben contaminar las mediciones del benchmark.

### Import del inventario existente (1:1, ya ejecutado)

El estado vive en `gs://otel-lab-tfstate-otel-lab-obs-2610/lab/<workspace>/terraform.tfstate`.
Direcciones e IDs usados (reproducen el bootstrap desde cero si el state se pierde):

```text
google_compute_network.otel_lab
  projects/otel-lab-obs-2610/global/networks/otel-lab
google_compute_subnetwork.otel_lab_subnet
  projects/otel-lab-obs-2610/regions/us-central1/subnetworks/otel-lab-subnet
google_artifact_registry_repository.otel_lab
  projects/otel-lab-obs-2610/locations/us-central1/repositories/otel-lab
google_storage_bucket.evidence
  otel-lab-evidence-otel-lab-obs-2610
google_storage_bucket.tfstate
  otel-lab-tfstate-otel-lab-obs-2610
google_service_account.otel_lab_deploy
  projects/otel-lab-obs-2610/serviceAccounts/otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com
google_project_iam_member.otel_lab_deploy["roles/<rol>"]   (6 roles: cloudtrace.agent,
  logging.logWriter, monitoring.metricWriter, artifactregistry.writer,
  secretmanager.secretAccessor, container.developer)
  id (verificado): "otel-lab-obs-2610 roles/<rol> serviceAccount:otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com"
  — el import id de *_iam_member va SEPARADO POR ESPACIOS (resource role member),
  no con slashes; con slashes da "Wrong number of parts".
google_service_account_iam_member.otel_lab_deploy_wi["serviceAccount:otel-lab-obs-2610.svc.id.goog[otel-lab/otel-lab-collector]"]
google_service_account_iam_member.otel_lab_deploy_wi["serviceAccount:otel-lab-obs-2610.svc.id.goog[otel-lab/default]"]
  id: "projects/otel-lab-obs-2610/serviceAccounts/otel-lab-deploy@otel-lab-obs-2610.iam.gserviceaccount.com roles/iam.workloadIdentityUser serviceAccount:otel-lab-obs-2610.svc.id.goog[otel-lab/<ksa>]"
google_secret_manager_secret.pg_password
  projects/otel-lab-obs-2610/secrets/pg-password
google_billing_budget.otel_lab[0]   (provider google-beta)
  013D56-6929B9-3AC57A/6260de7a-d122-476a-bc6d-2c8e03aa086e
google_monitoring_dashboard.otel_lab_sli[0]
  projects/797970839119/dashboards/8ceea108-ca30-4d79-b18a-f4610bfc8f71
```

Notas del import (verificadas en sesión):

- El **budget** solo importa con `google-beta` + `billing_project` +
  `user_project_override = true` (el provider GA no manda
  `x-goog-user-project` en billingbudgets → 403 SERVICE_DISABLED con ADC de
  usuario).
- El **dashboard** se gestiona con `ignore_changes = [dashboard_json]`: la API
  normaliza el JSON (layout 12→48 columnas, etag, relleno de defaults) y el
  provider lo compara literal. El JSON gestionado es
  `dashboards/cloud-monitoring-gcp.json`; para re-sincronizar a mano tras
  editarlo: `gcloud monitoring dashboards describe <id> --format=json` y
  comparar, o `terraform apply -replace` con el JSON nuevo.
- `project` del dashboard va como **número** (`797970839119`): el provider
  normaliza así el estado y pasar el ID alfanumérico fuerza replacement.
- Los **threshold_rules** del budget van en el orden en que los devuelve la
  API (50/90/100/150/80): es una lista y reordenarla genera diff.
- Las **firewall rules** de la VPC son auto-creadas por el modo custom y NO
  se gestionan desde aquí.

### Criterio de calidad

`terraform plan -detailed-exitcode` con defaults (`cluster_enabled=0`) →
**exit 0, sin diff**. El estado describe la realidad.

### Ciclo de sesión del cluster (apply/destroy)

Estado en reposo = `cluster_enabled=0` (el cluster es el recurso caro:
$0.10/h de fee de plano; solo vive durante sesiones de trabajo).

```powershell
# sesion: levantar cluster + pool spot (zonal us-central1-a, e2-medium spot,
# autoscaling 0-1, Workload Identity, deletion_protection=false)
... -e TF_VAR_cluster_enabled=1 terraform apply -input=false

# el provider GA no puede gestionar el add-on Secret Manager (solo beta):
# habilitarlo tras el apply (o el helm install del driver CSI):
gcloud container clusters update otel-lab-std --zone us-central1-a `
  --enable-component secret-manager

# workloads (requiere cluster vivo; el kubeconfig se genera dinamicamente con
# ADC dentro del modulo infra/gcp/deploy):
... -e TF_VAR_cluster_enabled=1 -e TF_VAR_deploy_workloads=1 terraform apply -input=false

# teardown de la sesion: quitar workloads y cluster; la infra permanente NO se toca
... -e TF_VAR_cluster_enabled=1 terraform destroy -input=false   # helm_release
... terraform apply -input=false                                  # cluster_enabled=0 por defecto: destruye el cluster
```

Regla operativa: *la sesión de nube se enciende para trabajar y se destruye al
terminar; registrar horas y coste en `docs/evidence/cluster-hours.csv`.*

### guard_cost (lista negra de coste)

```powershell
... terraform plan -input=false "-out=tfplan.bin"
... --entrypoint /bin/sh hashicorp/terraform:1.9.8 -c "terraform show -json tfplan.bin > tfplan.json"
python src/observabilidad-otel/scripts/guard_cost.py --provider google --plan-file src/observabilidad-otel/infra/gcp/tfplan.json
```

O en pipe: `terraform show -json tfplan.bin | python scripts/guard_cost.py --provider google`.
Exit 1 + reporte por recurso si el plan porta algo de la lista negra (Cloud
NAT, Autopilot, Cloud SQL, LB/forwarding rules/IP reservada, Cloud Run con
min-instances>0, pools no-spot). Tambien existe `--provider aws` (mismo
contrato, lista negra AWS). En CI corre en
`.github/workflows/terraform-plan-gcp.yml` sobre el plan de cada PR.

### CI

`terraform-plan-gcp.yml`: init `-backend=false` + fmt + validate + plan +
guard_cost (nunca apply). El apply solo ocurre desde main con aprobación
manual y credenciales del mantenedor.
