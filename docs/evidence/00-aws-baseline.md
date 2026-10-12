# 00 — Línea base free tier / créditos AWS (Fase 0, tarea 0.1 — PLAN §3.1)

> **Fecha de auditoría:** 2026-10-11 · **Cuenta AWS:** `621236283643` ("Moonshot Mode") ·
> **Organización:** `o-mjga20juxh` (management `070443470565`) ·
> **Región operativa:** `us-east-1` (pasada explícita, nunca por defecto) ·
> **Identidad CLI:** `arn:aws:iam::621236283643:user/otel-lab-deploy` (perfil `otel-lab`; **nunca** root) ·
> **Toolchain:** aws-cli 2.32.31, Python 3.13.11, Windows 11.
> **Créditos:** $100 USD — **dato declarado por el operador el 2026-10-11** (no hay API listable
> de saldo; las señales operativas son AWS Budgets + Cost Explorer).
> **Alcance:** re-verificación de los importes marcados con ⚠ en PLAN §3.1 (tarea 0.1) y estado de
> los recursos de Fase 0 en AWS. Fuentes: salidas CLI de `aws` (reproducibles, secciones 2-10) y
> documentación oficial de AWS. **Evidencia = volcado CLI + dato declarado con fecha (sin capturas
> — enmienda 2026-10-11).**
> **Regla anti-invento:** todo número sin salida CLI o fuente citada va marcado como "dato declarado
> por el operador".

---

## 1. Lista blanca AWS (PLAN §3.3) — cobertura free tier por recurso

| # | Recurso | Cobertura gratuita (PLAN §3.1) | Límite | Estado en el proyecto | Verificación |
|---|---|---|---|---|---|
| 1 | VPC + subnet pública + IGW + route table + SG (sin ingress) | $0 (la red no se tarifa) | — | default VPC presente; VPC del diseño se crea con IaC en Fase 2 | CLI: `aws ec2 describe-vpcs` (sección 2) |
| 2 | ECS Fargate (0.5 vCPU / 1 GB) | Sin free tier: se paga de créditos | ≈$0.025/h el perfil mínimo | 0 clusters (cómputo en Fase 2) | CLI: `aws ecs list-clusters` → `[]` (sección 2) |
| 3 | ECR | 500 GB/mes | $0.10/GB·mes excedido | **2 repos creados** (service-a, service-b), tag IMMUTABLE, lifecycle "expire untagged 7 d" | CLI: `aws ecr describe-repositories` + `get-lifecycle-policy` (sección 6) |
| 4 | S3 ≤5 GB | 5 GB/mes + 20.000 GET + 2.000 PUT | estándar | **1 bucket de evidencia** con lifecycle abort-multipart 7 d y bloqueo de acceso público total | CLI: `create-bucket` + `get-public-access-block` (sección 6) |
| 5 | SSM Session Manager | Sin cargo por sesión (híbridos $0.05 desde 2026-09-30) | — | sin sesión activa (no hay cómputo); plugin local **instalado** (sección 5) | CLI: `session-manager-plugin --version` → `1.2.835.0` |
| 6 | SSM Parameter Store (Standard) | 10.000 parámetros | $0.05/50 excedidos | sin parámetros aún (Fase 0 opcional / Fase 2) | CLI: `aws ssm get-parameters-by-path` (pendiente) |
| 7 | CloudWatch Logs | 5 GB/mes (ingest + archivo + escaneo) | $0.50/GB ingest | 0 grupos; retención 3 d se aplica por grupo al nacer (sección 7) | CLI: `aws logs describe-log-groups` |
| 8 | CloudWatch métricas custom | 10/mes | $0.30/métrica·mes | 0 (EMF en Fase 3; ≤4 de lista blanca) | CLI (Fase 3): `aws cloudwatch list-metrics` |
| 9 | CloudWatch alarmas / dashboards | 10 alarmas, 3 dashboards | $0.10/alarma, $3/dashboard | 0 | CLI: `aws cloudwatch describe-alarms` |
| 10 | X-Ray | 100.000 trazas registradas/mes | ≈$5/1 M registradas | sampling rule de red de seguridad pendiente de escritura (decisión en sección 9) | CLI: `aws xray get-sampling-rules` |
| 11 | IAM | $0 (sin tarifa) | — | usuario `otel-lab-deploy` least-privilege (sin Admin) | CLI: `aws sts get-caller-identity` + sondeo de acciones (sección 4) |
| 12 | AWS Budgets | $0 (Budgets no cobra) | — | **2 budgets**: `free-tier` ($100/mes) y `otel-lab-daily` ($1/día) | CLI: `aws budgets describe-budgets` (sección 3) |
| 13 | Cost Explorer | $0 por consulta estándar | — | operativo (lo usa `cost_guard`) | CLI: `aws ce get-cost-and-usage` |
| 14 | Cost Anomaly Detection | $0 | — | **1 monitor** `otel-lab-anomaly` (SERVICE) | CLI: `aws ce get-anomaly-monitors` (sección 3) |
| 15 | IPv4 público (IP efímera del task) | Se cobra mientras está en uso | $0.005/h | 0 (no hay tarea) | n/a hasta Fase 2 |
| 16 | Data transfer out | 100 GB/mes | $0.09/GB excedido | ≈0 (egress OTLP interno) | — |

**Conclusión de cobertura:** ningún recurso de la lista blanca AWS queda fuera de créditos/free tier
en el volumen del lab. Los consumos reales previstos de los $100 son **horas de task Fargate**
(sección 10), **ingest de CloudWatch Logs / X-Ray** (acotados por C2/C3 del PLAN) y **egress mínimo**.

---

## 2. Resolución del "sandbox" de la cuenta (tarea 0.0) — SCPs del propio org

Al dar de alta la cuenta se observaron denegaciones que impedían `ecs:*`, `ecr:*`, `xray:*`,
`ec2` (VPC/SG) y `s3:CreateBucket`, junto con un *region-lock* aparente a `us-east-1`.

**Causa real (2026-10-11):** **no** era un "sandbox mode" de AWS ni un caso de soporte. La cuenta
`621236283643` ("Moonshot Mode") pertenece a la organización `o-mjga20juxh`, cuyo **management
account `070443470565` es del propio operador** (cuenta auto-creada con el alta en "advanced mode").
Dos **Service Control Policies** adjuntas a la **Raíz** (`r-7sxt`) causaban las denegaciones:

- `AdvancedModeRegionRestrictionSecurityControlPolicy` (region-lock + denies `ecs`/`ecr`/`xray`/`ec2`/`s3`).
- `ManagedAccountSecurityControlPolicy`.

**Fix aplicado por el operador (2026-10-11):** Organizations → Raíz → pestaña SCP → **Separar**
(detach) ambas políticas, dejando solo `FullAWSAccess`. **No fue necesario abrir caso de soporte.**
Efecto verificado en minutos con el perfil `otel-lab`:

```powershell
aws ecs list-clusters --profile otel-lab
# {"clusterArns": []}

aws ec2 describe-vpcs --profile otel-lab --query 'Vpcs[].{VpcId:VpcId,Cidr:CidrBlock,Default:IsDefault}'
# [ { "VpcId": "vpc-08cfe52a9aea923ab", "Cidr": "172.31.0.0/16", "Default": true } ]
```

> El ID de la SCP que aparecía en los mensajes de error (`p-tdaa03nz`) no coincide con el nombre
> mostrado en la consola; el nombre es la fuente de verdad. Estado actual: **AWS desbloqueado**.

---

## 3. AWS Budgets + Cost Anomaly Detection (tarea 0.2)

### 3.1 Budget mensual existente (`free-tier`, no se re-crea)

`aws budgets describe-budgets --account-id 621236283643 --profile otel-lab`:

```json
{
  "Budgets": [
    { "BudgetName": "free-tier", "BudgetLimit": { "Amount": "100.0", "Unit": "USD" },
      "TimeUnit": "MONTHLY", "BudgetType": "COST",
      "CalculatedSpend": { "ActualSpend": { "Amount": "0.0", "Unit": "USD" } },
      "HealthStatus": { "Status": "HEALTHY" } }
  ]
}
```

Notificaciones (`describe-notifications-for-budget --budget-name free-tier`):

```json
{ "Notifications": [
    { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 100.0, "NotificationState": "OK" },
    { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 50.0,  "NotificationState": "OK" },
    { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 80.0,  "NotificationState": "OK" } ] }
```

### 3.2 Budget diario nuevo (`otel-lab-daily`)

Creado vía CLI (JSON en archivo temporal, fuera del repo):

```powershell
aws budgets create-budget --account-id 621236283643 --profile otel-lab `
  --budget file://budget-daily.json `
  --notifications-with-subscribers file://budget-daily-notif.json
# --budget:        {"BudgetName":"otel-lab-daily","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"DAILY","BudgetType":"COST"}
# --notif (100 %): ACTUAL / GREATER_THAN / 100 % PERCENTAGE, subscriber EMAIL = correo del operador
```

Alerta adicional al **80 %** (fidelidad con PLAN §3.5, que fija 80 %/100 %):

```powershell
aws budgets create-notification --account-id 621236283643 --budget-name otel-lab-daily --profile otel-lab `
  --notification file://budget-notif80.json --subscribers file://budget-sub80-list.json
```

Estado final verificado:

```powershell
aws budgets describe-budgets --account-id 621236283643 --profile otel-lab `
  --query 'Budgets[].{Name:BudgetName,Type:BudgetType,Unit:TimeUnit,Limit:BudgetLimit,Spend:CalculatedSpend.ActualSpend.Amount}'
# [ { "Name": "free-tier",      "Type": "COST", "Unit": "MONTHLY", "Limit": {"Amount":"100.0","Unit":"USD"}, "Spend": "0.0" },
#   { "Name": "otel-lab-daily", "Type": "COST", "Unit": "DAILY",   "Limit": {"Amount":"1.0","Unit":"USD"},   "Spend": "0.0" } ]

aws budgets describe-notifications-for-budget --account-id 621236283643 --budget-name otel-lab-daily --profile otel-lab
# { "Notifications": [
#     { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 100.0, "NotificationState": "OK" },
#     { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 80.0,  "NotificationState": "OK" } ] }
```

### 3.3 Cost Anomaly Detection

```powershell
aws ce create-anomaly-monitor --profile otel-lab --cli-input-json file://anomaly-monitor.json
# { "MonitorArn": "arn:aws:ce::621236283643:anomalymonitor/b1e77542-a0eb-4252-941f-3f7e00e0b55a" }

aws ce get-anomaly-monitors --profile otel-lab
# { "AnomalyMonitors": [
#     { "MonitorArn": "arn:aws:ce::621236283643:anomalymonitor/b1e77542-a0eb-4252-941f-3f7e00e0b55a",
#       "MonitorName": "otel-lab-anomaly", "MonitorType": "DIMENSIONAL",
#       "MonitorDimension": "SERVICE", "DimensionalValueCount": 0,
#       "CreationDate": "2026-10-12T01:10:01.464648482Z" } ] }
```

> El API de creación por CLI exige `MonitorDimension` para monitores `DIMENSIONAL` (un monitor
> `CUSTOM` requiere igualmente una especificación de dimensión válida); se usa el monitor
> dimensional de servicio (`SERVICE`), que es el más simple y detecta picos por servicio.
> El monitor **existe** y es el guardarraíl de anomalía **activo** de la cuenta.
> **Suscripción por correo del monitor: diferida a la fase 5.A (IaC/guardarraíles)**, donde se
> creará el topic SNS con los permisos adecuados (con la política declarada actual,
> `aws sns create-topic` responde `AuthorizationError` porque no concede SNS). Mientras tanto la
> cobertura de alerta por correo sigue activa por otra vía: los budgets `free-tier` ($100/mes) y
> `otel-lab-daily` ($1/día) tienen suscripción de correo del operador verificada (§3.1-§3.2).

---

## 4. IAM least privilege del despliegue (tarea 0.3)

Identidad de despliegue (no root):

```powershell
aws sts get-caller-identity --profile otel-lab
# { "UserId": "AIDA****5H5UB", "Account": "621236283643",
#   "Arn": "arn:aws:iam::621236283643:user/otel-lab-deploy" }
```

El usuario `otel-lab-deploy` **no** tiene permisos de lectura de IAM, por diseño least-privilege
(no `iam:Get*`). Los comandos de introspección de la tarea devuelven `AccessDenied`:

| Comando | Resultado | Lectura |
|---|---|---|
| `aws iam get-user --user-name otel-lab-deploy` | AccessDenied (`iam:GetUser`) | sin permiso de lectura IAM |
| `aws iam list-attached-user-policies` | AccessDenied (`iam:ListAttachedUserPolicies`) | sin managed policies legibles |
| `aws iam list-user-policies` | AccessDenied (`iam:ListUserPolicies`) | sin listado inline |
| `aws iam get-user-policy` | AccessDenied (`iam:GetUserPolicy`) | sin lectura del JSON inline |
| `aws iam list-users` | AccessDenied (`iam:ListUsers`) | — |

**Sondeo de acciones (evidencia comportamental de least-privilege, sin leerse el JSON):**

| Acción | Resultado | Interpretación |
|---|---|---|
| `ecs:ListClusters` | OK | permitido |
| `ec2:DescribeVpcs` | OK | permitido |
| `ecr:DescribeRepositories` / `CreateRepository` / `PutLifecyclePolicy` | OK | permitido |
| `budgets:DescribeBudgets` / `CreateBudget` / `CreateNotification` | OK | permitido |
| `ce:GetAnomalyMonitors` / `CreateAnomalyMonitor` | OK | permitido |
| `s3:CreateBucket` / `s3:PutLifecycleConfiguration` / `s3:PutBucketPublicAccessBlock` | OK | permitido |
| `s3:ListAllMyBuckets` | AccessDenied | **no** hay lectura de todo el inventario S3 |
| `s3:GetLifecycleConfiguration` | AccessDenied | **no** hay lectura de lifecycle |
| `iam:GetUser` / `ListUsers` / `GetUserPolicy` | AccessDenied | **no** hay ningún `iam:*` de escritura/administración |

Conclusión: **NO hay `AdministratorAccess` ni `iam:*` completo**; el usuario opera por acciones
acotadas. El JSON de la política inline no es legible por el propio usuario (`iam:ListUserPolicies`
→ `AccessDenied`, re-verificado 2026-10-11); no obstante, el contenido declarado de la política
(PLAN §0.3 / runbook A.3) queda registrado en el anexo §4.1, a codificar como IaC en la fase 5.A
(IaC/guardarraíles):

- Acciones: `ecs:*`, `ecr:*`, `logs:*`, `cloudwatch:*`, `xray:*`, `ssm:*`, `budgets:*`, `ce:*`, `s3:*`, `ec2:*`.
- `iam:PassRole` **restringido** a `Resource: arn:aws:iam::621236283643:role/otel-lab-*` con
  `Condition: iam:PassedToService = ecs-tasks.amazonaws.com`.
- Sin `iam:*` de administración, sin credenciales root.

#### 4.1 Anexo — Política inline de `otel-lab-deploy` (declarada)

> **Texto declarado en el runbook §A.3**; el usuario no tiene `iam:Get*` (`AccessDenied`
> verificado 2026-10-11, §4) por lo que este JSON **no** se ha leído por CLI — la verificación es
> **funcional, por sondeo comportamental** (§4, tabla de acciones), coherente con el conjunto
> declarado. Se reproduce el documento de política IAM fiel a la declaración de §A.3 (lista de
> acciones + `iam:PassRole` restringido); a codificar como IaC en la fase 5.A (IaC/guardarraíles).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:*", "ecr:*", "logs:*", "cloudwatch:*", "xray:*",
        "ssm:*", "budgets:*", "ce:*", "s3:*", "ec2:*"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::621236283643:role/otel-lab-*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "ecs-tasks.amazonaws.com"
        }
      }
    }
  ]
}
```

Excluye deliberadamente cualquier `iam:*` de administración y las credenciales root (§A.3.2).

---

## 5. Tooling local (tarea 0.4)

```powershell
aws --version
# aws-cli/2.32.31 Python/3.13.11 Windows/11 exe/AMD64
```

**Session Manager plugin:** **INSTALADO** — versión **`1.2.835.0`**. Binario en
`C:\Program Files\Amazon\SessionManagerPlugin\bin\session-manager-plugin.exe`; ese directorio
(`C:\Program Files\Amazon\SessionManagerPlugin\bin\`) está en el **PATH de Usuario**. Las
terminales nuevas lo resuelven; una shell ya abierta antes de la instalación no lo ve hasta
reiniciarse (hereda el PATH antiguo) — no es un fallo de instalación.

`aws ssm start-session` está disponible como subcomando del CLI; la sesión real contra un workload
se ejercita en la Fase 2 (aún no hay cómputo). Verificación local:
`session-manager-plugin --version` → `1.2.835.0`.

---

## 6. Bucket S3 de evidencia + repositorio ECR (tarea 0.5)

### 6.1 S3

```powershell
aws s3api create-bucket --bucket otel-lab-evidence-621236283643 --region us-east-1 --profile otel-lab
# { "Location": "/otel-lab-evidence-621236283643", "BucketArn": "arn:aws:s3:::otel-lab-evidence-621236283643" }

aws s3api put-bucket-lifecycle-configuration --bucket otel-lab-evidence-621236283643 --profile otel-lab `
  --lifecycle-configuration file://s3-lifecycle.json
# regla: abort-incomplete-multipart-7d  (AbortIncompleteMultipartUpload DaysAfterInitiation = 7)

aws s3api put-public-access-block --bucket otel-lab-evidence-621236283643 --profile otel-lab `
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

Verificación de "nada público":

```powershell
aws s3api get-public-access-block --bucket otel-lab-evidence-621236283643 --profile otel-lab
# { "PublicAccessBlockConfiguration": { "BlockPublicAcls": true, "IgnorePublicAcls": true,
#     "BlockPublicPolicy": true, "RestrictPublicBuckets": true } }
```

`get-bucket-lifecycle-configuration` devuelve `AccessDenied` (`s3:GetLifecycleConfiguration` no está
en la política del usuario de despliegue), pero el `put-bucket-lifecycle-configuration` **respondió
éxito** (`TransitionDefaultMinimumObjectSize`), por lo que la regla quedó aplicada. Lectura de vuelta
= **paso HUMANO** (consola) o al ampliar la política en IaC.

### 6.2 ECR

```powershell
aws ecr describe-repositories --region us-east-1 --profile otel-lab `
  --query 'repositories[].{name:repositoryName,uri:repositoryUri,mutability:imageTagMutability}'
# [ { "name": "otel-lab/service-a", "uri": "621236283643.dkr.ecr.us-east-1.amazonaws.com/otel-lab/service-a", "mutability": "IMMUTABLE" },
#   { "name": "otel-lab/service-b", "uri": "621236283643.dkr.ecr.us-east-1.amazonaws.com/otel-lab/service-b", "mutability": "IMMUTABLE" } ]
```

Lifecycle "expire untagged 7 d" aplicado y verificado con `get-lifecycle-policy` en ambos repos:

```json
{"rules":[{"rulePriority":1,"description":"expire untagged images after 7 days",
 "selection":{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":7},
 "action":{"type":"expire"}}]}
```

Login contra el registry (token **nunca** persistido; solo se comprueba que se emite):

```powershell
aws ecr get-login-password --region us-east-1 --profile otel-lab   # token de 2172 caracteres, no se guarda
docker login --username AWS --password-stdin 621236283643.dkr.ecr.us-east-1.amazonaws.com
```

---

## 7. Retención de CloudWatch Logs (tarea 0.6 / PLAN §3.5 C3) — decisión

**Decisión (no hay acción en Fase 0):** en aws-cli 2.32.31 **no existe** una API de retención por
defecto a nivel de cuenta (`aws logs put-account-settings` / `get-account-settings` no están
disponibles). La retención de **3 días** se aplica **por grupo de logs** con
`aws logs put-retention-policy --log-group-name <grupo> --retention-in-days 3` **cuando el grupo
nazca** (Fase 2/3: grupos `/otel/labs/{service}`). Alternativa: consola Logs → settings si el
operador la habilita. Documentado; sin acción en Fase 0.

---

## 8. OIDC de GitHub Actions (tarea 0.7) — decisión: diferido a CI

El repositorio aún **no** tiene workflows de GitHub Actions; la federación OIDC de AWS
(`aws-actions/configure-aws-credentials` con `permissions: id-token: write`, rol `otel-lab-gha` con
la política del operador de despliegue) se **define en la fase de CI/IaC**, no en Fase 0. Mientras
tanto, el acceso se hace con el **perfil IAM local `otel-lab`** (credenciales de larga duración
guardadas en el gestor del PC, **nunca** en el repo). Esto **no bloquea** el resto de Fase 0.

---

## 9. Muestreo de X-Ray (tarea 0.7 / PLAN §0.7) — decisión

**Decisión:** X-Ray se habilita con la **regla de muestreo de red de seguridad** (reservoir 1,
`fixed_rate = 0.05`) definida como `aws_xray_sampling_rule` en IaC. La **escritura** de la regla y
la configuración del Collector (exporter `awsxray`) llegan en **Fase 2** (junto al despliegue
Fargate), coherentemente con C2 del PLAN (export a X-Ray **apagado** durante el benchmark vía
`OTEL_TRACES_XRAY=false`; regla activa solo en el test de correlación, con decenas de trazas).
En Fase 0 se registra la **decisión**, no la regla. Verificación futura:
`aws xray get-sampling-rules`.

---

## 10. Guardarraíl de horas de cómputo — 150 h/mes (task Fargate)

El antiguo límite "150 h/mes" (heredado de EC2) se **re-interpreta como 150 h de task Fargate al
mes**, registradas en `docs/evidence/instance-hours.csv` (mismo formato que `cluster-hours.csv`).
El script `scripts/instance_hours.ps1` acumula horas de task frente al presupuesto y `make
budget-hours` falla si el acumulado supera el umbral. En Fase 0 no hay cómputo, por lo que el
acumulado es **0 h**. La política de sesiones (`make aws-start` / `make aws-stop` /
`make aws-destroy`; task Fargate solo durante la sesión de trabajo y teardown al terminar) es el
guardarraíl operativo y se documenta en la sección "Coste" del README.

---

## 11. Evidencia adoptada (sin capturas — enmienda 2026-10-11)

| Dato | Evidencia adoptada | Estado |
|---|---|---|
| Créditos $100 USD | **Dato declarado por el operador** (2026-10-11) — sin API listable | OK (declarado) |
| Budgets `free-tier` ($100/mes) y `otel-lab-daily` ($1/día) con alertas 50/80/100 % y 80/100 % | **Volcado CLI §3** | OK |
| Cost Anomaly Detection: monitor `otel-lab-anomaly` | **Volcado CLI §3.3** | OK (suscripción de correo = diferida a fase 5.A IaC) |
| IAM least privilege (sin Admin) | **Sondeo de acciones §4** + `sts get-caller-identity` | OK; política inline = declarada (anexo §4.1) |
| aws-cli versión | **Volcado CLI §5** | OK |
| Session Manager plugin `1.2.835.0` | **Verificación local §5** (ruta + PATH de Usuario) | OK (INSTALADO) |
| Bucket S3 + lifecycle + ECR + lifecycle | **Volcado CLI §6** | OK (lectura de vuelta del lifecycle S3 = HUMANO) |
| Retención Logs 3 d / X-Ray / OIDC | **Decisión documentada §7-§9** | OK (acción en Fase 2 / CI) |

---

## 12. Discrepancias detectadas vs PLAN (para actualización)

| # | PLAN decía | Verificado 2026-10-11 | Impacto |
|---|---|---|---|
| D1 | "sandbox mode de AWS; si persiste, caso de soporte" (errata previa) | Era **SCP del propio org** separada de la Raíz (sección 2); **sin** caso de soporte | A.0 resuelto y documentado |
| D2 | `budgets describe-budgets` con `--time-unit` | El flag **no existe**; alertas con `describe-notifications-for-budget` | Comandos de verificación corregidos |
| D3 | Retención CloudWatch "a nivel de cuenta" | **No hay** API de retención por defecto de cuenta; es **por grupo** | Retención 3 d por grupo al nacer (Fase 2) |
| D4 | "política de 150 h/mes" (resto de la era EC2) | Guardarraíl de **150 h/mes de task Fargate** + `instance-hours.csv` | Texto del catálogo a corregir |
| D5 | ECR: repo único por servicio | Creados **2 repos** (`otel-lab/service-a`, `otel-lab/service-b`) | Coherente con dos servicios |

---

## 13. Trazabilidad

- Comandos y salidas de §2-§6 ejecutados el 2026-10-11/12 desde la máquina del operador
  (aws-cli 2.32.31, perfil `otel-lab`, cuenta `621236283643`, región `us-east-1`).
- Región pasada **explícitamente** en todas las llamadas (nunca por defecto, PLAN §3.6).
- Ficheros de evidencia: `docs/evidence/00-aws-baseline.md` (este) y `docs/evidence/00-credentials.txt`.
