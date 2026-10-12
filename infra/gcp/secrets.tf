# Secret Manager: UN secreto (pg-password) con replicacion automatica.
# El VALOR nunca vive en Terraform: se creo fuera (gcloud) y aqui solo se
# gestiona el contenedor metadata; la version activa se lee via data source
# cuando un consumidor la necesita. Lista negra §3.2 AWS: Secrets Manager
# vetado; en GCP Secret Manager esta en blanca con maximo 2 secretos.

resource "google_secret_manager_secret" "pg_password" {
  secret_id = "pg-password"
  project   = var.gcp_project

  replication {
    auto {}
  }
}

data "google_secret_manager_secret_version" "pg_password_latest" {
  secret = google_secret_manager_secret.pg_password.id
}
