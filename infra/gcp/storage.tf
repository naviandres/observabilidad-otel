# GCS: bucket de evidencia (lifecycle 7 dias, PLAN §3.5) + bucket de state
# (versioning ON = locking nativo del backend; SIN lifecycle a proposito:
# borrar el state con retencion de 7 dias rompería el locking/auditabilidad —
# desviacion documentada en COST.md).

resource "google_storage_bucket" "evidence" {
  name     = "otel-lab-evidence-${var.gcp_project}"
  location = "US-CENTRAL1"
  project  = var.gcp_project

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 7
    }
  }
}

resource "google_storage_bucket" "tfstate" {
  name     = "otel-lab-tfstate-${var.gcp_project}"
  location = "US-CENTRAL1"
  project  = var.gcp_project

  versioning {
    enabled = true
  }
}
