# Makefile — ciclo de vida GCP (feature #21, PLAN §2.B-GCP/§3.5).
#
# GUARDARRAILES (§3.5): el cluster GKE Standard cuesta $0.10/h de mgmt fee MAS
# el nodo spot e2-small. Regla operativa: la sesion de nube se ENCIENDE para
# trabajar y se DESTRUYE al terminar; el ciclo diario es `docker compose up`
# (modo local, $0). `gcp-stop` escala el nodo a 0 (el fee sigue corriendo);
# `gcp-destroy` es el boton nuclear y parte del DoD.
#
# Variables con ?= para override sin editar el archivo:
#   make deploy-gcp OTEL_TRACES_EXPORTER=nop   (modo benchmark, §3.4 C2)

GCP_PROJECT_ID ?= otel-lab-obs-2610
GCP_REGION     ?= us-central1
GCP_ZONE       ?= us-central1-a
GCP_CLUSTER    ?= otel-lab-std
GCP_NETWORK    ?= otel-lab
GCP_SUBNET     ?= otel-lab-subnet
GCP_NAMESPACE  ?= otel-lab
COLLECTOR_K8S_SA    ?= otel-lab-collector
COLLECTOR_GCP_SA    ?= otel-lab-deploy@$(GCP_PROJECT_ID).iam.gserviceaccount.com
OTEL_TRACES_EXPORTER ?= googlecloud

GCLOUD := gcloud --project $(GCP_PROJECT_ID)

.PHONY: gcp-start gcp-stop gcp-destroy deploy-gcp port-gcp

# gcp-start — cluster GKE Standard ZONAL us-central1-a, release channel regular,
# 1 nodo spot e2-small con autoscaling 0-1, private Google Access (SIN Cloud
# NAT, lista negra §3.2) y Workload Identity. Coste: fee $0.10/h + spot e2-small.
gcp-start:
	@echo ">> VPC $(GCP_NETWORK) + subnet $(GCP_SUBNET) (private Google Access ON, sin NAT)"
	$(GCLOUD) compute networks describe $(GCP_NETWORK) >/dev/null 2>&1 \
	  || $(GCLOUD) compute networks create $(GCP_NETWORK) --subnet-mode=custom
	$(GCLOUD) compute networks subnets describe $(GCP_SUBNET) --region=$(GCP_REGION) >/dev/null 2>&1 \
	  || $(GCLOUD) compute networks subnets create $(GCP_SUBNET) \
	       --network=$(GCP_NETWORK) --region=$(GCP_REGION) \
	       --range=10.40.0.0/20 --enable-private-ip-google-access
	@echo ">> cluster $(GCP_CLUSTER) (zonal $(GCP_ZONE), regular channel, spot e2-small 0-1) — fee $0.10/h"
	$(GCLOUD) container clusters describe $(GCP_CLUSTER) --location=$(GCP_ZONE) >/dev/null 2>&1 \
	  || $(GCLOUD) container clusters create $(GCP_CLUSTER) \
	       --location=$(GCP_ZONE) \
	       --release-channel=regular \
	       --machine-type=e2-small --num-nodes=1 --spot \
	       --enable-autoscaling --min-nodes=0 --max-nodes=1 \
	       --network=$(GCP_NETWORK) --subnetwork=$(GCP_SUBNET) \
	       --workload-pool=$(GCP_PROJECT_ID).svc.id.goog --no-enable-basic-auth --no-enable-legacy-authorization
	@echo ">> Workload Identity binding: $(COLLECTOR_GCP_SA) <- $(GCP_NAMESPACE)/$(COLLECTOR_K8S_SA)"
	$(GCLOUD) iam service-accounts add-iam-policy-binding $(COLLECTOR_GCP_SA) \
	  --role=roles/iam.workloadIdentityUser \
	  --member="serviceAccount:$(GCP_PROJECT_ID).svc.id.goog[$(GCP_NAMESPACE)/$(COLLECTOR_K8S_SA)]"
	@echo ">> kubeconfig"
	$(GCLOUD) container clusters get-credentials $(GCP_CLUSTER) --location=$(GCP_ZONE)
	@echo ">> PREREQUISITO #21b (no automatizado aqui): instalar el driver CSI"
	@echo "   secrets-store.csi.k8s.io + provider-gcp (helm repo external-secrets/cs-driver)"
	@echo "   y dar SM secretAccessor al SA del addon: $(COLLECTOR_GCP_SA) ya lo tiene."

# gcp-stop — escala el pool a 0 (deja de correr el nodo spot; el fee $0.10/h
# del cluster SIGUE corriendo: usa gcp-destroy fuera de la sesion de trabajo).
gcp-stop:
	$(GCLOUD) container clusters resize $(GCP_CLUSTER) --num-nodes=0 --location=$(GCP_ZONE)

# gcp-destroy — boton de destruccion (DoD §3.5) + VERIFICACION: el cluster no
# debe aparecer en la lista. Coste tras destroy: ~$0 (queda VPC/subnet, $0).
gcp-destroy:
	$(GCLOUD) container clusters delete $(GCP_CLUSTER) --location=$(GCP_ZONE) --quiet
	@test -z "$$($(GCLOUD) container clusters list --filter="name=$(GCP_CLUSTER)" --format='value(name)')" \
	  || (echo "ERROR: $(GCP_CLUSTER) sigue vivo"; exit 1)
	@echo ">> cluster destruido y verificado (fee detenido)"

# deploy-gcp — sincroniza la config del collector y el seed al chart (unica
# fuente de verdad: deploy/collector/ + init-scripts/; el test del harness
# exige bytes identicos) y hace helm upgrade --install.
deploy-gcp:
	mkdir -p helm/otel-lab/files/init
	cp deploy/collector/otel-collector-gcp.yaml helm/otel-lab/files/otel-collector-gcp.yaml
	cp init-scripts/* helm/otel-lab/files/init/
	helm upgrade --install otel-lab helm/otel-lab \
	  --namespace $(GCP_NAMESPACE) --create-namespace \
	  --set global.project=$(GCP_PROJECT_ID) \
	  --set global.region=$(GCP_REGION) \
	  --set collector.env.OTEL_TRACES_EXPORTER=$(OTEL_TRACES_EXPORTER)
	kubectl -n $(GCP_NAMESPACE) rollout status deployment/otel-lab-collector --timeout=180s

# port-gcp — acceso SIN ingress publico (§2.B-GCP/C4): port-forward autenticado.
# svc/service-a 8080:8080 (k6/correlacion) + collector 4317 (OTLP) y 8889
# (Prometheus exporter para leer otelcol_* del checklist §2.C via scrape local).
port-gcp:
	kubectl -n $(GCP_NAMESPACE) port-forward svc/service-a 8080:8080 & \
	kubectl -n $(GCP_NAMESPACE) port-forward svc/otel-lab-collector 4317:4317 8889:8889
