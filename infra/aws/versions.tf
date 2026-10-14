# Versiones fijadas (PLAN §5: nunca `latest`).
# required_version ~> 1.10: el backend S3 usa `use_lockfile = true` (locking
# NATIVO de S3, sin DynamoDB), disponible desde Terraform 1.10 (§5.A).
terraform {
  required_version = "~> 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
