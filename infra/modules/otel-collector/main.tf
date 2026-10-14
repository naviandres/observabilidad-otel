# Modulo `otel-collector` (PLAN §5.A): publica el YAML del Collector AWS como
# OBJETO DE AUDITORIA en S3. La clave es el SHA256 del contenido, de modo que
# cada revision de la config deja una version inmutable identificable por hash
# (paridad con la copia GCS del root hermano GCP).
#
# El modulo recibe la ruta del YAML (el root la resuelve desde `deploy/`) y el
# bucket destino: no conoce la estructura del repo, solo el artefacto.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_object" "config_audit" {
  bucket = var.bucket

  # Hash del contenido: auditoria inmutable y libre de drift por re-runs.
  key    = "collector/${filesha256(var.config_source)}.yaml"
  source = var.config_source
  etag   = filemd5(var.config_source)

  tags = { Name = var.object_name }
}
