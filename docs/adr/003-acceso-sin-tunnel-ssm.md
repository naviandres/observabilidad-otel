# ADR-003: Acceso a la task Fargate — regla /32 temporal porque el túnel SSM a tasks no existe

**Proveedor: AWS.** Status: aceptado (desviación verificada en vivo el
2026-10-12; sustituye el mecanismo previsto en el PLAN §2.B/C4).

## Contexto

El diseño original (PLAN §2.B, C4) preveía llegar a los puertos de la task con
**port-forwarding de SSM Session Manager** (`AWS-StartPortForwardingSession*`),
lo que habría permitido acceso sin exponer nada en la red. El requisito
durable es otro: el SG de la task debe quedar **sin ingress permanente**
(decisión D5 / lista negra: ni ALB, ni Service Connect, ni EIP 24/7).

## Opciones consideradas

- **SSM port-forwarding a la task** — rechazado empíricamente: `aws
  start-session` respondió `"Invalid target ID format"` con **todos** los
  formatos de target ECS probados (`taskId` plano, `ecs:<cluster>:<task>`,
  `ecs-<cluster>-<service>-<task>`, `runtimeId`, ARN) y con los tres documentos
  relevantes (`AWS-StartPortForwardingSessionForRemoteSession`, `…ForECS`,
  `…ForRemoteHost`). ECS Exec (`execute-command`) sí funciona, pero solo da
  **shell dentro del contenedor**, no reenvío de puertos. El artefacto
  `scripts/tunnel.ps1` se retiró del repo.
- **ALB + SG** — vetado por lista negra de coste.
- **Regla ingress temporal /32 limitada a la IP pública del operador** — coste
  $0, reversible, auditable; el SG vuelve a 0 reglas al revocar. **Elegida.**

## Decisión

`scripts/access.ps1` (targets `make aws-access-grant` / `aws-access-revoke`):

1. **Grant:** autodetecta la IPv4 del operador (`api4.ipify.org`), añade una
   regla TCP /32 (puertos 8000 y 8889) marcada `otel-lab-temp-session`, y revela
   la IP pública del ENI de la task — legible solo en
   `ec2 describe-network-interfaces → Association.PublicIp` (no aparece en
   `ecs describe-tasks`).
2. **Revoke:** determinista y simétrico, por `--ip-permissions`
   (protocolo/puerto/CIDR); la IP se lee del archivo de sesión creado por el
   grant. Como mejor esfuerzo se intenta también el revoke por
   `SecurityGroupRuleId`.
3. **Verificación:** tras el revoke, `describe-security-groups` debe mostrar
   `IpPermissions []`; si queda cualquier regla, el script falla.

## Consecuencias

- El steady-state del SG (0 ingress) se **verifica**, no se afirma; la ventana
  de exposición es solo el /32 del operador durante la sesión.
- `make aws-verify` (y el runbook de `docs/aws-ecs.md` §5) dependen de la IP
  pública del ENI; con aws-cli ≥2.32/botocore ≥1.43 los `details` de los
  attachments salen en camelCase y el JMESPath `.Value` falla — el camino real
  es resolver la IP con boto3 o `describe-network-interfaces` (documentado).
- El shell de depuración sigue disponible por ECS Exec; es la única interfaz
  que SSM sí soporta sobre Fargate en este laboratorio.

## Evidencia

- `docs/aws-ecs.md` §5 (los rechazos de `start-session`, formatos probados).
- `scripts/access.ps1` (grant/revoke + verificación `IpPermissions []`).
- `docs/evidence/03-correlation.md`: "revoke del grant /32 confirmado
  (`IpPermissions []`)" tras la mini-sesión del 2026-10-12.
