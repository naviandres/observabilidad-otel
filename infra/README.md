# infra/ — Terraform dual-cloud

Dos roots independientes (nunca uno único para las dos nubes):

- `infra/aws/` — provider `aws`, state en S3 con locking nativo
  (`use_lockfile`, Terraform ≥ 1.10), workspaces `lab|bench`. Bootstrap exacto
  más abajo; verificado con Terraform 1.10.5.
- `infra/gcp/` — providers `google` + `google-beta` (solo budget) + `helm`,
  state en GCS con locking nativo (object versioning del bucket de state).

---

## infra/aws — bootstrap (verificado 2026-10-13)

### Requisitos

- **Terraform ≥ 1.10**: el backend S3 usa locking nativo (`use_lockfile = true`,
  sin DynamoDB), que 1.9 no soporta. Verificado con **1.10.5**. Vale el binario
  local en el PATH o la imagen `hashicorp/terraform:1.10.5` (su entrypoint ya es
  `terraform`, así que el comando es `... hashicorp/terraform:1.10.5 plan ...`).
- Credenciales AWS con permiso sobre el bucket de state. Perfil del laboratorio:
  `AWS_PROFILE=otel-lab` (cuenta `621236283643`). Cualquier `plan` que necesite
  resolver el backend o el `data.aws_caller_identity` requiere acceso real; el
  gate de PR (`init -backend=false`) no.
- El bucket de state (`otel-lab-tfstate-621236283643`, `us-east-1`) debe existir
  antes del primer `init` con backend: se crea *bootstrap manual* (el root no
  puede gestionar el bucket que guarda su propio state).

### Comandos literales

```powershell
# 1. init con backend S3 (workspace_key_prefix = "lab")
terraform -chdir=infra/aws init -input=false

# 2. workspaces lab | bench (la primera vez) y seleccion del activo
terraform -chdir=infra/aws workspace list
terraform -chdir=infra/aws workspace new lab
terraform -chdir=infra/aws workspace new bench
terraform -chdir=infra/aws workspace select lab

# 3. fmt + validate (mismo gate que el workflow de PR)
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate

# 4. plan 0-diff contra la realidad (0 = sin cambios, 2 = drift)
terraform -chdir=infra/aws plan -detailed-exitcode -input=false -var aws_region=us-east-1 -var alert_email=<email>

# 5. plan guardado + guard_cost por stdin (lista negra AWS)
terraform -chdir=infra/aws plan -input=false -out=tfplan.bin -var aws_region=us-east-1 -var alert_email=<email>
terraform -chdir=infra/aws show -json tfplan.bin | & ".venv\Scripts\python.exe" "src/observabilidad-otel/scripts/guard_cost.py" --provider aws
```

Equivalentes del Makefile (usados en local y en CI):

```powershell
make tf-validate-aws   # init -backend=false + fmt -check -recursive + validate
make tf-plan-aws       # init -backend=false + plan + show -json | guard_cost.py --provider aws
```

> El gate read-only (`-backend=false`) se ejecutó en una **copia temporal** del
> root sin `backend.tf` (no toca el state remoto): `lab` renderiza **55** recursos
> y `bench` **52** (3 menos: el budget diario, el anomaly monitor y su
> subscription SNS, que solo viven en `lab`). El `init` con backend real y el
> primer `apply` son operaciones **online** y no se ejecutan en una revisión
> read-only.

### Workspaces

- `lab` (el activo): inventario completo, **incluido el AWS Budget 1 USD/día, el
  topic SNS `otel-lab-alerts` y el anomaly monitor** (`count = local.is_lab ? 1 : 0`).
- `bench`: mismo plano SIN budget ni anomaly monitor, para que los umbrales de
  coste no contaminen las mediciones del benchmark.

### Modulos (conventions.md §estructura)

- `modules/otel-collector` — publica el YAML del Collector AWS
  (`deploy/collector/otel-collector-aws.yaml`) como **objeto de auditoria** en el
  bucket de evidencia, con clave derivada del SHA-256 (`filesha256`) y `etag`
  del contenido. Recibe `bucket` + `config_source`; exporta `audit_key` y
  `config_sha256` (este último como output del root).
- `modules/guardrails` — `check` blocks con las invariantes de coste (§3.2/§3.5):
  retencion de logs, `keep last 5` en ECR, lifecycle del bucket de evidencia y
  `desired_count` + ECS Exec del service. El root le pasa los valores **reales**
  (no constantes) para que las aserciones no sean tautologicas; avisan en
  plan/apply sin abortar (el gate duro es `guard_cost.py`).

### Nombres de recursos gestionados

`aws_vpc.otel_lab` (10.20.0.0/16, sin NAT) · `aws_subnet.otel_lab_public`
(10.20.1.0/24) · `aws_internet_gateway.otel_lab` · `aws_route_table.otel_lab_public`
· `aws_security_group.otel_lab_task` (sin ingress) · `aws_s3_bucket.evidence`
(ciclo de vida 7 d) y `aws_s3_bucket.tfstate` (versionado) ·
`aws_ecr_repository.*` (service-a, service-b, otel-collector, postgres; keep-last-5,
scan_on_push) · `aws_cloudwatch_log_group.*` (`/otel/labs/ecs-platform/*`,
`/otel/labs/ecs-cluster/*`, retención 3 d) · `aws_ssm_parameter.*` (`/otel-lab/*`,
`REPLACED-OUT-OF-BAND`) · `aws_iam_role.task`/`.execution` + `aws_iam_user.deploy`
(política inline de mínimo privilegio) · `aws_xray_sampling_rule.otel_lab_base` ·
`aws_sns_topic.alerts` + `aws_budgets_budget.daily` + `aws_ce_anomaly_monitor` +
`aws_ce_anomaly_subscription` · `aws_ecs_cluster.otel_lab` + `aws_ecs_task_definition.otel_lab`
+ `aws_ecs_service.otel_lab` (`desired_count` de la var `service_desired_count`).

El topic `aws_sns_topic.alerts` (output `alerts_topic_arn`) es el destino común del
budget, el anomaly monitor y las **alarmas CloudWatch de la feature #7**: el
cableado canónico de `AlarmActions` es

```bash
make aws-alarms SNS_TOPIC_ARN="$(terraform -chdir=infra/aws output -raw alerts_topic_arn)"
```

(sin `SNS_TOPIC_ARN` las alarmas se crean/actualizan igual, pero sin acción SNS).

### Collector YAML — copia de auditoría en S3

`deploy/collector/otel-collector-aws.yaml` se sube como **objeto de auditoría**
al bucket de evidencias (`modules/otel-collector`, invocado desde `storage.tf`),
con **clave derivada del SHA-256 del contenido**, para poder demostrar qué
configuración exacta corría en cada sesión. La imagen ADOT
(`deploy/collector/otelcol-contrib.dockerfile`) hornea la misma copia; el objeto
S3 es la referencia reproducible.

### guard_cost (lista negra de coste, pata AWS)

Mismo contrato que la pata GCP (`--provider aws`, stdin o `--plan-file`). Además
de la lista negra (`aws_lb`, `aws_nat_gateway`, `aws_vpc_endpoint`, `aws_eip`,
`aws_eks_cluster`, `aws_db_instance`, ...) aplica
guardarraíles condicionales sobre el propio inventario: falla si aparece un
`aws_ecs_service` con `desired_count > 1` o un log group sin los **3 días** de
retención. En CI corre en `.github/workflows/terraform-plan.yml` sobre el plan de
cada PR (**nunca** en un `apply`).


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
