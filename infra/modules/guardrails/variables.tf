# Entradas del modulo `guardrails`: los valores que el root observa en sus
# recursos (no constantes) para que los `check` no sean tautologicos.

variable "log_retention_days" {
  description = "Retencion efectiva de cada CloudWatch log group del lab."
  type        = list(number)
}

variable "expected_log_retention_days" {
  description = "Retencion exigida por §3.5 (higiene de datos)."
  type        = number
  default     = 3
}

variable "ecr_lifecycle_repositories" {
  description = "Repos ECR con lifecycle policy `keep last 5`."
  type        = number
}

variable "ecr_repositories" {
  description = "Repos ECR totales del lab (cada uno necesita su cleanup)."
  type        = number
}

variable "evidence_lifecycle_rules" {
  description = "Reglas de lifecycle del bucket de evidencia."
  type        = number
}

variable "ecs_enable_execute_command" {
  description = "El service ECS debe permitir ECS Exec (§3.3, depuracion sin ingress)."
  type        = bool
}

variable "service_desired_count" {
  description = "Tasks Fargate que mantiene el service (0 = reposo)."
  type        = number
}

variable "max_service_desired_count" {
  description = "Techo de tasks Fargate del lab: mas de una multiplica el coste/h (§3.5)."
  type        = number
  default     = 1
}
