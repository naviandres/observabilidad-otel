# Versiones fijadas (PLAN §5: nunca `latest`).
terraform {
  required_version = "~> 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2"
    }
  }
}
