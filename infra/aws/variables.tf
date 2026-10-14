# Variables — PLAN §5.A / §3.6: `aws_region` SIN default para obligar a
# pasarla explicitamente (-var 'aws_region=us-east-1') y evitar el desvio
# accidental de region con precios distintos.

variable "aws_region" {
  description = "Region AWS del lab (us-east-1). Sin default a proposito (PLAN §3.6)."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region debe tener forma de region AWS (p. ej. us-east-1)."
  }
}

variable "alert_email" {
  description = "Correo de la suscripcion del topic SNS de alertas (budget/anomalias/alarmas). Vacio = sin suscripcion (se gestiona el topic igual)."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Tag de las imagenes ECR del lab (nunca latest, C3)."
  type        = string
  default     = "0.1.3"
}

variable "service_desired_count" {
  description = "Cuantas tasks Fargate mantiene el service. 0 = reposo (sin coste de computo); el ciclo de sesion lo sube con `make aws-start`."
  type        = number
  default     = 0

  validation {
    condition     = var.service_desired_count >= 0 && var.service_desired_count <= 1
    error_message = "service_desired_count en [0,1]: la task agrupa los 5 contenedores (un solo par en ejecucion)."
  }
}

variable "ecs_cpu" {
  description = "CPU (unidades) de la task Fargate (perfil 1 vCPU = 1024)."
  type        = string
  default     = "1024"
}

variable "ecs_memory" {
  description = "Memoria (MiB) de la task Fargate (perfil 2 GB = 2048)."
  type        = string
  default     = "2048"
}

variable "vpc_cidr" {
  description = "CIDR de la VPC (subnet publica + IGW; SIN NAT, §3.2)."
  type        = string
  default     = "10.20.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR de la subnet publica (mapea public IP al lanzar, R-06)."
  type        = string
  default     = "10.20.1.0/24"
}

variable "max_session_hours_per_month" {
  description = "Guardarrail de horas de task Fargate/mes (PLAN §3.5). Lo consume `make budget-hours` sobre docs/evidence/instance-hours.csv."
  type        = number
  default     = 150

  validation {
    condition     = var.max_session_hours_per_month > 0 && var.max_session_hours_per_month <= 200
    error_message = "max_session_hours_per_month debe estar en (0, 200]: mas horas rompen el techo de creditos AWS."
  }
}

variable "budget_daily_limit_usd" {
  description = "Techo de gasto diario del AWS Budget (PLAN §3.5: 1 USD/dia)."
  type        = number
  default     = 1
}
