# docs/adr — registro de decisiones arquitectónicas

Formato MADR (Contexto / Opciones / Decisión / Consecuencias / Evidencia).
Cada ADR se escribió **después** de la verificación en vivo que lo motiva; la
sección "Evidencia" enlaza el artefacto o registro que lo prueba.

| # | Título | Nube | Estado |
|---|---|---|---|
| [001](001-driver-postgres-psycopg2.md) | Driver PostgreSQL: psycopg2-binary sobre SQLAlchemy 2.0 | — | Aceptado |
| [002](002-computo-aws-ecs-fargate.md) | Cómputo AWS: ECS Fargate, una task de 5 contenedores | **AWS** | Aceptado |
| [003](003-acceso-sin-tunnel-ssm.md) | Acceso a la task: regla /32 temporal (el túnel SSM a Fargate no existe) | **AWS** | Aceptado |
| [004](004-computo-gcp-gke-standard-spot.md) | Cómputo GCP: GKE Standard zonal + pool spot | **GCP** | Aceptado |
| [005](005-perfil-nodo-gke.md) | Perfil de nodo: e2-small → e2-medium; add-on CSI en el create | **GCP** | Aceptado |
| [006](006-colector-dual-adot-contrib.md) | Collector dual: ADOT v0.46.0 + contrib 0.162.0 | AWS+GCP | Aceptado |
| [007](007-exemplars-app-level.md) | Exemplars métrica→traza a nivel de aplicación (A8) | AWS+GCP | Aceptado |
| [008](008-modelo-de-coste-creditos-y-guardarrailes.md) | Modelo de coste: créditos + guardarraíles + teardown DoD | AWS+GCP | Aceptado |
| [009](009-sampling-y-trazas-off-benchmark.md) | Benchmark: sampling 0.25 + trazas gestionadas OFF (C2) | AWS+GCP | Aceptado |
| [010](010-dashboards-nativos-bajo-limites-api.md) | Dashboards nativos bajo límites reales de API | AWS+GCP | Aceptado |

Convención: un ADR no se edita tras ser superado — se escribe uno nuevo que
lo referencie (el historial vive en el control de versiones).
