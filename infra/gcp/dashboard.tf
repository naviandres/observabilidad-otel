# Dashboard Cloud Monitoring (entregable L35): el JSON gestionado es el
# contenido de dashboards/cloud-monitoring-gcp.json. OJO normalizacion: la API
# reescribe el JSON (ordena tiles, rellena defaults) y el provider hace diff
# contra lo enviado; por eso ignore_changes sobre dashboard_json mantiene el
# 0-diff honesto — el contenido se re-sincroniza a mano con
# `gcloud monitoring dashboards describe` si se edita el JSON del repo
# (procedimiento en infra/README.md).
# Workspace bench: sin dashboard (PLAN §5.A).

resource "google_monitoring_dashboard" "otel_lab_sli" {
  count = terraform.workspace == "lab" ? 1 : 0

  dashboard_json = file("${path.module}/../../dashboards/cloud-monitoring-gcp.json")

  # El provider normaliza `project` al NUMERO del proyecto en el estado; pasar
  # el ID alfanumerico fuerza replacement (verificado en el plan de import).
  project = "797970839119" # = otel-lab-obs-2610

  lifecycle {
    ignore_changes = [dashboard_json]
  }
}
