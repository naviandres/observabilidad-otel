# Red: VPC custom + subnet con Private Google Access. SIN Cloud NAT y SIN
# ninguna referencia a google_compute_router_nat (lista negra PLAN §3.2 — el
# egress de los nodos sale a APIs de Google via PGA y el acceso humano es por
# port-forward/IAP, §3.4 C4). Las firewall rules (create-defaults) las crea la
# propia VPC; no se gestionan desde aqui para mantener la paridad con la
# realidad del proyecto.

resource "google_compute_network" "otel_lab" {
  name                    = "otel-lab"
  auto_create_subnetworks = false
  project                 = var.gcp_project
}

resource "google_compute_subnetwork" "otel_lab_subnet" {
  name                     = "otel-lab-subnet"
  network                  = google_compute_network.otel_lab.self_link
  ip_cidr_range            = "10.40.0.0/20"
  region                   = var.gcp_region
  private_ip_google_access = true
  project                  = var.gcp_project
}
