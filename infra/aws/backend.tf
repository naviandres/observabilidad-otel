# State remoto en S3 con LOCKING NATIVO (`use_lockfile = true`, condicional
# writes de S3): sin tabla DynamoDB — PLAN §5.A.
# El backend no puede interpolar variables: bucket/key/region van literales y
# las credenciales se resuelven por el perfil AWS (AWS_PROFILE=otel-lab) o la
# cadena estandar del SDK. El bucket de state se crea en el bootstrap
# (infra/README.md) y luego se importa a este root (storage.tf).
#
# Workspaces: `workspace_key_prefix = "lab"` deja el estado en
#   s3://otel-lab-tfstate-621236283643/lab/<workspace>/terraform.tfstate
# (paridad con el prefijo `lab` del backend GCS del root hermano).
terraform {
  backend "s3" {
    bucket               = "otel-lab-tfstate-621236283643"
    key                  = "terraform.tfstate"
    region               = "us-east-1"
    workspace_key_prefix = "lab"
    use_lockfile         = true
    encrypt              = true
  }
}
