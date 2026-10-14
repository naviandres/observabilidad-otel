# ECR: un repositorio por imagen del lab, con cleanup "keep last 5" (higiene de
# datos PLAN §3.5; mismo criterio que Artifact Registry en GCP). Tags fijos,
# nunca :latest (C3). El escaneo on-push es gratuito (scan basico).

locals {
  ecr_images = toset(["service-a", "service-b", "otel-collector", "postgres"])
}

resource "aws_ecr_repository" "otel_lab" {
  for_each = local.ecr_images

  name                 = "otel-lab/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "otel-lab-${each.value}" }
}

resource "aws_ecr_lifecycle_policy" "otel_lab" {
  for_each   = aws_ecr_repository.otel_lab
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "keep last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = { type = "expire" }
      }
    ]
  })
}
