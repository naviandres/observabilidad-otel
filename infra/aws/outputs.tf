# Salidas: contrato con el Makefile y los scripts (`make cost-guard`,
# `scripts/access.ps1`, benchmark). Nombres estables y planos.

output "aws_region" {
  description = "Region del root (us-east-1 sin default)."
  value       = var.aws_region
}

output "aws_account_id" {
  description = "Cuenta AWS destino."
  value       = local.account_id
}

output "cluster_name" {
  description = "Nombre del cluster ECS Fargate."
  value       = aws_ecs_cluster.otel_lab.name
}

output "service_name" {
  description = "Nombre del service ECS."
  value       = aws_ecs_service.otel_lab.name
}

output "task_definition_arn" {
  description = "ARN de la task definition otel-lab."
  value       = aws_ecs_task_definition.otel_lab.arn
}

output "ecr_registry" {
  description = "Registro ECR (<cuenta>.dkr.ecr.<region>.amazonaws.com)."
  value       = local.ecr_registry
}

output "ecr_repository_urls" {
  description = "URL de cada repositorio ECR del lab."
  value       = { for k, r in aws_ecr_repository.otel_lab : k => r.repository_url }
}

output "evidence_bucket" {
  description = "Bucket S3 de evidencia (lifecycle 7 dias)."
  value       = aws_s3_bucket.evidence.bucket
}

output "tfstate_bucket" {
  description = "Bucket S3 del state de Terraform (versionado + locking nativo)."
  value       = aws_s3_bucket.tfstate.bucket
}

output "collector_config_sha256" {
  description = "SHA256 del YAML del Collector auditado en S3 (modules/otel-collector)."
  value       = module.otel_collector.config_sha256
}

output "alerts_topic_arn" {
  description = "ARN del topic SNS de alertas (budget + alarmas CloudWatch)."
  value       = aws_sns_topic.alerts.arn
}

output "task_role_arn" {
  description = "ARN del task role (X-Ray / CloudWatch / ECS Exec)."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "ARN del execution role (ECR pull / Logs / SSM)."
  value       = aws_iam_role.execution.arn
}
