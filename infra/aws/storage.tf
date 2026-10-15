# S3: bucket de evidencia (lifecycle 7 dias, PLAN §3.5 "higiene de datos") +
# bucket del state (versioning ON = prerequisito del locking nativo del backend;
# SIN lifecycle a proposito: borrar el state con retencion de 7 dias romperia la
# auditabilidad — desviacion documentada, igual que el root GCP).

resource "aws_s3_bucket" "evidence" {
  bucket = "otel-lab-evidence-${local.account_id}"

  tags = { Name = "otel-lab-evidence" }
}

resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id

  rule {
    id     = "expire-evidence"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }
  }
}

resource "aws_s3_bucket" "tfstate" {
  bucket = "otel-lab-tfstate-${local.account_id}"

  tags = { Name = "otel-lab-tfstate" }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled" # locking nativo del backend S3
  }
}

# Copia de auditoria del YAML del Collector AWS en S3 (PLAN §5.A, paridad con
# la copia GCS del lado GCP). La gestiona `modules/otel-collector`: la clave
# es el SHA256 del contenido, asi que cada cambio de config deja una version
# inmutable identificable por hash.
module "otel_collector" {
  source = "../modules/otel-collector"

  bucket        = aws_s3_bucket.evidence.id
  config_source = "${path.module}/../../deploy/collector/otel-collector-aws.yaml"
  object_name   = "otel-lab-collector-config-audit"
}

