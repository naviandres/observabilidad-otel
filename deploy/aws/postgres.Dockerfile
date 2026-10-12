# otel-lab/postgres — postgres:16-alpine con el seed del lab HORNEADO.
#
# Por que una imagen propia: Fargate NO admite bind-mounts de carpetas del
# host, y la imagen publica de postgres trae /docker-entrypoint-initdb.d
# vacio, asi que la task arrancaba sin tablas (sesion viva 2026-10-12).
#
# BUILD CONTEXT = RAIZ DEL REPO (no init-scripts/): la sesion viva demostro
# que `COPY .` con contexto raiz montaba TODO el repo en
# /docker-entrypoint-initdb.d (solo DIRECTORIOS de primer nivel, ningun
# .sh/.sql) → el entrypoint nunca ejecutaba el seed → `psql -l` solo DB
# `orders` → service-b FATAL "database inventory does not exist" → 500 →
# A2 FAIL en vivo. Fix: COPY con rutas EXPLICITAS desde la raiz, de modo
# que el contenido de /docker-entrypoint-initdb.d sea exactamente los
# 3 scripts del seed y el Dockerfile sea invariante al contexto.
#
# Build (ver target `aws-push` en el Makefile):
#   docker build -t <ECR_REGISTRY>/otel-lab/postgres:<tag> \
#     -f deploy/aws/postgres.Dockerfile .
#
# Verificacion obligatoria tras cada build (solo 3 archivos):
#   docker run --rm --entrypoint ls <IMG> -la /docker-entrypoint-initdb.d
#
# Repo ECR otel-lab/postgres con tag INMUTABLE (mismo patron que
# otel-lab/service-a y otel-lab/service-b, docs/evidence/00-aws-baseline.md).
# La raiz lleva .dockerignore para que el contexto no arrastre basura
# (.git/.venv/__pycache__) hacia la capa de build.
FROM public.ecr.aws/docker/library/postgres:16-alpine

# 01-init.sh + 01-orders-schema.sql + 02-inventory-db.sql: orden alfabetico
# idempotente (mismo directorio que monta docker-compose.yml). Una sola linea
# COPY para que el contrato de test (ruta explicita por script) sea trivial.
COPY init-scripts/01-init.sh init-scripts/01-orders-schema.sql init-scripts/02-inventory-db.sql /docker-entrypoint-initdb.d/

# El checkout Windows no porta el bit de ejecucion; el entrypoint de postgres
# ejecuta los .sh con `.` si no son ejecutables, pero el chmod elimina la
# duda y fija el comportamiento identico al compose local.
RUN chmod +x /docker-entrypoint-initdb.d/01-init.sh
