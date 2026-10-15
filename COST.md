# COST.md — modelo de coste dual-cloud y procedimiento de apagado

> Entregable de primer nivel del plan del laboratorio: el proyecto opera
> **dentro de créditos con guardarraíles**, no "$0/mes". Créditos disponibles:
> **$100 AWS + $300 GCP** (ventana de 90 días en GCP). El objetivo es minimizar
> el gasto dentro de ese presupuesto, con alertas de presupuesto/cuota y
> **teardown obligatorio** al cerrar cada sesión de nube.

## 1. Modelo de coste por nube

### AWS (ECS Fargate)

| Concepto | Coste | Cómo se goberna |
|---|---|---|
| Cómputo | Task **Fargate** perfil 1 vCPU / 2 GB (`cpu 1024`/`memory 2048` de la task definition real; ≈ $0,04928/h medido en us-east-1) — requisito del enunciado, pagado con créditos en **sesiones cortas** | `make aws-start/aws-stop/aws-destroy`; desired_count=0 fuera de sesión |
| Red | VPC + subnet pública + IGW; **sin NAT Gateway, sin ALB, sin VPC endpoints** | acceso por túnel SSM Session Manager (cifrado por el proveedor, $0) |
| Logs | CloudWatch Logs, retención **3 días**, 1 línea INFO por evento | decisión C3 del plan (~0.4 KB/request; margen ~11× sobre el free tier) |
| Trazas | X-Ray con sampling head 25 %; **apagado durante el benchmark** | decisión C2: coste ≈ $0 en el ciclo ABBA |
| Métricas | ≤10 contadores EMF; histogramas viven en Prometheus | decisión C1: `GetMetricData` se cobra siempre |
| Registry | ECR keep-last-5 | cleanup policy |
| Storage | S3 ≤5 GB con lifecycle 7 días | bucket de evidencia + state |
| Budget | AWS Budgets: umbral diario $1 + alertas 80/100 % | `aws_budget` en IaC + `cost_guard` diario en CI |

### GCP (GKE Standard)

| Concepto | Coste | Cómo se goberna |
|---|---|---|
| Cómputo | **GKE Standard zonal** (us-central1-a): fee de plano **$0.10/h** + node pool **spot e2-medium** autoscaling 0-3 (el default del `gcp-start`; 0-1 en sesiones sueltas según demanda) | el cluster solo vive en sesiones de trabajo (`cluster_enabled=0` en reposo); `make gcp-stop` (pool a 0) / `make gcp-destroy` |
| Perfil de nodo | e2-small (2 GiB) del diseño original NO dejó arrancar los system-pods en GKE ≥ 1.36 (`Insufficient cpu`); se escaló a **e2-medium spot (4 GiB)** — la escalada que el propio plan prevé (riesgo R-08), manteniendo el descuento spot | requests de CPU del chart bajados a **20 m/pod** para caber en el nodo; limits intactos |
| Red | VPC + subnet con **Private Google Access**; **sin Cloud NAT, sin Cloud LB** | egress a APIs de Google por PGA; acceso humano por `kubectl port-forward`/IAP |
| Logs | Cloud Logging, retención 3 días, mismo filtro de volumen que AWS | decisión C3 |
| Trazas | Cloud Trace con sampling 25 %; **apagado durante el benchmark** | decisión C2 (Cloud Trace preserva el `trace_id` W3C) |
| Métricas | Cloud Monitoring + Managed Prometheus; solo contadores | decisión C1 |
| Registry | Artifact Registry keep-last-5 + cleanup 7 días | `cleanup_policies` en IaC (verificado 0-diff) |
| Storage | GCS: evidencia con lifecycle 7 días; **state sin lifecycle a propósito** (borrar el state rompería el locking/auditabilidad — desviación documentada) | bucket de state con versioning = locking nativo |
| Secretos | Secret Manager, 1 secreto (`pg-password`) | valor fuera de Terraform; acceso por Workload Identity |
| Budget | GCP Budget COP 964 461 (~USD 300) `EXCLUDE_ALL_CREDITS`, umbrales **50/80/90/100/150 %** | alertas por correo (sin PubSub); en IaC y en el estado |

## 2. Listas por nube

**Blanca (lo que el diseño puede usar)** — AWS: VPC/subnet pública/IGW/route
table, SG sin ingress, ECS Fargate (perfil mínimo), ECR, S3 ≤5 GB, SSM
Parameter Store + Session Manager, CloudWatch (≤10 métricas custom, ≤3
alarmas, ≤3 dashboards, ≤5 GB logs), X-Ray, IAM, Budgets, Cost Explorer.
GCP: VPC/subnet/firewall sin ingress público, Private Google Access, GKE
Standard con pool spot, Workload Identity, Artifact Registry, GCS (evidencia +
state), Secret Manager (≤2 secretos), Cloud Trace/Logging/Monitoring (+
Managed Prometheus), IAM/Service Accounts, Budgets + Quota Alerts.

**Negra (vetada, impuesta por `scripts/guard_cost.py` sobre el JSON del plan)**
— AWS: ALB, NAT Gateway, VPC endpoints de interfaz, ENI sueltas/Service
Connect, Elastic IPs 24/7, EKS, RDS, WAF, Secrets Manager, Container Insights,
Application Signals, Managed Prometheus collectors, endpoint OTLP de
CloudWatch, CloudFront/API Gateway/Kinesis/Firehose/ElastiCache/Aurora/MSK/
DataSync/App Runner/Lightsail/Cloud9/Redshift/SageMaker/Bedrock/OpenSearch.
GCP: **Cloud NAT**, **GKE Autopilot**, **Cloud Load Balancing gestionado**,
**Cloud SQL**, Filestore, **node pools no-spot**, **Cloud Run con
min-instances > 0**, IP externa reservada permanente, BigQuery/Pub/Sub/Vertex.

El fee de plano de GKE Standard ($0.10/h) NO está vetado: el diseño lo
necesita y se goberna con teardown por sesión + registro de horas + alerta de
budget.

## 3. Guardarraíles

| Guardarraíl | Mecanismo | Falla si |
|---|---|---|
| Techo de gasto AWS | `aws_budget` umbral $1/día + alertas 80/100 % | coste del día ≥ $1 |
| Techo de gasto GCP | Budget COP 964 461 con umbrales 50/80/90/100/150 % | consumo ≥ 50 % sin sesión en curso |
| Gasto diario | `cost_guard` (`--provider aws|google`) en workflow programado diario | > $0.20/día (AWS) o > $1/día (GCP) en mes activo |
| Horas de cómputo | `instance-hours.csv` (Fargate) y `cluster-hours.csv` (GKE); variable `max_session_hours_per_month` en IaC | acumulado > presupuesto de horas de la fase |
| Recursos prohibidos | `scripts/guard_cost.py --provider google|aws` sobre `terraform show -json` (pre-commit + CI de ambos roots) | cualquier tipo de la lista negra aparece en el plan |
| Higiene de datos | retenciones 3 días (logs), lifecycle 7 días (evidencia), keep-last-5 (registries), sin `prevent_destroy` | — |
| Botón de parada | `make aws-stop` / `make gcp-stop` | recurso corriendo fuera de sesión |
| Botón de destrucción | `make aws-destroy` / `make gcp-destroy` + verificación de gasto | — |

## 4. Procedimiento de teardown (por nube)

**GCP** (orden):

1. `terraform apply` con `deploy_workloads=0` → destruye los workloads Helm.
2. `terraform apply` con `cluster_enabled=0` → destruye cluster + node pool
   (el fee de plano se detiene; la infra permanente —VPC, subnet, AR, buckets,
   SA/IAM, secreto, budget, dashboard— no se toca: es $0 en reposo).
3. Verificar: `gcloud container clusters list` vacío; el cluster no aparece en
   la lista; gasto diario ≈ $0 en la factura.
4. Registrar la línea `delete` + `summary` en `docs/evidence/cluster-hours.csv`.

**AWS** (orden): `make aws-stop` (desired_count=0) → `make aws-destroy`
(service + cluster, con verificación de lista vacía) → `make aws-net-destroy`
(la red tagueada); destrucción completa de la infraestructura gestionada por
IaC: `terraform destroy` del root AWS (state S3). Verificar con
`aws ce get-cost-and-usage` que el día es ≈ $0.

**Regla operativa:** *la sesión de nube (task Fargate / cluster GKE) se
enciende para trabajar y se destruye al terminar; el teardown y la verificación
de coste son parte del DoD de cada fase de nube. El ciclo de desarrollo diario
ocurre en el modo local (`docker compose up`), que cuesta $0.*

## 5. Evidencia del mes en curso

- `docs/evidence/cluster-hours.csv` — horas de cluster GKE por sesión (líneas
  create/delete/summary con fee de plano y subtotal; incluye la sesión que
  motivó la escalada e2-small→e2-medium).
- `docs/evidence/01-wi-binding-gcp.md` — binding Workload Identity real del
  proyecto (cero coste).
- Cost Explorer (AWS) y la página de Budgets (GCP) como fuente primaria de
  gasto real por proveedor.
