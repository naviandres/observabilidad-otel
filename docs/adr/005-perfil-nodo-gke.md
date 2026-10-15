# ADR-005: Perfil de nodo GKE — escalada e2-small → e2-medium y add-on CSI en el create

**Proveedor: GCP.** Status: aceptado (2026-10-11, tras dos sesiones de nube que
destaparon ambas restricciones; el registro de horas es la prueba).

## Contexto

El diseño (PLAN §5.A) fijaba el node pool spot en `e2-small` (2 GiB) — el
perfil más barato que cabe en créditos. Dos hechos verificados en vivo lo
rompieron:

1. **GKE ≥ 1.36 no cabe en e2-small.** Los *system pods* del cluster consumen
   ~856m de CPU (y rondan los 940m de memoria) sobre un nodo de 2 vCPU
   allocables; con los requests del stack, los pods del lab quedaban `Pending`
   con `Insufficient cpu`. Observado el 2026-10-11 durante la validación del
   dashboard: el primer cluster (us-central1-a, e2-small) tuvo que destruirse a
   los ~20 min sin llegar a validar.
2. **El add-on CSI de Secret Manager no es barato de añadir después.**
   `gcloud container clusters update --enable-secret-manager` sobre un cluster
   existente tardó ~40 min en `UPGRADE_NODES` (ronda 1 del 2026-10-11).
   Habilitado **en el create** (`--enable-secret-manager`), está disponible de
   inmediato.

Además, la **capacidad spot es contingente**: un `create` falló por falta de
capacidad spot en us-central1-a (no facturable); las sesiones posteriores se
reubicaron a us-central1-c cuando hizo falta.

## Opciones consideradas

- `e2-small` + requests mínimos — imposible: el overhead de sistema de GKE
  1.36 ya consume el nodo (no es un problema de las requests del lab).
- Varios nodos `e2-small` (autoscaling) — funciona pero multiplica el coste
  sin necesidad: un solo `e2-medium` (4 GiB) aloja el stack completo.
- **`e2-medium` spot** — ~2× el precio de e2-small pero sigue siendo spot
  (~$0.012/h por nodo medido); el fee de plano (dominante) no cambia.
  **Elegida.**
- Nodos on-demand — vetados por lista negra (pools no-spot).

## Decisión

Perfil **`e2-medium` spot** con autoscaling 0-3 en el `gcp-start` del Makefile
y como default de Terraform (`infra/gcp/cluster.tf`); el chart fija requests de
**20m/pod** en los 5 componentes (limits intactos) como higiene adicional de
cabida. El add-on CSI se habilita **exclusivamente en el create**; el camino
post-create queda documentado como penalización (~40 min) para quien herede un
cluster sin él.

## Consecuencias

- El PLAN literal ("node pool spot `e2-small`") queda desviado **dentro de lo
  que el propio plan prevé** (riesgo R-08: escalado de perfil si el sistema lo
  exige), manteniendo el descuento spot.
- El coste marginal de sesión sube de ~$0.006/h a ~$0.012/h por nodo — frente
  al fee de plano ($0.10/h), es ruido: todas las sesiones cerraron ≤ ~$1.6.
- Queda documentado como "dos caminos" (IaC default e2-medium; Makefile legado
  e2-small corregido a e2-medium) para evitar deriva entre artefactos.

## Evidencia

- `docs/evidence/cluster-hours.csv`: fila del create e2-small en us-central1-a
  con "delete al detectar Insufficient cpu del system GKE 1.36"; re-creación
  en zona C con e2-medium y add-on en create; "create fallido por capacidad
  spot … no facturable".
- `Makefile` (`gcp-start`: `--machine-type=e2-medium`, comentario de perfil con
  la cifra ~856m; `--enable-secret-manager` en el create con nota del update de
  40 min).
- `helm/otel-lab/values.yaml` (requests 20m fijados en el chart).
- Sesión de correlación del 2026-10-11: dos recreaciones (zona A sin capacidad
  → zona C) hasta el PASS de A1/A2/A10/A11/A12.
