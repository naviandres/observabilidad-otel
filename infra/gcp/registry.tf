# Artifact Registry (DOCKER) con cleanup 7 dias / keep-5 (higiene de datos
# PLAN §3.5: "Artifact Registry keep last 5").
# Nota: el bloque GA del provider 6.x es `cleanup_policies`; la variante
# `cleanup_policy_docker_setting` es exclusiva del provider beta.

resource "google_artifact_registry_repository" "otel_lab" {
  repository_id = "otel-lab"
  location      = var.gcp_region
  format        = "DOCKER"
  description   = "imagenes service-a/service-b/collector"
  project       = var.gcp_project

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"

    condition {
      older_than = "604800s" # 7 dias
      tag_state  = "ANY"
    }
  }

  cleanup_policies {
    id     = "keep-minimum-versions"
    action = "KEEP"

    most_recent_versions {
      keep_count = 5
    }
  }
}
