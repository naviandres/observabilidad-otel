variable "bucket" {
  description = "Bucket de evidencia donde vive la copia de auditoria del YAML."
  type        = string
}

variable "config_source" {
  description = "Ruta local del YAML del Collector AWS (`deploy/collector/otel-collector-aws.yaml`)."
  type        = string
}

variable "object_name" {
  description = "Valor de la etiqueta `Name` del objeto de auditoria."
  type        = string
  default     = "otel-lab-collector-config-audit"
}
