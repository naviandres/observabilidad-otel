# otel-lab/postgres — postgres:16-alpine con el seed del lab HORNEADO.
#
# Por que una imagen propia: Fargate NO admite bind-mounts de carpetas del
# host, y la imagen publica de postgres trae /docker-entrypoint-initdb.d
# vacio, asi que la task arrancaba sin tablas (sesion viva 2026-10-12).
# Copiar init-scripts/ dentro de la imagen reproduce el comportamiento del
# compose local: el entrypoint ejecuta los scripts en un data directory
# nuevo (volumen efimero por task), creando orders/order_items + inventory.
#
# Build context: init-scripts/ (los 3 scripts; ver target `aws-push`):
#   docker build -t <ECR_REGISTRY>/otel-lab/postgres:<tag> \
#     -f deploy/aws/postgres.Dockerfile init-scripts
# Repo ECR otel-lab/postgres con tag INMUTABLE (mismo patron que
# otel-lab/service-a y otel-lab/service-b, docs/evidence/00-aws-baseline.md).
FROM public.ecr.aws/docker/library/postgres:16-alpine

# 01-init.sh + 01-orders-schema.sql + 02-inventory-db.sql: orden alfabetico
# idempotente (mismo directorio que monta docker-compose.yml).
COPY . /docker-entrypoint-initdb.d/
