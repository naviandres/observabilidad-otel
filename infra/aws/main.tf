# Provider aws: region SIN default (PLAN §3.6). Autenticacion por perfil
# AWS_PROFILE=otel-lab (nunca root) o la cadena estandar del SDK; el backend S3
# usa la misma identidad.

provider "aws" {
  region = var.aws_region

  # Etiquetas comunes a TODO recurso con soporte de tags (PLAN §5.A). `Name` se
  # anade por recurso (no en default_tags: cada recurso lleva el suyo).
  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # El workspace ES el entorno: `lab` (todo el inventario + guardarrailes de
  # coste) vs `bench` (mismos workloads SIN budget/monitor de anomalias: los
  # umbrales no deben contaminar la medicion del benchmark, PLAN §5.A).
  environment = terraform.workspace
  is_lab      = terraform.workspace == "lab"

  common_tags = {
    Environment = local.environment
    ManagedBy   = "terraform"
    Project     = "otel-lab"
  }
}

# Guardarrail de coste (check, NO precondition): Terraform avisa si alguien
# cambia la region por una con precios distintos (§3.6) pero no aborta el plan
# (el root hermano es us-central1; el lab AWS es us-east-1).
check "aws_region_cost_guard" {
  assert {
    condition     = var.aws_region == "us-east-1"
    error_message = "AVISO de coste: el lab AWS se cotiza en us-east-1 (PLAN §3.6). Otra region invalida la linea base del reporte."
  }
}

check "workspace_is_lab_or_bench" {
  assert {
    condition     = contains(["lab", "bench"], terraform.workspace)
    error_message = "Los workspaces validos son lab (sesion) y bench (benchmark); otro nombre sugiere un state mal apuntado (PLAN §5.A)."
  }
}
