# infra/ — Terraform dual-cloud

Dos roots independientes (nunca uno único para las dos nubes):

- `infra/aws/` — provider `aws`, state en S3 con locking nativo. **Aún no
  implementado**: llega con el lado AWS de la fase 5; este README documenta hoy
  el lado GCP y se actualizará con el bootstrap exacto de AWS cuando exista.
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

- El entrypoint de `hashicorp/terraform:1.9.8` es `terraform`. Por eso el
  comando correcto es `... hashicorp/terraform:1.9.8 plan ...`, **no**
  `... hashicorp/terraform:1.9.8 terraform plan ...`.
- `gcp_project` y `gcp_region` se pasan SIEMPRE con `-var` en `plan`/`apply`
  (sin default a propósito; ver la nota de riesgo de desvío de región en el plan
  del proyecto). En PowerShell 5.1, `terraform plan` acepta `-out tfplan.bin`
  con espacio; `-out=tfplan.bin` se probó y falla en esta imagen, así que este
  README usa la variante verificada.

### Comandos literales verificados

Desde `D:\Projects\lab_obs`. Cada bloque es un comando completo, sin elipsis:

```powershell
# 1. init (backend GCS + providers google/google-beta/helm)
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 init -input=false

# 2. workspaces: list, crear solo la primera vez, seleccionar lab
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 workspace list

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 workspace new lab

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 workspace new bench

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 workspace select lab

# 3. fmt + validate
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 fmt -check -recursive

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 validate

# 4. plan 0-diff contra la realidad (cluster en reposo)
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 plan -detailed-exitcode -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1

# 5. plan guardado + guard_cost por stdin
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 plan -input=false -out tfplan.bin -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 show -json tfplan.bin | & ".venv\Scripts\python.exe" "src/observabilidad-otel/scripts/guard_cost.py" --provider google
```

### Salidas reales de la sesión de verificación

```text
$ docker run ... hashicorp/terraform:1.9.8 init -input=false
Initializing the backend...
Initializing modules...
Initializing provider plugins...
- Reusing previous version of hashicorp/google-beta from the dependency lock file
- Reusing previous version of hashicorp/helm from the dependency lock file
- Reusing previous version of hashicorp/google from the dependency lock file
- Using previously-installed hashicorp/google-beta v6.50.0
- Using previously-installed hashicorp/helm v2.17.0
- Using previously-installed hashicorp/google v6.50.0

Terraform has been successfully initialized!
exit=0

$ docker run ... hashicorp/terraform:1.9.8 workspace list
  default
  bench
* lab
exit=0

$ docker run ... hashicorp/terraform:1.9.8 workspace new lab
Workspace "lab" already exists
exit=1

$ docker run ... hashicorp/terraform:1.9.8 workspace new bench
Workspace "bench" already exists
exit=1

$ docker run ... hashicorp/terraform:1.9.8 workspace select lab
exit=0

$ docker run ... hashicorp/terraform:1.9.8 fmt -check -recursive
exit=0

$ docker run ... hashicorp/terraform:1.9.8 validate
Success! The configuration is valid.
exit=0

$ docker run ... hashicorp/terraform:1.9.8 plan -detailed-exitcode -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1
...
No changes. Your infrastructure matches the configuration.

Terraform has compared your real infrastructure against your configuration
and found no differences, so no changes are needed.
exit=0

$ docker run ... hashicorp/terraform:1.9.8 plan -input=false -out tfplan.bin -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1
...
No changes. Your infrastructure matches the configuration.

Terraform has compared your real infrastructure against your configuration
and found no differences, so no changes are needed.
exit=0

$ docker run ... hashicorp/terraform:1.9.8 show -json tfplan.bin | .venv\Scripts\python.exe src/observabilidad-otel/scripts/guard_cost.py --provider google
guard_cost: OK — 0 violaciones (provider=google)
exit=0
```

### Workspaces

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
$0.10/h de fee de plano; solo vive durante sesiones de trabajo). Estos comandos
usan el mismo entrypoint literal verificado arriba; no se ejecutan en una
revisión read-only porque crearían/destruirían recursos:

```powershell
# sesion: levantar cluster + pool spot (zonal us-central1-a, e2-medium spot,
# autoscaling 0-1, Workload Identity, deletion_protection=false)
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 apply -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1 -var cluster_enabled=1

# el provider GA no puede gestionar el add-on Secret Manager (solo beta):
# habilitarlo tras el apply (o el helm install del driver CSI):
gcloud container clusters update otel-lab-std --zone us-central1-a `
  --enable-component secret-manager

# workloads (requiere cluster vivo; el kubeconfig se genera dinamicamente con
# ADC dentro del modulo infra/gcp/deploy):
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 apply -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1 -var cluster_enabled=1 -var deploy_workloads=1

# teardown de la sesion: quitar workloads y cluster; la infra permanente NO se toca
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 destroy -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1 -var cluster_enabled=1 -var deploy_workloads=1

docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 apply -input=false -var gcp_project=otel-lab-obs-2610 -var gcp_region=us-central1
```

Regla operativa: *la sesión de nube se enciende para trabajar y se destruye al
terminar; registrar horas y coste en `docs/evidence/cluster-hours.csv`.*

### guard_cost (lista negra de coste)

Opción stdin, verificada arriba:

```powershell
docker run --rm `
  -v "/d/Projects/lab_obs/src/observabilidad-otel:/infra" `
  -w /infra/infra/gcp `
  -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json `
  -v "/c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro" `
  hashicorp/terraform:1.9.8 show -json tfplan.bin | & ".venv\Scripts\python.exe" "src/observabilidad-otel/scripts/guard_cost.py" --provider google
```

Opción `--plan-file`, verificada con `cmd /c` para evitar el BOM de PowerShell:

```powershell
cmd /c "docker run --rm -v /d/Projects/lab_obs/src/observabilidad-otel:/infra -w /infra/infra/gcp -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json -v /c/Users/Miguel/AppData/Roaming/gcloud/application_default_credentials.json:/creds/adc.json:ro hashicorp/terraform:1.9.8 show -json tfplan.bin > src\observabilidad-otel\infra\gcp\tfplan.json"
& ".venv\Scripts\python.exe" "src/observabilidad-otel/scripts/guard_cost.py" --provider google --plan-file "src/observabilidad-otel/infra/gcp/tfplan.json"
```

Exit 1 + reporte por recurso si el plan porta algo de la lista negra (Cloud
NAT, Autopilot, Cloud SQL, LB/forwarding rules/IP reservada, Cloud Run con
min-instances>0, pools no-spot). Tambien existe `--provider aws` (mismo
contrato, lista negra AWS). En CI corre en
`.github/workflows/terraform-plan-gcp.yml` sobre el plan de cada PR.

### CI

`terraform-plan-gcp.yml`: init `-backend=false` + fmt + validate + plan +
guard_cost (nunca apply). El apply solo ocurre desde main con aprobación
manual y credenciales del mantenedor.
