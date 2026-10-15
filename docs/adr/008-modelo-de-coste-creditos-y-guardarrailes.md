# ADR-008: Modelo de coste — operar dentro de créditos con guardarraíles, no "$0/mes"

**Proveedores: AWS + GCP.** Status: aceptado (2026-10-11; los hallazgos de
billing y presupuesto se verificaron al montar la Fase 0 en cada nube).

## Contexto

El laboratorio dispone de **$100 de créditos AWS + $300 de créditos GCP**
(ventana de 90 días; dato declarado por el operador con fecha, porque no hay
API listable de saldo). Un diseño "free tier estricto" sería falso: Fargate,
el fee de plano GKE y la ingestión de señales **se pagan de créditos**. Lo que
sí es negociable es operar dentro del presupuesto con alertas, topes y
teardown obligatorio. Dos hechos reales de billing condicionaron el modelo:

1. **Consolidated billing (AWS).** La cuenta del lab (`621236283643`) es
   **miembro** de una organización cuyo pagador (`070443470565`) concentra los
   créditos promocionales (~$120). La miembro **no ve créditos propios** — es
   comportamiento esperado, no una incidencia. Los budgets por-cuenta son
   proxy; el techo real es el pool del pagador. Queda como paso humano un
   budget a nivel de pagador (no gestionable desde la miembro).
2. **Créditos y moneda del budget (GCP).** El budget GCP se fijó en **COP
   964 461** (≈ USD 300 al cambio aplicable) con `EXCLUDE_ALL_CREDITS` — sin
   esa exclusión las alertas se disparan sobre el gasto *neto de créditos* y
   el guardarraíl queda ciego. La combinación moneda/créditos tuvo que
   corregirse **vía API** tras el error inicial de la consola/CLI.

## Decisión

- **Teardown como DoD**: la sesión de nube (task Fargate / cluster GKE) se
  enciende para trabajar y se **destruye** al terminar, con verificación
  (`aws-destroy`/`gcp-destroy` comprueban que el recurso no aparece). El ciclo
  diario de desarrollo es local ($0).
- **Guardarraíles en capas**: AWS — budget diario $1 (alertas 80/100 %),
  budget mensual $100, Cost Anomaly Monitor por servicio, gate de 150 h de
  task (`make budget-hours` + `docs/evidence/instance-hours.csv`); GCP —
  budget COP 964 461 con umbrales 50/80/90/100/150 % y registro de horas en
  `cluster-hours.csv`. Ambos — `guard_cost.py` sobre el JSON del plan (lista
  negra por proveedor, PR-blocking) y `cost_guard.ps1` diario (Cost Explorer +
  barrido de recursos vivos, tolerante a AccessDenied).
- **Presupuesto por diseño, no por sorpresa**: ninguna sesión se aprueba sin
  estimación previa y sin registro posterior de coste real.

## Consecuencias

- El fee de plano GKE ($0.10/h) corre **desde el create hasta el delete**:
  `gcp-stop` (pool a 0) NO lo detiene. La lección costó dinero real: la sesión
  de correlación del 2026-10-12 pausó 9.2 h con nubes encendidas y cerró en
  **~$2.27**, por encima del criterio "<1 USD" — se documentó como
  **disclosure** en `docs/evidence/03-correlation.md` para que la validación
  humana fuera informada, y la regla operativa quedó: *delete entre bloques,
  no stop*.
- Los budgets por-cuenta AWS son **proxy** del techo real (pool del pagador):
  el modelo de coste lo declara explícitamente en COST.md en vez de fingir
  precisión.
- El presupuesto hace de **diseñador**: las vetadas de lista negra (NAT, ALB,
  Autopilot, Cloud SQL…) empujaron las decisiones de topología (ADR-002,
  ADR-004) y el apagado de trazas gestionadas en el benchmark (ADR-009).

## Evidencia

- `docs/evidence/00-aws-baseline.md` (budgets CLI, anomaly monitor, nota de
  consolidated billing; SCPs del propio org como causa del "sandbox").
- `docs/evidence/00-free-tier-baseline-gcp.md` (budget y corrección
  EXCLUDE_ALL_CREDITS; créditos $300 con 31 días restantes como dato
  declarado).
- `docs/evidence/cluster-hours.csv` L15 ("leccion: apagar (delete) entre
  bloques, gcp-stop (resize 0) NO detiene el fee del plano").
- `COST.md` §1-§4 (modelo completo, listas, guardarraíles, apagado).
- `infra/aws/budget.tf` + `infra/gcp/budget.tf` (budgets como IaC).
