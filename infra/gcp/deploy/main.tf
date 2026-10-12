# Modulo de despliegue de workloads (helm provider con kubeconfig dinamico).
# Todo queda gateado por deploy_enabled: con 0 no se instancia NINGUN recurso,
# NINGUN data source ni se configura el provider helm — asi el plan del root
# no requiere cluster vivo.

variable "project" {
  description = "Project ID GCP del cluster."
  type        = string
}

variable "cluster_name" {
  description = "Nombre del cluster GKE Standard del lab (vacio si no aplica)."
  type        = string
  default     = ""
}

variable "cluster_location" {
  description = "Location (zona) del cluster (vacia si no aplica)."
  type        = string
  default     = ""
}

variable "chart_path" {
  description = "Ruta local del chart helm/otel-lab."
  type        = string
}

variable "deploy_enabled" {
  description = "1 = instanciar helm_release; 0 = modulo inerte (sin cluster vivo)."
  type        = number
  default     = 0
}

data "google_container_cluster" "otel_lab_std" {
  count = var.deploy_enabled

  name     = var.cluster_name
  location = var.cluster_location
  project  = var.project
}

data "google_client_config" "default" {
  count = var.deploy_enabled
}

provider "helm" {
  kubernetes {
    host                   = try("https://${data.google_container_cluster.otel_lab_std[0].endpoint}", "")
    token                  = try(data.google_client_config.default[0].access_token, "")
    cluster_ca_certificate = try(base64decode(data.google_container_cluster.otel_lab_std[0].master_auth[0].cluster_ca_certificate), "")
  }
}

resource "helm_release" "otel_lab" {
  count = var.deploy_enabled

  name             = "otel-lab"
  chart            = var.chart_path
  namespace        = "otel-lab"
  create_namespace = true

  # Valores por defecto del chart (global.project/region pinneados en
  # values.yaml del chart). No se inyectan secretos aqui: el pod monta
  # pg-password via CSI/Secret Manager (helm chart), no via Terraform.
}
