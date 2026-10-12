# Provider google: project/region SIN default (PLAN §3.6). Autenticacion por
# ADC (GOOGLE_APPLICATION_CREDENTIALS) — mismo canal que el backend GCS.

provider "google" {
  project = var.gcp_project
  region  = var.gcp_region

  # ADC de usuario (gcloud) debe llevar quota_project_id=var.gcp_project
  # (gcloud auth application-default set-quota-project otel-lab-obs-2610):
  # APIs como billingbudgets lo exigen y el token ADC sin quota project da
  # 403 SERVICE_DISABLED. El provider lo manda como x-goog-user-project.
}

# google-beta: SOLO para google_billing_budget. Verificado en sesion: el
# google-beta provider con user_project_override=true manda
# x-goog-user-project=project en billingbudgets (el GA 6.x no lo manda y da
# 403 SERVICE_DISABLED con ADC de usuario).
provider "google-beta" {
  project               = var.gcp_project
  region                = var.gcp_region
  billing_project       = var.gcp_project
  user_project_override = true
}

locals {
  # Etiquetas/logica comunes. Solo se aplican donde el recurso real ya las
  # porta o donde no rompen la paridad 0-diff del import (PLAN §5.A: plan
  # limpio tras el primer apply = el estado describe la realidad).
  labels = {
    Environment = "lab"
    ManagedBy   = "terraform"
  }

  wi_pool = "${var.gcp_project}.svc.id.goog"

  # Unico knob del modo spot del pool de trabajo. La precondition del pool lo
  # exige true: cambiarlo a false aborta el PLAN (lista negra §3.2).
  node_pool_spot = true
}
