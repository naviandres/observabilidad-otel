# ADR-002: Sustrato de cómputo AWS — ECS Fargate con una única task de 5 contenedores

**Proveedor: AWS.** Status: aceptado (decisión de diseño del PLAN; contratos
corregidos contra sesión real el 2026-10-12).

## Contexto

El enunciado de la actividad **exige ECS Fargate** como cómputo AWS. El lab
necesita ejecutar 5 procesos (service-a, service-b, postgres, collector ADOT y
sidecar cloudwatch-agent) dentro de un presupuesto de créditos ($100) con
reglas duras de lista negra (vetados: EKS, ALB, NAT Gateway, RDS, Secrets
Manager, Elastic IPs permanentes). Fargate cobra por **task** (vCPU-hora +
GB-hora), no por contenedor.

## Opciones consideradas

| Opción | Coste/riesgo | Veredicto |
|---|---|---|
| **Fargate, 1 task · 5 containers** (awsvpc) | 1 × perfil 1 vCPU/2 GB ≈ **$0.04928/h** medido; $0 con `desired_count=0` | **Elegida** |
| Fargate, 4 tasks (un servicio por task) | ~4× el coste de cómputo; complejidad de red entre tasks | Descartada |
| EC2 sobre ECS | nodo 24/7 facturable aunque la sesión dormía; parcheo de SO | Descartada |
| EKS | en lista negra (coste de control plane + complejidad) | Vetada |
| App Runner / Lightsail | en lista negra | Vetadas |

## Decisión

Una única task Fargate (`cpu: "1024"`, `memory: "2048"`, `awsvpc`) con los 5
contenedores; `assignPublicIp=ENABLED` efímero; sin ALB ni NAT — la subnet
pública saca el egress por IGW. Las reservas por contenedor caben en el perfil
(CPU 4×256 = 1024; memoria 384+384+512+512+128 = 1920 < 2048). El ciclo de
coste lo gobierna el Makefile (`aws-start`/`aws-stop`/`aws-destroy` con
verificación de teardown); Terraform describe la infraestructura permanente.

## Consecuencias

- **Positivas:** coste por segundo con botón de apagado real; un solo
  despliegue; paridad de fases con el lado GCP; la app comparte namespace de
  red (service-a llama a service-b por `localhost:8001`).
- **Negativas / contrapartidas:** Fargate no admite bind-mount → el seed de
  postgres y el YAML del collector se **hornean en sus imágenes**
  (`deploy/aws/postgres.Dockerfile`, `deploy/collector/otelcol-contrib.dockerfile`);
  el driver de logs es `awslogs` únicamente (json-file no soportado); sin IMDS
  → identidad de infra por el endpoint de metadata de task (`resourcedetection/ecs`);
  `enableExecuteCommand` vive en el service, no en la task definition
  (contrato verificado en vivo).

## Evidencia

- `deploy/aws/task-definition.json` (cpu/memory, 5 containers, puertos reales
  8000/8001 verificados — los 8080/8081 iniciales fueron un bug corregido en
  la primera sesión).
- `docs/aws-ecs.md` §1-§3 (contratos verificados en sesión real).
- `docs/evidence/02-aws-ecs-session.md` (task RUNNING, X-Ray con segmentos de
  ambos servicios, teardown verificado).
- Horas y coste reales: `docs/evidence/instance-hours.csv` (p. ej. 0.55 h ≈
  $0.027; tarifa $0.04038 vCPU-h + $0.00445/GB-h en us-east-1).
- `tests/test_ecs_deliverable.py` (contratos fijados por test).
