output "network_self_link" {
  description = "Self link de la VPC del lab."
  value       = google_compute_network.otel_lab.self_link
}

output "subnet_self_link" {
  description = "Self link de la subnet (PGA on, sin NAT)."
  value       = google_compute_subnetwork.otel_lab_subnet.self_link
}

output "artifact_registry_uri" {
  description = "URI docker del repo de AR (us-central1-docker.pkg.dev/...)."
  value       = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project}/otel-lab"
}

output "evidence_bucket_url" {
  description = "Bucket de evidencia (lifecycle 7d)."
  value       = google_storage_bucket.evidence.url
}

output "deploy_service_account_email" {
  description = "SA least-privilege usada por Workload Identity."
  value       = google_service_account.otel_lab_deploy.email
}

output "pg_password_secret_version" {
  description = "Nombre completo de la version activa del secreto pg-password (metadata; el VALOR no se expone aqui)."
  value       = data.google_secret_manager_secret_version.pg_password_latest.id
}

output "cluster_endpoint" {
  description = "Endpoint del cluster cuando cluster_enabled=1."
  value       = var.cluster_enabled == 1 ? google_container_cluster.otel_lab_std[0].endpoint : null
  sensitive   = true
}
