# ADR-004: Sustrato de cómputo GCP — GKE Standard zonal con node pool spot

**Proveedor: GCP.** Status: aceptado (decisión D2 del PLAN; desplegado y
validado en vivo el 2026-10-11).

## Contexto

El par GCP del pipeline debe correr con presupuesto de créditos ($300, ventana
de 90 días) y lista negra propia (vetados: GKE Autopilot, Cloud NAT, Cloud Load
Balancing gestionado permanente, Cloud SQL, Cloud Run con min-instances > 0,
node pools **no-spot**). El enunciado pide el mismo código desplegado en ambas
nubes, con acceso sin ingress público.

## Opciones consideradas

| Opción | Coste/riesgo | Veredicto |
|---|---|---|
| **GKE Standard zonal + pool spot** | fee de plano **$0.10/h** (el free tier GKE cubre 1 cluster zonal: 744 h/mes) + nodo spot marginal | **Elegida** |
| GKE Autopilot | cobra por pod-reservation 24/7 y no permite pools spot | Vetada por lista negra |
| Cloud Run | min-instances > 0 facturable permanente; sin sidecars al estilo del lab | Vetada |
| GKE Regional | fee de plano ~2× | Descartada (el lab no requiere HA) |
| Compute Engine VM directa | reinventaría el orquestador que el enunciado pide demostrar | Descartada |

## Decisión

Cluster **zonal** (`us-central1-a` por defecto, con reubicación de zona según
capacidad spot — ver ADR-005), release channel `regular`, node pool **spot**
con autoscaling **0-3** (0 nodos en reposo), Workload Identity
(`--workload-pool`) para el SA del collector, Private Google Access en la
subnet (egress a APIs de Google sin Cloud NAT), y todos los Services en
ClusterIP — el único LB (regional, service-b) existe solo durante el modo dual
y muere con el cluster. Los workloads se despliegan vía **Helm**
(`helm/otel-lab`) desde Terraform (provider `helm`) y desde el Makefile
(`make deploy-gcp`).

## Consecuencias

- El **fee de plano domina el coste de sesión GCP** ($0.10/h desde el create
  hasta el delete, aunque el pool esté a 0): por eso `make gcp-destroy` — no
  `gcp-stop` — es el DoD entre bloques de trabajo, y cada create/delete se
  registra en `docs/evidence/cluster-hours.csv`.
- Workload Identity elimina las claves JSON de los pods: cero secretos de SA en
  manifiestos (binding verificado en `docs/evidence/01-wi-binding-gcp.md`).
- La paridad con AWS es de **fases**, no de recursos: ECS Exec ↔ port-forward,
  SSM ↔ Secret Manager, X-Ray ↔ Cloud Trace, EMF ↔ Cloud Monitoring.
- Spot implica preemption: aceptada porque el lab tolera re-creaciones rápidas
  del cluster + workloads (`make gcp-start` + `make deploy-gcp`, idempotente
  vía `helm upgrade --install`).

## Evidencia

- `Makefile` (`gcp-start`: flags verificadas `--workload-pool`,
  `--enable-private-ip-google-access`, `--spot`, `--min-nodes=0 --max-nodes=3`).
- `helm/otel-lab/` (5 workloads, ClusterIP-only, WI annotations,
  SecretProviderClass).
- `docs/evidence/02-validation-dashboard-gcp.md` (smoke 12×200 + fault
  injection en el cluster real), `docs/evidence/04-jaeger-gcp.md` (trazas
  navegables en-cluster).
- `docs/evidence/cluster-hours.csv` (sesiones reales: fee, zonas, perfiles).
- Línea base free tier/cuotas: `docs/evidence/00-free-tier-baseline-gcp.md`.
