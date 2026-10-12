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
| Acceso | **sin ALB, sin ingress permanente**; regla ingress **temporal /32** del IP del operador (`scripts/access.ps1` Grant/Revoke) | SSM **no** soporta port-forwarding a Fargate (verificado en vivo 2026-10-12, §6); ECS Exec solo da shell. Steady-state: SG sin ingress (R-10) |
| Red | VPC + **una subnet pública** + IGW, **sin NAT ni VPC endpoints** | lista negra del PLAN §3.2; el egress sale por IGW con `assignPublicIp=ENABLED` (efímero) |
| Logs | driver **`awslogs`** en los 5 containers → grupos `/otel/labs/ecs-platform/<contenedor>` | Fargate **no soporta** `json-file` (solo `awslogs`/`splunk`/`awsfirelens`); un solo stdout por servicio ⇒ sin duplicación |
| Imágenes | app + collector + **postgres con seed horneado** en **ECR** con **tag fijo** (`$(IMAGE_TAG)`, nunca `:latest`); solo CW-agent público fijado | C3; Fargate no admite bind-mount, la DB necesita `init-scripts/` dentro de la imagen |
| Config collector | YAML **horneado en la imagen** del collector (`deploy/collector/otelcol-contrib.dockerfile`) | secretos vía SSM (CW agent), el resto de configuración es versionable |
| Identidad de infra | `resourcedetection/ecs` (task metadata endpoint `169.254.170.2`) | R-10-equivalente: Fargate no tiene IMDS (`http_tokens` es EC2-launch-type) |
| Roles | **`otel-lab-exec-ecs`** (execution) y **`otel-lab-task-ecs`** (task) | creados por el humano en la Fase 0 (este lab no tiene IAM de autómata) |
| Coste | `aws-start`/`aws-stop`/`aws-destroy` | ciclo de coste del PLAN §3.5; guardarraíl `scripts/guard_cost.py` |

Artifactos versionables (todo el punto anterior es **$0 y offline**):

```
deploy/aws/task-definition.json       template con tokens (__AWS_ACCOUNT_ID__, …)
deploy/aws/postgres.Dockerfile        imagen otel-lab/postgres: 16-alpine + init-scripts horneados
deploy/collector/otel-collector-aws.yaml  config ADOT (API awsxray/awsemf/awscloudwatchlogs)
deploy/collector/otelcol-contrib.dockerfile  build del collector ECR
scripts/access.ps1                    grant/revoke de una regla ingress /32 temporal (SIN tunnel)
Makefile (targets aws-*)             ciclo de vida idempotente
```

## 2. Ciclo de vida (checklist del operador)

```powershell
# 0) Validación offline (sin AWS): el binario ADOT no tiene `validate`, así que
#    se valida por ARRANQUE local del contenedor con el mismo YAML tagueado.
make validate-collector

# 1) una sola vez por tag de imagen:
make aws-push                      # ECR login + build + push (4 imágenes)

# 2) encender (red → cluster → task def render+register → service count=1):
make aws-start

# 3) acceso: SSM NO porta-forward a Fargate (§6). El helper abre una regla
#    ingress TEMPORAL TCP /32 limitada a la IP pública del operador y revela
#    la IP pública del ENI de la task (service-a :8000, collector :8889):
make aws-access-grant              # scripts/access.ps1 -Action Grant
#    ... trabajar (smoke, verificación) ...
make aws-verify                    # verify_correlation.py --include-aws contra la IP de la task
#    (equivalente a mano: python scripts/verify_correlation.py
#     --endpoint http://<IP_TASK>:8000 --include-aws
#     --prometheus-url http://<IP_TASK>:8889/metrics)
make aws-access-revoke             # scripts/access.ps1 -Action Revoke (SG vuelve a 0 ingress)

# 4) smoke: POST http://<IP_TASK>:8000/orders (con la regla grant activa)
#    → traza en X-Ray, logs en CloudWatch

# 5) parar (no borra nada; desired_count=0 = sin cómputo):
make aws-stop

# 6) nuclear + verificación:
make aws-destroy                   # borra service+cluster, comprueba list-clusters
make aws-net-destroy               # borra la red tagueada (opcional, $0)
```

## 3. Task definition (5 containers)

| Container | Imagen | Puertos | Función |
|---|---|---|---|
| service-a | ECR `otel-lab/service-a:<tag>` | 8000 | API principal, trazas OTLP → collector |
| service-b | ECR `otel-lab/service-b:<tag>` | 8001 | API de inventario, trazas OTLP → collector |
| postgres | ECR `otel-lab/postgres:<tag>` (`postgres:16-alpine` + `init-scripts/` horneados, `deploy/aws/postgres.Dockerfile`) | 5432 | DB transaccional; `POSTGRES_USER=postgres`, `POSTGRES_DB=orders`, `inventory` creada por el seed (volumen efímero `postgres-data`) |
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

### Contratos verificados en sesión real (fixes ya incorporados al template)

La primera sesión Fargate end-to-end obligó a corregir el template. Estos
contratos quedan fijados por `tests/test_ecs_deliverable.py`:

- **Puertos reales.** `service-a` escucha en **8000** y `service-b` en **8001**
  (lo dictan sus Dockerfiles: `EXPOSE`/`uvicorn --port`); los `containerPort`
  y `hostPort` del template coinciden. El acceso temporal y el smoke usan
  esos puertos.
- **Health checks sobre `/metrics`.** Ningún servicio expone `/health`; ambos
  exponen `/metrics` (FastAPI + Prometheus client), así que el `healthCheck`
  sondea `http://localhost:<puerto>/metrics` con `urllib`.
- **Llamada service-a → service-b.** Con `awsvpc` los contenedores comparten
  namespace de red: no hay DNS de servicio, se llama por **localhost**. La app
  lee `INVENTORY_SERVICE_URL` (default `http://service-b:8001`, válido solo en
  docker-compose), por lo que el template fija
  `INVENTORY_SERVICE_URL=http://localhost:8001`; `SERVICE_B_URL` se conserva
  como referencia documentada del endpoint.
- **ECS Exec no es campo de la task definition.** `enableExecuteCommand` **no**
  es un campo válido de `register-task-definition` (falla la validación). El
  comando del target `aws-start` lo aplica en `create-service`/`update-service`
  con `--enable-execute-command`, que es donde corresponde.
- **Ruta de config del collector.** La imagen hornea el YAML en
  `/etc/otelcol/config.yaml` (`otelcol-contrib.dockerfile`), por lo que el
  `command` del contenedor es `--config=/etc/otelcol/config.yaml` (no
  `file:/cfg/...`, ruta que no existe en la imagen).
- **Notas operativas.** La subnet pública necesita
  `ModifySubnetAttribute --map-public-ip-on-launch true` (permiso de IAM que
  corresponde al humano de la Fase 0). Con `assignPublicIp=ENABLED` el ENI de
  la task **sí** recibe IP pública, pero `ecs describe-tasks` **no** la lista:
  sale en `ec2 describe-network-interfaces → Association.PublicIp` (verificado
  en vivo 2026-10-12; `make aws-verify` la resuelve así).

### Contratos de la segunda sesión (ronda 2, 2026-10-12)

La corrida de correlación destapó cuatro bugs más, ya corregidos y fijados por
tests:

- **Rol/DB de Postgres.** El template creaba `POSTGRES_USER=otel-lab` /
  `POSTGRES_DB=otel`, pero compose + `init-scripts/` usan el rol `postgres`,
  la DB `orders` y crean además `inventory` para service-b. En vivo:
  `FATAL: role "postgres" does not exist` → service-b 500 → service-a 503.
  El template fija ahora `POSTGRES_USER=postgres` / `POSTGRES_DB=orders` y el
  healthCheck `pg_isready -U postgres -d orders`.
- **Imagen de DB propia.** `postgres:16-alpine` pelada no trae
  `init-scripts/` → tablas inexistentes, y Fargate no admite bind-mount.
  Nace `deploy/aws/postgres.Dockerfile` → imagen ECR `otel-lab/postgres` con
  el seed horneado en `/docker-entrypoint-initdb.d/` (contexto de build =
  `init-scripts/`). `make aws-push` construye/pushea **4** imágenes.
- **Valores SSM de las DATABASE_URL.** `/otel-lab/database-url/service-a` =
  `postgresql://postgres:<pw>@localhost:5432/orders` y
  `/otel-lab/database-url/service-b` =
  `postgresql://postgres:<pw>@localhost:5432/inventory` (service-b vive en la
  DB `inventory` que crea `01-init.sh`).
- **Parámetros de `xray:GetTraceSummaries`.** boto3 exige `StartTime`/`EndTime`
  como datetimes de nivel superior; el `TimeRange` anidado se rechaza con
  `ParamValidationError` (la corrida en vivo lo confirmó). Corregido en
  `scripts/verify_correlation.py`, con test que fija los kwargs exactos.

## 4. Coste y verificación

- Los targets `aws-stop` (`desired_count=0`) y `aws-destroy` terminan con
  verificación (describe/list) para demostrar que no queda cómputo
  facturando. El coste de red residual (VPC/subnet/IGW) es ~$0.
- `scripts/guard_cost.py` consulta el **Cost Explorer** (state en S3) y frena
  con aviso si el proyecto supera el presupuesto ($100 AWS, PLAN §3.5).
- Verificación de Fase 2 (CHECKPOINTS): los artefactos listados en §1 existen y
  el template registrable contiene los 5 containers, `FARGATE`, `awsvpc`,
  `cpu:1024`/`memory:2048` y tags fijos (ver `tests/test_ecs_deliverable.py`).

## 5. Acceso al task: por qué NO hay túnel SSM (desviación del PLAN)

El PLAN (§2.B/§13, C4) preveía llegar a los puertos del task con port-forwarding
de SSM Session Manager (`AWS-StartPortForwardingSession*`). En la sesión viva
del 2026-10-12, `aws ssm start-session` **rechazó todos los formatos de target
ECS probados** — `taskId` plano, `ecs:<cluster>:<task>`,
`ecs-<cluster>-<service>-<task>`, `runtimeId`, ARN — con los documentos
`AWS-StartPortForwardingSessionForRemoteSession`, `ForECS` y `ForRemoteHost`:
`"Invalid target ID format"`. La documentación actual de ECS Exec solo ofrece
`execute-command` (shell dentro del contenedor), **no** reenvío de puertos hacia
tasks Fargate. El artefacto `scripts/tunnel.ps1` se retiró.

Sustituto real — `scripts/access.ps1` (targets `aws-access-grant` /
`aws-access-revoke`):

1. **Grant**: autodetecta la IPv4 del operador (`https://api4.ipify.org`) y
   añade al SG `otel-lab-task` una regla ingress TCP **/32** (solo esa IP) para
   los puertos pedidos (default `8000,8889`), marcada con la description
   `otel-lab-temp-session`. Imprime la IP pública del ENI de la task leída de
   `ec2 describe-network-interfaces → Association.PublicIp` (el ENI se resuelve
   desde los details de la task con la clave real `.Value`, mayúscula) y
   persiste los `SecurityGroupRuleId` creados en un archivo de sesión
   (`$env:TEMP\otel-lab-sg-rules-<sg>.txt`) para que Revoke los use.
2. **Verificación**: `make aws-verify` corre el framework `--include-aws`
   contra `http://<IP_TASK>:8000` con `--prometheus-url http://<IP_TASK>:8889/metrics`.
3. **Revoke**: revoca **por `--security-group-rule-ids`** (los ids persistidos
   por Grant + discovery en vivo con `ec2 describe-security-group-rules`,
   prefiltrando `egress=false` + `description=otel-lab-temp-session`); después
   **verifica** con `describe-security-groups` que el SG muestra `IpPermissions`
   **[]** (0 ingress; si no, sale con error). Nota (bug corregido en ronda 3):
   la description NO vive a nivel de `IpPermissions` sino dentro de `IpRanges[]`,
   así que filtrar `IpPermissions[?Description==…]` nunca coincidía y la /32
   sobrevivia a la sesión. El steady-state R-10/§3.2 queda garantizado por la
   verificación post-revoke. La regla vive **solo durante la sesión**; olvidar
   el revoke deja exposición puntual del /32 del operador, no de 0.0.0.0/0.

El shell por ECS Exec (`aws ecs execute-command ... --command "..."`, plugin
`session-manager-plugin` en el PATH) sigue disponible para depuración dentro de
la task; es la única interfaz que SSM sí soporta aquí.

## 6. Pendiente humano (Fase 0) — fuera del alcance de este fichero

- Repos ECR `otel-lab/{service-a,service-b,otel-collector,postgres}` — el de
  postgres se añadió en la ronda 2 (tag **IMMUTABLE** y lifecycle "expire
  untagged 7 d", mismo patrón que service-a/b).
- Roles `otel-lab-exec-ecs` / `otel-lab-task-ecs` (IAM manual).
- SSM `/otel-lab/cloudwatch-agent/config`, `/otel-lab/postgres/password`,
  `/otel-lab/database-url/{service-a,service-b}` (valores en §3 ronda 2) y
  bucket S3 del guard de coste: **esto** es lo que bloquea el encendido en la
  nube, no el código.