# CloudWatch Logs: grupos del driver (ecs-platform/*) y del exportador
# (ecs-cluster/*) con retencion 3 dias (PLAN §3.4 C3 / §3.5 "higiene de
# datos": el presupuesto de 5 GB/mes se protege con volumen + retencion).
# La task def usa `awslogs-create-group=true`; tener el grupo gestionado aqui
# garantiza la retencion aunque ECS lo cree primero.

locals {
  log_group_names = toset([
    "/otel/labs/ecs-platform/service-a",
    "/otel/labs/ecs-platform/service-b",
    "/otel/labs/ecs-platform/postgres",
    "/otel/labs/ecs-platform/otel-collector",
    "/otel/labs/ecs-platform/cloudwatch-agent",
    "/otel/labs/ecs-cluster/service-a",
    "/otel/labs/ecs-cluster/service-b",
  ])
}

resource "aws_cloudwatch_log_group" "otel_lab" {
  for_each = local.log_group_names

  name              = each.value
  retention_in_days = 3

  tags = { Name = replace(each.value, "/", "-") }
}
