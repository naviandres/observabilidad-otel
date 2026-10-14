# X-Ray: regla de muestreo por encima de la reserva por defecto (PLAN §3.4
# C3: reserva 1 rps + 5% base). El SDK de cada servicio baja esta regla del
# servicio X-Ray (GetSamplingRules) al arrancar. Es regional y no admite tags.

resource "aws_xray_sampling_rule" "otel_lab" {
  rule_name      = "otel-lab-base"
  priority       = 1
  version        = 1
  reservoir_size = 1
  fixed_rate     = 0.05
  url_path       = "*"
  host           = "*"
  http_method    = "*"
  service_type   = "*"
  service_name   = "*"
  resource_arn   = "*"
  attributes     = {}
}
