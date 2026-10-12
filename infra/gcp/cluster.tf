# GKE Standard zonal (PLAN D2). count = var.cluster_enabled: el cluster es el
# recurso caro ($0.10/h de fee de plano, §3.5) y vive SOLO durante sesiones de
# trabajo; el estado "en reposo" del proyecto es cluster_enabled=0.
#
# Guardarraíles inline (PLAN §5.A "precondition en los recursos de la lista
# negra"): Autopilot vetado (§3.2) y node pool NO-spot vetado (§3.2). Un
# precondition aborta el PLAN; un `check` solo avisa — por eso la condicion
# dura es precondition. (Los pre/postconditions de Terraform no pueden leer
# `self` en preconditions: el knob vive en una variable y la precondition la
# corta; ademas el pool lleva un postcondition que verifica SPOT contra el
# estado real tras el apply.)
#
# Add-on Secret Manager (CSI): el provider GA 6.x no expone el bloque
# addons_config.secret_manager_config (solo beta). El flujo de sesion lo
# habilita tras el apply con
#   gcloud container clusters update otel-lab-std --zone <zona> \
#     --enable-component secret-manager
# (o el helm install del driver CSI, docs/deployment.md §21b). Documentado en
# infra/README.md.

variable "cluster_autopilot" {
  description = "SIEMPRE false: GKE Autopilot esta vetado por la lista negra PLAN §3.2 (recargo por pod). Existe solo para que la precondition pueda abortar el plan si alguien lo cambia."
  type        = bool
  default     = false
}

resource "google_container_cluster" "otel_lab_std" {
  count = var.cluster_enabled

  name     = "otel-lab-std"
  location = "${var.gcp_region}-a" # zonal: mismo fee, superficie menor
  project  = var.gcp_project

  network    = google_compute_network.otel_lab.self_link
  subnetwork = google_compute_subnetwork.otel_lab_subnet.self_link

  # El pool de trabajo es el dedicado de abajo; el default se quita.
  remove_default_node_pool = true
  initial_node_count       = 1

  enable_autopilot = var.cluster_autopilot

  release_channel {
    channel = "REGULAR" # mismo canal que el ciclo de vida del Makefile
  }

  # Workload Identity: los pods obtienen credenciales de Google sin llaves
  # (PLAN §5.B; el binding KSA->GSA esta en iam.tf).
  workload_identity_config {
    workload_pool = local.wi_pool
  }

  ip_allocation_policy {} # alias IPs, sin coste

  # Sin LB ni ingress publico: el acceso es kubectl port-forward / IAP
  # (PLAN §3.4 C4). Nada de google_compute_*_proxy/forwarding_rule aqui.
  # Nodes con IP efimera (lista negra §3.2: nada de IP reservada permanente).
  deletion_protection = false # el DoD de coste es destroy por sesion

  lifecycle {
    precondition {
      condition     = !var.cluster_autopilot
      error_message = "GKE Autopilot vetado (lista negra PLAN §3.2: recargo por pod sobre el precio de recursos). Usar GKE Standard con pool spot."
    }
  }
}

# Pool de trabajo: SPOT obligatorio (§3.2 "node pools no-spot pagan on-demand"),
# autoscaling 0-1 (a 0 fuera de sesion = solo paga el fee de plano), perfil
# e2-medium por defecto (ver nota en variables.tf: e2-small no dejo arrancar
# los system pods en GKE 1.36).
resource "google_container_node_pool" "otel_lab_spot" {
  count = var.cluster_enabled

  name       = "otel-lab-spot"
  cluster    = google_container_cluster.otel_lab_std[0].id
  location   = google_container_cluster.otel_lab_std[0].location
  node_count = 1 # transitorio: con autoscaling 0-1 el pool escala a 0 fuera de sesion

  autoscaling {
    min_node_count = 0
    max_node_count = 1
  }

  node_config {
    machine_type = var.machine_type
    spot         = local.node_pool_spot # inquebrantable: precondition + postcondition abajo
    disk_size_gb = 20

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]

    workload_metadata_config {
      mode = "GKE_METADATA" # WI activo en el nodo (sin metadata server legacy)
    }
  }

  lifecycle {
    precondition {
      condition     = local.node_pool_spot
      error_message = "Pool NO-spot vetado (lista negra PLAN §3.2: node pools no-spot pagan on-demand completo)."
    }

    postcondition {
      condition     = try(self.node_config[0].spot, false) == true
      error_message = "El pool creado NO es spot (machine provisioning model != SPOT): veto PLAN §3.2."
    }
  }
}
