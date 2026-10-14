# Guardarrailes del root AWS (PLAN §3.2 lista negra / §3.5 coste). Las
# invariantes viven en el modulo `../modules/guardrails` (check blocks: emiten
# AVISO en plan/apply pero no abortan). El gate DURO es `guard_cost.py
# --provider aws`, que corre en CI y en `make cost-guard`.
#
# El root le pasa los valores REALES (no constantes) para que los checks no sean
# tautologicos.

module "guardrails" {
  source = "../modules/guardrails"

  log_retention_days         = [for g in aws_cloudwatch_log_group.otel_lab : g.retention_in_days]
  ecr_lifecycle_repositories = length(aws_ecr_lifecycle_policy.otel_lab)
  ecr_repositories           = length(local.ecr_images)
  evidence_lifecycle_rules   = length(aws_s3_bucket_lifecycle_configuration.evidence.rule)
  ecs_enable_execute_command = aws_ecs_service.otel_lab.enable_execute_command
  service_desired_count      = var.service_desired_count
  max_service_desired_count  = 1
}
