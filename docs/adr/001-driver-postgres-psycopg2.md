# ADR-001: Driver PostgreSQL — psycopg2-binary sobre SQLAlchemy 2.0

## Estado

Aceptado (2026-10-09; fijado en el primer commit de la Fase 1 y vigente).

## Contexto

Los dos servicios acceden a PostgreSQL 16 vía SQLAlchemy 2.0 con FastAPI +
uvicorn (stack **síncrono**: las rutas de negocio corren en el threadpool de
Starlette). El instrumentador de OTel para bases de datos en el stack elegido es
`opentelemetry-instrumentation-psycopg2` (junto con la de SQLAlchemy). El PLAN
fija Python 3.12 y pins exactos sin floats.

## Opciones consideradas

- **psycopg2-binary** — empaquetado binario del driver clásico; soporte oficial
  del instrumentador psycopg2; cero compilación en el Dockerfile.
- `psycopg[binary]` (v3) — API moderna, pero la instrumentación nativa de OTel
  para v3 estaba menos madura en el momento de la fijación y obligaría a
  reescribir engine/queue de SQLAlchemy.
- `asyncpg` + SQLAlchemy async — cambiaría el modelo de concurrencia de toda la
  app (fuera del alcance del laboratorio).

## Decisión

`psycopg2-binary==2.9.13` con `SQLAlchemy==2.0.54` e
`opentelemetry-instrumentation-psycopg2==0.66b1` (pins visibles en
`service-a/requirements.txt` y `service-b/requirements.txt`).

## Consecuencias

- Los spans `INSERT orders` / `SELECT inventory` / `UPDATE inventory` salen
  automáticamente en la trazabilidad (verificados en el waterfall de
  `docs/evidence/04-jaeger-gcp.md`).
- El binario aumenta el tamaño de imagen (~10 MB) — irrelevante frente al
  coste de las imágenes de app.
- La v3 de psycopg queda como migración futura natural si el instrumentador
  se estabiliza; no hay deuda oculta porque el driver está aislado en la capa
  de datos.

## Evidencia

- `service-a/requirements.txt`, `service-a/app/db.py` (engine + pool).
- Waterfall con spans de DB: `docs/evidence/04-jaeger-gcp.md`.
- Aserciones A5-A7 (consistencia traza↔log↔métrica con `db.system`):
  `docs/evidence/03-correlation.md`.
