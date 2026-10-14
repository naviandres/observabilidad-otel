# SSM Parameter Store (Standard, lista blanca PLAN §3.3 — NUNCA Secrets
# Manager, lista negra §3.2): contenedores de los parametros que consume la
# task def. El VALOR real se carga fuera de Terraform (CLI) al preparar la
# sesion; aqui solo se gestiona el CONTENEDOR y se ignora `value` para no
# sobrescribirlo (paridad con el secreto pg-password del root GCP).

locals {
  ssm_parameters = {
    "/otel-lab/database-url/service-a"  = { type = "SecureString", desc = "DSN de service-a hacia postgres" }
    "/otel-lab/database-url/service-b"  = { type = "SecureString", desc = "DSN de service-b hacia postgres" }
    "/otel-lab/postgres/password"       = { type = "SecureString", desc = "password del sidecar postgres" }
    "/otel-lab/grafana/prom-url"        = { type = "String", desc = "endpoint Prometheus remoto" }
    "/otel-lab/grafana/prom-auth"       = { type = "SecureString", desc = "auth basic de Prometheus remoto" }
    "/otel-lab/grafana/tempo-endpoint"  = { type = "String", desc = "endpoint OTLP de Tempo" }
    "/otel-lab/grafana/tempo-auth"      = { type = "SecureString", desc = "auth de Tempo" }
    "/otel-lab/grafana/loki-endpoint"   = { type = "String", desc = "endpoint OTLP de Loki" }
    "/otel-lab/grafana/loki-auth"       = { type = "SecureString", desc = "auth de Loki" }
    "/otel-lab/cloudwatch-agent/config" = { type = "String", desc = "config inline del cloudwatch-agent (CW_CONFIG_CONTENT)" }
  }
}

resource "aws_ssm_parameter" "otel_lab" {
  for_each = local.ssm_parameters

  name        = each.key
  description = each.value.desc
  type        = each.value.type
  # Placeholder: el valor real se escribe con la CLI al abrir sesion.
  value = "REPLACED-OUT-OF-BAND"

  tags = { Name = replace(trimprefix(each.key, "/otel-lab/"), "/", "-") }

  lifecycle {
    ignore_changes = [value]
  }
}
