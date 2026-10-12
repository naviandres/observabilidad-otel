# State remoto en GCS con locking nativo (object versioning del bucket
# otel-lab-tfstate-*; sin bucket de locks) — PLAN §5.A.
# El backend no puede interpolar variables: bucket/prefix van literales y las
# credenciales se resuelven por ADC (GOOGLE_APPLICATION_CREDENTIALS), igual que
# en el provider.
terraform {
  backend "gcs" {
    bucket = "otel-lab-tfstate-otel-lab-obs-2610"
    prefix = "lab"
  }
}
