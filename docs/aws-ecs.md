# Despliegue AWS — ECS Fargate (Fase 2)

> Lado AWS del pipeline dual-cloud. El mismo `service-a` → `service-b` →
> PostgreSQL con el **Collector ADOT** exportando a X-Ray / CloudWatch Logs /
> CloudWatch EMF y un **CloudWatch Agent** de métricas ECS. La vertiente gemela en
> GCP está en `deploy/collector/otel-collector-gcp.yaml` + `helm/` + `infra/gcp`.

## 1. Decisión de diseño (PLAN §2.B/§3.2)

| Decisión | Elección | Por qué |
|---|---|---|
| Cómputo | **ECS Fargate**, task `otel-lab` (cpu 1024 / mem 2048, `awsvpc`) | sin nodos que parchear, coste por segundo; paridad de fases con GKE |
| Todos los contenedores en **una task** | 5 containers: service-a, service-b, postgres, otel-collector, cloudwatch-agent | la app es polilítica (varios procesos) y Fargate cobra por task; 1 task = 1 despliegue |
| Acceso | **sin ALB, sin ingress público**; `ECS Exec` (SSM) + `scripts/tunnel.ps1` | R-10: nadie abre la red salvo el túnel; sin SG con ingress |
| Red | VPC + **una subnet pública** + IGW, **sin NAT ni VPC endpoints** | lista negra del PLAN §3.2; el egress sale por IGW con `assignPublicIp=ENABLED` (efímero) |
| Logs | driver **`awslogs`** en los 5 containers → grupos `/otel/labs/ecs-platform/<contenedor>` | Fargate **no soporta** `json-file` (solo `awslogs`/`splunk`/`awsfirelens`); un solo stdout por servicio ⇒ sin duplicación |
| Imágenes | app + collector propios en **ECR** con **tag fijo** (`$(IMAGE_TAG)`, nunca `:latest`); postgres y CW-agent públicos fijados | C3 |
| Config collector | YAML **horneado en la imagen** del collector (`deploy/collector/otelcol-contrib.dockerfile`) | secretos vía SSM (CW agent), el resto de configuración es versionable |
| Identidad de infra | `resourcedetection/ecs` (task metadata endpoint `169.254.170.2`) | R-10-equivalente: Fargate no tiene IMDS (`http_tokens` es EC2-launch-type) |
| Roles | **`otel-lab-exec-ecs`** (execution) y **`otel-lab-task-ecs`** (task) | creados por el humano en la Fase 0 (este lab no tiene IAM de autómata) |
| Coste | `aws-start`/`aws-stop`/`aws-destroy` | ciclo de coste del PLAN §3.5; guardarraíl `scripts/guard_cost.py` |

Artifactos versionables (todo el punto anterior es **$0 y offline**):

```
deploy/aws/task-definition.json       template con tokens (__AWS_ACCOUNT_ID__, …)
deploy/collector/otel-collector-aws.yaml  config ADOT (API awsxray/awsemf/awscloudwatchlogs)
deploy/collector/otelcol-contrib.dockerfile  build del collector ECR
scripts/tunnel.ps1                    túnel SSM ECS Exec (sin ngrok/ingress)
Makefile (targets aws-*)             ciclo de vida idempotente
```

## 2. Ciclo de vida (checklist del operador)

```powershell
# 0) Validación offline (sin AWS): el binario ADOT no tiene `validate`, así que
#    se valida por ARRANQUE local del contenedor con el mismo YAML tagueado.
make validate-collector

# 1) una sola vez por tag de imagen:
make aws-push                      # ECR login + build + push (3 imágenes)

# 2) encender (red → cluster → task def render+register → service count=1):
make aws-start

# 3) disponer del túnel (EC2 → SSM Session Manager → ECS Exec):
make tunnel                        # service-a:8080, service-b:8081, collector 4317

# 4) smoke: POST localhost:8080/orders → traza en X-Ray, logs en CloudWatch

# 5) parar (no borra nada; desired_count=0 = sin cómputo):
make aws-stop

# 6) nuclear + verificación:
make aws-destroy                   # borra service+cluster, comprueba list-clusters
make aws-net-destroy               # borra la red tagueada (opcional, $0)
```

## 3. Task definition (5 containers)

| Container | Imagen | Puertos | Función |
|---|---|---|---|
| service-a | ECR `otel-lab/service-a:<tag>` | 8080 | API principal, trazas OTLP → collector |
| service-b | ECR `otel-lab/service-b:<tag>` | 8081 | API de inventario, trazas OTLP → collector |
| postgres | `public.ecr.aws/docker/library/postgres:16-alpine` | 5432 | DB transaccional (volumen nombrado `postgres-data`) |
| otel-collector | ECR `otel-lab/otel-collector:<tag>` (ADOT 0.46.0 + YAML AWS) | 4317 | OTLP/gRPC; exporta X-Ray + EMF + logs |
| cloudwatch-agent | `public.ecr.aws/cloudwatch-agent/cloudwatch-agent:1.300073.0b1828` | — | métricas ECS; config vía SSM, `essential:false` |

**Reservas de recursos.** El perfil de la task es `cpu 1024` / `memory 2048`
(§1). La suma de las reservas por contenedor cabe dentro de ese perfil:
CPU `256 + 256 + 256 + 256 = 1024` (el `cloudwatch-agent`, best-effort, se deja
sin reserva de CPU) y memoria `384 + 384 + 512 + 512 + 128 = 1920 < 2048`. Si se
sube un perfil por contenedor hay que subir antes el perfil de la task.

**Variables y secretos del collector.** El contenedor `otel-collector` recibe
`AWS_REGION` como `environment` y los seis valores de Grafana Cloud
(`GRAFANA_PROM_URL`/`AUTH`, `GRAFANA_TEMPO_ENDPOINT`/`AUTH`,
`GRAFANA_LOKI_ENDPOINT`/`AUTH`) como `secrets` de SSM, porque su config
horneada (`deploy/collector/otel-collector-aws.yaml`) los expande con
`${env:...}`. La contraseña de Postgres y las URLs de base de datos de los
servicios también se inyectan como `secrets` de SSM; en el JSON versionado solo
quedan las referencias por ARN, nunca el valor.

El template usa tokens que `make aws-start` renderiza con `sed`:

- `__AWS_ACCOUNT_ID__`, `__AWS_REGION__`, `__ECR_REGISTRY__`, `__IMAGE_TAG__`
  (la salida `task-definition.rendered.json` está en `.gitignore`).

Rollos de los roles:

- **execution** (`otel-lab-exec-ecs`): ECR pull, `logs:CreateLogStream`,
  `logs:PutLogEvents` (grupos `awslogs-create-group: true`), acceso a SSM
  (secret del CW agent).
- **task** (`otel-lab-task-ecs`): `xray:PutTraceSegments`, `xray:PutTelemetryRecords`,
  `cloudwatch:PutMetricData`, `ssmmessages:*` (ECS Exec).

## 4. Coste y verificación

- Los targets `aws-stop` (`desired_count=0`) y `aws-destroy` terminan con
  verificación (describe/list) para demostrar que no queda cómputo
  facturando. El coste de red residual (VPC/subnet/IGW) es ~$0.
- `scripts/guard_cost.py` consulta el **Cost Explorer** (state en S3) y frena
  con aviso si el proyecto supera el presupuesto ($100 AWS, PLAN §3.5).
- Verificación de Fase 2 (CHECKPOINTS): los 4 ficheros de arriba existen y el
  template registrable contiene los 5 containers, `FARGATE`, `awsvpc`,
  `cpu:1024`/`memory:2048` y tags fijos (ver `tests/test_ecs_deliverable.py`).

## 5. Pendiente humano (Fase 0) — fuera del alcance de este fichero

- Repos ECR `otel-lab/{service-a,service-b,otel-collector}`.
- Roles `otel-lab-exec-ecs` / `otel-lab-task-ecs` (IAM manual).
- SSM `/otel-lab/cloudwatch-agent/config` y bucket S3 del guard de coste:
  **esto** es lo que bloquea el encendido en la nube, no el código.