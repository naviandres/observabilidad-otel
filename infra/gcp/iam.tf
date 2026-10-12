# IAM: SA de despliegue least-privilege + Workload Identity.
# Los 6 roles son EXACTAMENTE los que porta hoy el SA en el proyecto (verificado
# via gcloud projects get-iam-policy en la sesion). Nada de roles editor/owner.

resource "google_service_account" "otel_lab_deploy" {
  account_id   = "otel-lab-deploy"
  display_name = "otel-lab deploy" # el nombre real tal cual lo porta el proyecto
  project      = var.gcp_project
}

locals {
  deploy_roles = [
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/artifactregistry.writer",
    "roles/secretmanager.secretAccessor",
    "roles/container.developer",
  ]
}

resource "google_project_iam_member" "otel_lab_deploy" {
  for_each = toset(local.deploy_roles)
  project  = var.gcp_project
  role     = each.value
  member   = "serviceAccount:${google_service_account.otel_lab_deploy.email}"
}

# Workload Identity: los KSA del namespace otel-lab pueden actuar como este SA
# (sin llaves JSON en el pod, PLAN §5.B). Dos members segun la evidencia dura
# docs/evidence/01-wi-binding-gcp.md: el KSA del collector y el default del ns.
locals {
  wi_members = [
    "serviceAccount:${local.wi_pool}[otel-lab/otel-lab-collector]",
    "serviceAccount:${local.wi_pool}[otel-lab/default]",
  ]
}

resource "google_service_account_iam_member" "otel_lab_deploy_wi" {
  for_each           = toset(local.wi_members)
  service_account_id = google_service_account.otel_lab_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
