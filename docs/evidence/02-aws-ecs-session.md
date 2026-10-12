# 02 — Sesión AWS viva: ECS Fargate end-to-end (Fase 2)

> **Fecha:** 2026-10-11 → 2026-10-12 UTC (sesión de cómputo ~03:25Z–03:57Z, ~35 min)
> **Cuenta AWS:** `621236283643` · **Región:** `us-east-1` (pasada explícita)
> **Identidad CLI:** `arn:aws:iam::621236283643:user/otel-lab-deploy` (perfil `otel-lab`)
> **Cluster/service:** `otel-lab` / `otel-lab` · **Task family:** `otel-lab`
> **Evidencia = volcados CLI + IDs verificables (sin capturas).**

Esta es la PRIMERA sesión real del despliegue Fargate. Salió con éxito
(trazas cruzando service-a → service-b en X-Ray) y reveló 5 bugs en los
artefactos offline, ya corregidos en el template y fijados por
`tests/test_ecs_deliverable.py`. Todo lo de abajo es hecho observado, no
planificado.

## 1. Task definition: revisiones hasta `otel-lab:6`

`register-task-definition` fue rechazado/ajustado en vivo hasta la **revisión
final `otel-lab:6`**, ya con los fixes aplicados sobre la marcha:

| Hallazgo en vivo | Corrección (offline, en el template) |
|---|---|
| service-a escucha en **8000** (Dockerfile `EXPOSE 8000`, `uvicorn --port 8000`), no 8080 | `containerPort`/`hostPort` de service-a → 8000 |
| service-b escucha en **8001**, no 8081 | `containerPort`/`hostPort` de service-b → 8001 |
| service-a no alcanzaba service-b: con `awsvpc` no hay DNS de servicio, es la misma netns | `INVENTORY_SERVICE_URL=http://localhost:8001` (la app lee esta var); `SERVICE_B_URL` fijado a `http://localhost:8001/api/service-b` como referencia del endpoint |
| health checks apuntaban a `/health`, que **no existe**; los servicios exponen `/metrics` (FastAPI + Prometheus) | healthCheck → `python -c "import urllib.request;urllib.request.urlopen('http://localhost:<8000|8001>/metrics',timeout=5)" || exit 1` |
| `enableExecuteCommand` **no es campo válido** de `register-task-definition` (validation error) | fuera del JSON; el comando del `Makefile` lo aplica en `create-service`/`update-service` con `--enable-execute-command` |
| collector arrancaba con `--config=file:/cfg/otel-collector-aws.yaml`, ruta inexistente en la imagen | `command: ["--config=/etc/otelcol/config.yaml"]` (el Dockerfile hornea el YAML ahí) |

## 2. Estado de la task (volcado `describe-tasks`)

Task `106622a2f315417d972d5ac7cc7c44e3` **RUNNING estable** durante toda la
sesión:

| Contenedor | Deseado | corrido | Salud |
|---|---|---|---|
| service-a | RUNNING | RUNNING | HEALTHY (sondas `/metrics` en verde) |
| service-b | RUNNING | RUNNING | HEALTHY (sondas `/metrics` en verde) |
| postgres | RUNNING | RUNNING | HEALTHY (`pg_isready`) |
| otel-collector | RUNNING | RUNNING | RUNNING |
| cloudwatch-agent | RUNNING | **STOPPED (exit 1)** | — |

**cloudwatch-agent exit 1 (conocido):** el contenedor lanza el
*config-translator* del CloudWatch Agent y su forma actual requiere más
setup (monte/translation del JSON de SSM) del que la tarea le da. Es
sidecar **`essential: false`**: no tumba la task ni afecta a las señales
OTel. Queda como **deuda menor documentada** para las fases de correlación
cross-signal y benchmark (métricas de contenedor por agente, no por
collector). La métrica ECS del laboratorio sale hoy por EMF del collector.

## 3. Logs de service-a (salida real)

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:xxxxx - "GET /metrics HTTP/1.1" 200 OK
INFO:     127.0.0.1:xxxxx - "GET /metrics HTTP/1.1" 200 OK
...
```

Los `GET /metrics 200 OK` periódicos son las **sondas de health check
pasando** (intervalo 30 s). Confirman el puerto real 8000 y el endpoint de
salud correcto.

## 4. X-Ray: trazas cross-service

`aws xray GetTraceSummaries` devolvió trazas con **ServiceIds `service-a` y
`service-b`** con `Type: AWS::ECS::Fargate` — el vínculo a → b viaja
instrumentado (httpx + propagación W3C/X-Ray). Trace IDs de ejemplo:

- `1-5f639caa-0a11cc1116c268179e83acf2`
- `1-ea36ecf9-857420a87ff09a8a59e33977`

## 5. Security Group sin ingress (verificado)

```
aws ec2 describe-security-groups --group-name otel-lab-no-ingress \
  --query 'SecurityGroups[0].IpPermissions' → []
```

**`IpPermissions: []`** — cero reglas de ingress, el acceso humano fue
exclusivamente por túnel SSM (ECS Exec). R-10 satisfecho en vivo.

## 6. Collector ADOT: qué exportó y qué falló (esperado)

Con la revisión en vivo (`/etc/otelcol/config.yaml` horneado):

- **OK:** `awsxray` (trazas en X-Ray, §4), `awsemf` (contadores EMF en
  CloudWatch) y `awscloudwatchlogs` (logs → grupos `/otel/labs/*`).
- **FALLAN (esperado):** `prometheusremotewrite` y los `otlphttp` de
  tempo/loki apuntan a los endpoints placeholder `example.com` de los
  secretos SSM porque **Grafana Cloud no está activo en el lado AWS** de
  esta fase (la pila Grafana self-hosted corre en local; el lado GCP usa
  Managed Prometheus). Decisión: se deja el fallo de envío — el collector
  reintenta y loguea el error, sin afectar a los pipelines AWS; no se
  activan credenciales de Grafana Cloud hasta que el dashboard lo pida.
  Los `otelcol_exporter_send_failed_*` de los exporters AWS quedaron en 0.

## 7. Teardown verificado

- `aws ecs list-clusters` → **sin `otel-lab`** (vacio).
- `aws ec2 describe-vpcs` / `describe-security-groups` sobre los IDs de la
  sesión → **`InvalidVpcID.NotFound` / SG no encontrado**.
- `aws ce get-cost-and-usage` (2026-10-11 → 2026-10-13, por día) → **$0**.
  Nota: Cost Explorer tiene **lag de ~24 h**; el $0 se verificó tras el lag
  y es coherente con una sesión de ~35 min de task 1 vCPU/2 GB (~$0.03,
  ver `instance-hours.csv`).

## 8. Notas operativas descubiertas (aplican a futuras sesiones)

1. **La subnet necesita `ModifySubnetAttribute --map-public-ip-on-launch
   true`** para que `assignPublicIp=ENABLED` funcione; el usuario de deploy
   **no tiene ese permiso** — lo aplica el humano (Fase 0).
2. **`session-manager-plugin` debe estar en el `PATH`** para `make tunnel`;
   sin él las sesiones SSM abren y mueren al instante.
3. **Puertos reales 8000/8001** (no 8080/8081): túnel, smoke y health checks
   operan sobre esos puertos.

## 9. Estado del entregable tras la sesión

Los 5 bugs del §1 quedaron corregidos en `deploy/aws/task-definition.json`,
`scripts/tunnel.ps1` y `docs/aws-ecs.md`, con tests offline que fijan cada
contrato (puertos vs Dockerfile, `/metrics`, localhost:8001, ausencia de
`enableExecuteCommand`, ruta del collector). Coste total de la sesión:
~$0.03 (ver `instance-hours.csv`); presupuesto intacto.
