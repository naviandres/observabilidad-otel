# Makefile — ciclo de vida GCP (despliegue GKE, PLAN §2.B-GCP/§3.5).
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

# --- AWS (ECS Fargate, PLAN §2.B) -------------------------------------------
# Defaults alineados con `.env.example` y `deploy/aws/`: el perfil de deploy
# (nunca root) y los repos ECR creados en la Fase 0. La task definition es un
# template con tokens (__AWS_ACCOUNT_ID__ etc.) que `aws-start` renderiza.
AWS_REGION      ?= us-east-1
AWS_ACCOUNT_ID  ?= 621236283643
AWS_PROFILE     ?= otel-lab
ECR_REGISTRY    ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE_TAG       ?= 0.1.0
ECS_CLUSTER     ?= otel-lab
ECS_SERVICE     ?= otel-lab
ECS_TASK_FAMILY ?= otel-lab
TASK_ROLE       ?= otel-lab-task-ecs
EXEC_ROLE       ?= otel-lab-exec-ecs
# Red minima (subnet publica + IGW, sin NAT): IDs descubiertos por tag, sin state.
NET_NAME        ?= otel-lab
VPC_CIDR        ?= 10.20.0.0/16
SUBNET_CIDR     ?= 10.20.1.0/24

AWS := aws --region $(AWS_REGION) --profile $(AWS_PROFILE)

.PHONY: gcp-start gcp-stop gcp-destroy deploy-gcp port-gcp port-jaeger-gcp \
        aws-net-create aws-net-destroy aws-push aws-start aws-stop aws-destroy \
        tunnel validate-collector

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
	@echo ">> PREREQUISITO (no automatizado aqui): instalar el driver CSI"
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
# fuente de verdad: deploy/collector/ + init-scripts/; la suite de
# verificacion del laboratorio exige bytes identicos) y hace helm upgrade --install.
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

# port-jaeger-gcp — Jaeger UI dentro del cluster (§2.B-GCP, sin ingress publico).
# La UI queda en http://localhost:16686; el waterfall service-a->service-b->DB
# se consulta ahi (o via la API /api/traces). port-forward autenticado.
port-jaeger-gcp:
	kubectl -n $(GCP_NAMESPACE) port-forward svc/jaeger-ui-on-gcp 16686:16686

# ===========================================================================
# AWS — ECS Fargate (PLAN §2.B/§3.5). Cómputo = task Fargate (D1).
# Ciclo de coste: `aws-start` enciende (desired_count=1), `aws-stop` apaga
# (0) y `aws-destroy` es el botón nuclear + verificación. Nada de esto corre
# por Terraform: son llamadas AWS CLI idempotentes sobre recursos tagueados.
# ===========================================================================

# aws-net-create — VPC + subnet publica + IGW + route table + SG SIN ingress
# (D5/§3.2: acceso humano solo por túnel SSM). Idempotente: descubre por tag.
# Sin NAT Gateway ni VPC endpoints (lista negra §3.2).
aws-net-create:
	@vpc=$$($(AWS) ec2 describe-vpcs --filters "Name=tag:Name,Values=$(NET_NAME)" --query 'Vpcs[0].VpcId' --output text); \
	if [ "$$vpc" = "None" ] || [ -z "$$vpc" ]; then \
	  vpc=$$($(AWS) ec2 create-vpc --cidr-block $(VPC_CIDR) --query 'Vpc.VpcId' --output text); \
	  $(AWS) ec2 create-tags --resources $$vpc --tags Key=Name,Value=$(NET_NAME); \
	  $(AWS) ec2 modify-vpc-attribute --vpc-id $$vpc --enable-dns-hostnames; \
	fi; \
	echo ">> VPC=$$vpc"; \
	subnet=$$($(AWS) ec2 describe-subnets --filters "Name=tag:Name,Values=$(NET_NAME)-public" --query 'Subnets[0].SubnetId' --output text); \
	if [ "$$subnet" = "None" ] || [ -z "$$subnet" ]; then \
	  subnet=$$($(AWS) ec2 create-subnet --vpc-id $$vpc --cidr-block $(SUBNET_CIDR) --query 'Subnet.SubnetId' --output text); \
	  $(AWS) ec2 create-tags --resources $$subnet --tags Key=Name,Value=$(NET_NAME)-public; \
	  $(AWS) ec2 modify-subnet-attribute --subnet-id $$subnet --map-public-ip-on-launch; \
	fi; \
	echo ">> subnet=$$subnet"; \
	igw=$$($(AWS) ec2 describe-internet-gateways --filters "Name=tag:Name,Values=$(NET_NAME)-igw" --query 'InternetGateways[0].InternetGatewayId' --output text); \
	if [ "$$igw" = "None" ] || [ -z "$$igw" ]; then \
	  igw=$$($(AWS) ec2 create-internet-gateway --query 'InternetGateway.InternetGatewayId' --output text); \
	  $(AWS) ec2 create-tags --resources $$igw --tags Key=Name,Value=$(NET_NAME)-igw; \
	  $(AWS) ec2 attach-internet-gateway --internet-gateway-id $$igw --vpc-id $$vpc; \
	fi; \
	echo ">> igw=$$igw"; \
	rt=$$($(AWS) ec2 describe-route-tables --filters "Name=tag:Name,Values=$(NET_NAME)-rt" --query 'RouteTables[0].RouteTableId' --output text); \
	if [ "$$rt" = "None" ] || [ -z "$$rt" ]; then \
	  rt=$$($(AWS) ec2 create-route-table --vpc-id $$vpc --query 'RouteTable.RouteTableId' --output text); \
	  $(AWS) ec2 create-tags --resources $$rt --tags Key=Name,Value=$(NET_NAME)-rt; \
	  $(AWS) ec2 create-route --route-table-id $$rt --destination-cidr-block 0.0.0.0/0 --gateway-id $$igw >/dev/null; \
	  $(AWS) ec2 associate-route-table --route-table-id $$rt --subnet-id $$subnet >/dev/null; \
	fi; \
	sg=$$($(AWS) ec2 describe-security-groups --filters "Name=tag:Name,Values=$(NET_NAME)-task" --query 'SecurityGroups[0].GroupId' --output text); \
	if [ "$$sg" = "None" ] || [ -z "$$sg" ]; then \
	  sg=$$($(AWS) ec2 create-security-group --group-name $(NET_NAME)-task --description "otel-lab ECS task, no ingress (SSM tunnel only)" --vpc-id $$vpc --query 'GroupId' --output text); \
	  $(AWS) ec2 create-tags --resources $$sg --tags Key=Name,Value=$(NET_NAME)-task; \
	fi; \
	echo ">> sg=$$sg (sin reglas de ingress)"

# aws-net-destroy — borra SG + subnet + IGW + route table + VPC (los deja si hay
# dependencias: la task debe estar destruida antes). Coste tras destroy: ~$0.
aws-net-destroy:
	@vpc=$$($(AWS) ec2 describe-vpcs --filters "Name=tag:Name,Values=$(NET_NAME)" --query 'Vpcs[0].VpcId' --output text); \
	if [ "$$vpc" = "None" ] || [ -z "$$vpc" ]; then echo ">> VPC $(NET_NAME) no existe (nada que borrar)"; exit 0; fi; \
	sg=$$($(AWS) ec2 describe-security-groups --filters "Name=tag:Name,Values=$(NET_NAME)-task" --query 'SecurityGroups[0].GroupId' --output text); \
	[ "$$sg" != "None" ] && $(AWS) ec2 delete-security-group --group-id $$sg || true; \
	rt=$$($(AWS) ec2 describe-route-tables --filters "Name=tag:Name,Values=$(NET_NAME)-rt" --query 'RouteTables[0].RouteTableId' --output text); \
	[ "$$rt" != "None" ] && $(AWS) ec2 delete-route-table --route-table-id $$rt || true; \
	igw=$$($(AWS) ec2 describe-internet-gateways --filters "Name=tag:Name,Values=$(NET_NAME)-igw" --query 'InternetGateways[0].InternetGatewayId' --output text); \
	if [ "$$igw" != "None" ]; then $(AWS) ec2 detach-internet-gateway --internet-gateway-id $$igw --vpc-id $$vpc; $(AWS) ec2 delete-internet-gateway --internet-gateway-id $$igw; fi; \
	subnet=$$($(AWS) ec2 describe-subnets --filters "Name=tag:Name,Values=$(NET_NAME)-public" --query 'Subnets[0].SubnetId' --output text); \
	[ "$$subnet" != "None" ] && $(AWS) ec2 delete-subnet --subnet-id $$subnet || true; \
	$(AWS) ec2 delete-vpc --vpc-id $$vpc; \
	echo ">> red $(NET_NAME) destruida"

# aws-push — login en ECR + build + push de las 3 imagenes (service-a, service-b
# y el collector ADOT con el YAML horneado). Tags fijos, nunca :latest (C3).
aws-push:
	@echo ">> ECR login ($(ECR_REGISTRY))"
	$(AWS) ecr get-login-password | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker build -t $(ECR_REGISTRY)/otel-lab/service-a:$(IMAGE_TAG) service-a
	docker push $(ECR_REGISTRY)/otel-lab/service-a:$(IMAGE_TAG)
	docker build -t $(ECR_REGISTRY)/otel-lab/service-b:$(IMAGE_TAG) service-b
	docker push $(ECR_REGISTRY)/otel-lab/service-b:$(IMAGE_TAG)
	docker build -t $(ECR_REGISTRY)/otel-lab/otel-collector:$(IMAGE_TAG) -f deploy/collector/otelcol-contrib.dockerfile deploy/collector
	docker push $(ECR_REGISTRY)/otel-lab/otel-collector:$(IMAGE_TAG)

# aws-start — red + cluster (si falta) + task definition render/register +
# service FARGATE desired_count=1 con assignPublicIp efimero (R-06) y ECS Exec.
aws-start: aws-net-create
	@vpc=$$($(AWS) ec2 describe-vpcs --filters "Name=tag:Name,Values=$(NET_NAME)" --query 'Vpcs[0].VpcId' --output text); \
	subnet=$$($(AWS) ec2 describe-subnets --filters "Name=tag:Name,Values=$(NET_NAME)-public" --query 'Subnets[0].SubnetId' --output text); \
	sg=$$($(AWS) ec2 describe-security-groups --filters "Name=tag:Name,Values=$(NET_NAME)-task" --query 'SecurityGroups[0].GroupId' --output text); \
	$(AWS) ecs describe-clusters --clusters $(ECS_CLUSTER) --query 'clusters[0].status' --output text | grep -q ACTIVE \
	  || $(AWS) ecs create-cluster --cluster-name $(ECS_CLUSTER); \
	sed -e 's#__AWS_ACCOUNT_ID__#$(AWS_ACCOUNT_ID)#g' \
	    -e 's#__AWS_REGION__#$(AWS_REGION)#g' \
	    -e 's#__ECR_REGISTRY__#$(ECR_REGISTRY)#g' \
	    -e 's#__IMAGE_TAG__#$(IMAGE_TAG)#g' \
	    deploy/aws/task-definition.json > deploy/aws/task-definition.rendered.json; \
	$(AWS) ecs register-task-definition --cli-input-json file://deploy/aws/task-definition.rendered.json --query 'taskDefinition.taskDefinitionArn' --output text; \
	if [ -n "$$($(AWS) ecs describe-services --cluster $(ECS_CLUSTER) --services $(ECS_SERVICE) --query 'services[?status==`ACTIVE`].serviceName' --output text)" ]; then \
	  $(AWS) ecs update-service --cluster $(ECS_CLUSTER) --service $(ECS_SERVICE) --task-definition $(ECS_TASK_FAMILY) --desired-count 1 --enable-execute-command >/dev/null; \
	else \
	  $(AWS) ecs create-service --cluster $(ECS_CLUSTER) --service-name $(ECS_SERVICE) --task-definition $(ECS_TASK_FAMILY) --desired-count 1 --launch-type FARGATE --enable-execute-command --network-configuration "awsvpcConfiguration={subnets=[$$subnet],securityGroups=[$$sg],assignPublicIp=ENABLED}" >/dev/null; \
	fi; \
	echo ">> service $(ECS_SERVICE) desired_count=1 (task Fargate encendida)"; \
	echo ">> RECUERDA: make aws-stop al terminar la sesion (PLAN §3.5)"

# aws-stop — apaga la task (desired_count=0). El cluster queda VACIO: no hay
# coste de computo. Idempotente.
aws-stop:
	$(AWS) ecs update-service --cluster $(ECS_CLUSTER) --service $(ECS_SERVICE) --desired-count 0 --query 'service.{name:serviceName,desired:desiredCount}' --output text
	@echo ">> desired_count=0 — la task Fargate deja de facturar"

# aws-destroy — boton nuclear (DoD §3.5): borra service + cluster y VERIFICA
# que no quedan clusters activos con ese nombre. La red queda ($0); usa
# aws-net-destroy para borrarla tambien.
aws-destroy:
	-$(AWS) ecs update-service --cluster $(ECS_CLUSTER) --service $(ECS_SERVICE) --desired-count 0 >/dev/null 2>&1
	-$(AWS) ecs delete-service --cluster $(ECS_CLUSTER) --service $(ECS_SERVICE) --force >/dev/null 2>&1
	-$(AWS) ecs wait services-inactive --cluster $(ECS_CLUSTER) --services $(ECS_SERVICE) 2>/dev/null || true
	-$(AWS) ecs delete-cluster --cluster $(ECS_CLUSTER) >/dev/null 2>&1 || true
	@test -z "$$($(AWS) ecs list-clusters --query "clusterArns[?contains(@, '$(ECS_CLUSTER)')]" --output text)" \
	  || (echo "ERROR: cluster $(ECS_CLUSTER) sigue vivo"; exit 1)
	@echo ">> ECS destruido y verificado (sin computo). Ejecuta 'make aws-net-destroy' si quieres borrar la red"

# tunnel — acceso SIN ingress publico (C4/§2.B): port-forwarding SSM via
# scripts/tunnel.ps1. Requiere session-manager-plugin en el PATH.
tunnel:
	pwsh -NoProfile -File scripts/tunnel.ps1 -Cluster $(ECS_CLUSTER) -Service $(ECS_SERVICE) -Region $(AWS_REGION) -Profile $(AWS_PROFILE)

# validate-collector — PLAN §2.C: el binario ADOT (v0.46.0) NO tiene subcomando
# `validate`; se valida por ARRANQUE limpio ("Everything is ready" == exit 0).
# La config local/gcp se valida con el binario contrib (`validate`).
validate-collector:
	@echo ">> ADOT: validacion por arranque (sin subcomando validate)"
	-@docker rm -f otel-lab-validate >/dev/null 2>&1 || true
	docker run -d --name otel-lab-validate \
	  -e AWS_REGION=$(AWS_REGION) \
	  -e GRAFANA_PROM_URL=https://prometheus.example.com/api/prom \
	  -e GRAFANA_PROM_AUTH=dXNlcjpwYXNz \
	  -e GRAFANA_TEMPO_ENDPOINT=https://tempo.example.com \
	  -e GRAFANA_TEMPO_AUTH=dXNlcjpwYXNz \
	  -e GRAFANA_LOKI_ENDPOINT=https://loki.example.com/otlp \
	  -e GRAFANA_LOKI_AUTH=dXNlcjpwYXNz \
	  -v "$$(pwd)/deploy/collector:/cfg" \
	  public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0 \
	  --config=file:/cfg/otel-collector-aws.yaml >/dev/null
	@ok=0; for i in $$(seq 1 20); do \
	  if docker logs otel-lab-validate 2>&1 | grep -q "Everything is ready"; then ok=1; break; fi; \
	  if docker logs otel-lab-validate 2>&1 | grep -q "error found during service initialization"; then break; fi; \
	  sleep 1; \
	done; \
	docker rm -f otel-lab-validate >/dev/null 2>&1 || true; \
	test "$$ok" = "1" || (echo "ERROR: ADOT rechazo la config AWS (ver arriba)"; exit 1); \
	echo ">> ADOT OK: config AWS arranca limpio"
	docker run --rm -v "$$(pwd)/deploy/collector:/cfg" \
	  otel/opentelemetry-collector-contrib:0.162.0 \
	  validate --config=/cfg/otel-collector-local.yaml
