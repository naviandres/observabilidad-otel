# Budget GCP (PLAN §3.5): tope mensual COP 964461 (~USD 300 al cambio del
# proyecto) con EXCLUDE_ALL_CREDITS para que la alerta mida gasto REAL neto de
# creditos, y umbrales 50/80/90/100/150 % via email (no SNS/PubSub: se goberna
# por correo del owner, coste $0).
# Workspace bench: sin budget (PLAN §5.A: bench = mismos recursos sin alarmas
# de coste). El budget es UNO por BA/proyecto, no se duplica en bench.

resource "google_billing_budget" "otel_lab" {
  provider = google-beta
  count    = terraform.workspace == "lab" ? 1 : 0

  billing_account = var.gcp_billing_account
  display_name    = "$300 Alerta de presupuesto mensual"

  budget_filter {
    # SIN `projects`: el budget real se creo a nivel de toda la BA (el GET de
    # la API no porta projects; ponerlo aqui genera diff).
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
    calendar_period        = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = "COP"
      units         = "964461"
    }
  }

  dynamic "threshold_rules" {
    # Orden EXACTO en que la API devuelve los umbrales (orden de creacion:
    # 50, 90, 100, 150, 80). threshold_rules es una lista: reordenarla genera
    # diff. Los umbrales reales son 50/80/90/100/150 %.
    for_each = [0.5, 0.9, 1.0, 1.5, 0.8]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }
}
