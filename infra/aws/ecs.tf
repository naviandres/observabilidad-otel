# ECS Fargate: cluster + service con los 5 contenedores de la task def
# canonica (`deploy/aws/task-definition.json`). La task def se materializa
# desde ese JSON (unica fuente de verdad: mismo shape que usa `make deploy`)
# reemplazando los tokens de build (region, cuenta, registro, tag, URL interna).
# `desired_count` arranca en 0 (reposo, sin coste); el ciclo lo sube a 1.

locals {
  ecr_registry = "${local.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"

  task_def_doc = jsondecode(
    replace(
      replace(
        replace(
          replace(
            replace(
              file("${path.module}/../../deploy/aws/task-definition.json"),
              "__AWS_REGION__", var.aws_region
            ),
            "__AWS_ACCOUNT_ID__", local.account_id
          ),
          "__ECR_REGISTRY__", local.ecr_registry
        ),
        "__IMAGE_TAG__", var.image_tag
      ),
      "__INVENTORY_URL__", "http://localhost:8001/api/service-b"
    )
  )
}

resource "aws_ecs_cluster" "otel_lab" {
  name = local.is_lab ? "otel-lab" : "otel-bench"

  tags = { Name = "otel-lab" }
}

resource "aws_ecs_cluster_capacity_providers" "otel_lab" {
  cluster_name       = aws_ecs_cluster.otel_lab.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

resource "aws_ecs_task_definition" "otel_lab" {
  family                   = "otel-lab"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_cpu
  memory                   = var.ecs_memory
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "postgres-data" # efimero: no sobrevive al stop del task
  }

  container_definitions = jsonencode(local.task_def_doc.containerDefinitions)

  tags = { Name = "otel-lab" }
}

resource "aws_ecs_service" "otel_lab" {
  name            = "otel-lab"
  cluster         = aws_ecs_cluster.otel_lab.id
  task_definition = aws_ecs_task_definition.otel_lab.arn
  desired_count   = var.service_desired_count

  # ECS Exec (depuracion por SSM, PLAN §3.3): sin ALB, sin ingresos permanentes.
  enable_execute_command = true

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }

  network_configuration {
    subnets          = [aws_subnet.otel_lab_public.id]
    security_groups  = [aws_security_group.otel_lab_task.id]
    assign_public_ip = true # IP publica EFIMERA del ENI (R-06); sin NAT
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  lifecycle {
    # PLAN §5.A: precondition de la lista negra (ABORTA el plan, no solo avisa)
    # — el par de servicios cabe en una sola task; escalar a >1 multiplica el
    # coste Fargate (§3.2). `make aws-start/stop` mueve desired_count dentro de
    # [0,1], asi que la precondition nunca dispara en el flujo normal.
    precondition {
      condition     = var.service_desired_count <= 1
      error_message = "desired_count > 1 vetado (PLAN §3.2/§5.A: escala horizontal Fargate fuera de presupuesto)."
    }

    # El ciclo de sesion (make aws-start / aws-stop) mueve el contador fuera de
    # Terraform; se ignora para no perder ese control.
    ignore_changes = [desired_count]
  }

  tags = { Name = "otel-lab" }
}
