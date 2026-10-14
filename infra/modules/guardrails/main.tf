# Modulo `guardrails` (PLAN §3.2 lista negra / §3.5 coste): `check` blocks que
# emiten AVISO en plan/apply pero NO abortan. El gate DURO de coste es
# `guard_cost.py --provider aws` (CI + `make cost-guard`); este modulo deja las
# invariantes del stack escritas en HCL y verificables en `terraform plan`.
#
# Recibe los valores REALES calculados por el root (retencion de cada log group,
# numero de lifecycle policies ECR, reglas de lifecycle del bucket, atributos del
# service ECS) para que las aserciones no sean tautologicas.

terraform {
  required_version = ">= 1.10"
}

check "logs_retention_is_expected" {
  assert {
    condition = alltrue([
      for d in var.log_retention_days : d == var.expected_log_retention_days
    ])
    error_message = "AVISO de coste: todos los log groups del lab deben retener ${var.expected_log_retention_days} dias (§3.5)."
  }
}

check "ecr_keep_last_five" {
  assert {
    condition     = var.ecr_lifecycle_repositories == var.ecr_repositories
    error_message = "AVISO de coste: cada repo ECR necesita su cleanup 'keep last 5' (§3.5)."
  }
}

check "evidence_bucket_expires" {
  assert {
    condition     = var.evidence_lifecycle_rules >= 1
    error_message = "AVISO de higiene de datos: el bucket de evidencia necesita lifecycle con expiracion (§3.5)."
  }
}

check "no_idle_costly_service" {
  assert {
    condition = (
      var.service_desired_count >= 0
      && var.service_desired_count <= var.max_service_desired_count
      && var.ecs_enable_execute_command
    )
    error_message = "AVISO: el service debe permitir ECS Exec y mantener desired_count en [0, ${var.max_service_desired_count}] (§3.3/§3.5)."
  }
}
