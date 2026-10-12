# Variables — PLAN §5.A / §3.6: gcp_project y gcp_region SIN default para
# obligar a pasarlas explicitamente (-var) y evitar el desvio accidental de
# region con precios distintos.

variable "gcp_project" {
  description = "Project ID de GCP donde vive todo el lab. Sin default a proposito (PLAN §3.6)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.gcp_project))
    error_message = "gcp_project debe ser un project ID valido (minusculas, digitos y guiones)."
  }
}

variable "gcp_region" {
  description = "Region GCP (us-central1 en el lab). Sin default a proposito (PLAN §3.6)."
  type        = string

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.gcp_region))
    error_message = "gcp_region debe tener forma de region GCP (p. ej. us-central1)."
  }
}

variable "gcp_billing_account" {
  description = "Cuenta de facturacion (formato 000000-000000-000000) para el budget de la fase 0. No es un secreto: es un identificador."
  type        = string
  default     = "013D56-6929B9-3AC57A"
}

variable "cluster_enabled" {
  description = "1 = gestionar el cluster GKE Standard (otel-lab-std). 0 = solo infra permanente. El ciclo de coste del PLAN (§3.5) mantiene esto en 0 fuera de sesion de trabajo."
  type        = number
  default     = 0
}

variable "deploy_workloads" {
  description = "1 = desplegar el chart helm/otel-lab en el cluster via provider helm. Requiere cluster_enabled=1 y cluster VIVO (el provider genera el kubeconfig dinamicamente con ADC)."
  type        = number
  default     = 0
}

variable "machine_type" {
  description = "Perfil de nodo del pool spot. Default e2-medium: el e2-small (2 GiB) del PLAN R-08 se quedo corto de CPU reservable para los system pods en GKE 1.36 (sesion de validacion 2026-10-11: 'Insufficient cpu' con e2-small; e2-medium spot valida el dashboard y la correlacion). Subir el perfil era la mitigacion R-08 prevista; sigue spot, sigue en lista blanca."
  type        = string
  default     = "e2-medium"

  validation {
    # Lista negra §3.2: nada de perfiles compartidos/premium; solo la familia e2 spot.
    condition     = can(regex("^e2-(small|medium|large)$", var.machine_type))
    error_message = "machine_type debe ser de la familia e2 (small|medium|large): nada de N2D/Shared-core premium fuera de lista blanca."
  }
}

variable "max_session_hours_per_month" {
  description = "Guardarrail de horas de cluster/mes (PLAN §3.5: el fee $0.10/h se goberna con sesiones cortas). Lo consumen los guardas de coste (cost-guard), no los recursos."
  type        = number
  default     = 60

  validation {
    condition     = var.max_session_hours_per_month > 0 && var.max_session_hours_per_month <= 200
    error_message = "max_session_hours_per_month debe estar en (0, 200]: mas de 200 h de cluster/mes rompe el presupuesto de creditos."
  }
}
