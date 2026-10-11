# Workloads via provider helm (PLAN §5.A): el chart ../../helm/otel-lab se
# despliega contra el cluster del lab. El gate vive DENTRO del modulo
# (deploy_enabled): este modulo es "legacy" — declara su propio provider helm
# — y Terraform prohibe count sobre llamadas a modulos legacy. Con
# deploy_workloads=0 el modulo no instancia recursos y el provider helm no se
# configura: `terraform plan` del root funciona SIN cluster.
#
# REQUIERE CLUSTER VIVO: el kubeconfig se genera DINAMICAMENTE (data
# google_container_cluster + token ADC via google_client_config), no depende de
# ~/.kube/config.
#
# Orden de sesion: -var cluster_enabled=1 (apply del cluster) -> despues
# -var cluster_enabled=1 -var deploy_workloads=1 para los workloads.

module "otel_lab_workloads" {
  source = "./deploy"

  project = var.gcp_project

  # Referencias [0] protegidas por el guardado ternario: solo se evaluan con
  # cluster_enabled=1 (idioma canonico de Terraform para collections vacias).
  cluster_name     = var.cluster_enabled == 1 ? google_container_cluster.otel_lab_std[0].name : ""
  cluster_location = var.cluster_enabled == 1 ? google_container_cluster.otel_lab_std[0].location : ""

  chart_path     = "${path.module}/../../helm/otel-lab"
  deploy_enabled = var.deploy_workloads
}
