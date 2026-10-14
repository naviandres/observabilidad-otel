output "audit_key" {
  description = "Clave S3 del objeto de auditoria (incluye el SHA256 del YAML)."
  value       = aws_s3_object.config_audit.key
}

output "config_sha256" {
  description = "SHA256 del YAML auditado (identidad inmutable de la config)."
  value       = filesha256(var.config_source)
}
