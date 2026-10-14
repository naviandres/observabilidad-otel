# Red: VPC + subnet publica + IGW + route table. SIN NAT Gateway y SIN VPC
# endpoints de interfaz (lista negra PLAN §3.2 — el egress sale por la IP
# publica EFIMERA del ENI del task, R-06; el acceso humano es una regla /32
# temporal, no ingress permanente, D5).
# El SG nace SIN ninguna regla de ingress (steady-state); `scripts/access.ps1`
# anade/revoca el /32 del operador para una sesion.

resource "aws_vpc" "otel_lab" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  lifecycle {
    # PLAN §5.A: precondition de la lista negra (ABORTA, no solo avisa) — el lab
    # vive en us-east-1 (precios y free tier de la region, §3.6).
    precondition {
      condition     = var.aws_region == "us-east-1"
      error_message = "aws_region != us-east-1 vetado (PLAN §3.6/§5.A: el lab solo se despliega en us-east-1)."
    }
  }

  tags = { Name = "otel-lab" }
}

resource "aws_subnet" "otel_lab_public" {
  vpc_id                  = aws_vpc.otel_lab.id
  cidr_block              = var.subnet_cidr
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true # IP publica EFIMERA del ENI del task (R-06)

  tags = { Name = "otel-lab-public" }
}

resource "aws_internet_gateway" "otel_lab" {
  vpc_id = aws_vpc.otel_lab.id

  tags = { Name = "otel-lab-igw" }
}

resource "aws_route_table" "otel_lab_public" {
  vpc_id = aws_vpc.otel_lab.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.otel_lab.id
  }

  tags = { Name = "otel-lab-rt" }
}

resource "aws_route_table_association" "otel_lab_public" {
  subnet_id      = aws_subnet.otel_lab_public.id
  route_table_id = aws_route_table.otel_lab_public.id
}

# SG del task: SIN reglas de ingress en estado de reposo (D5, R-10). El egress
# por defecto (allow-all) es el minimo para alcanzar X-Ray/CloudWatch/SSM.
resource "aws_security_group" "otel_lab_task" {
  name        = "otel-lab-task"
  description = "otel-lab ECS task, no ingress (access via temporary /32 grant)"
  vpc_id      = aws_vpc.otel_lab.id

  tags = { Name = "otel-lab-task" }
}

# Guardarrail: si alguien anade un NAT Gateway o un VPC endpoint, este check lo
# recuerda (el gate DURO es guard_cost.py sobre el plan; el check solo avisa).
check "no_managed_network_services" {
  assert {
    condition     = length(aws_route_table.otel_lab_public.route) == 1
    error_message = "AVISO de coste: la route table publica solo debe tener la ruta 0.0.0.0/0 -> IGW (sin NAT, sin endpoints, §3.2)."
  }
}
