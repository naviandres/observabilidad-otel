# Presupuesto y alertas (PLAN §3.5): AWS Budget de gasto DIARIO (1 USD/dia) +
# topic SNS. El topic tambien es el destino de las alarmas de CloudWatch
# (dashboards/cloudwatch-alarms.json se publica con AlarmActions -> este ARN,
# via `make aws-alarms SNS_TOPIC_ARN=<alerts_topic_arn>`), de modo que una sola
# suscripcion de correo cubre budget, anomalias y alarmas.
#
# El budget y el monitor de anomalias son propios del workspace `lab`: en
# `bench` NO se crean (los umbrales de coste no deben contaminar la medicion
# del benchmark, PLAN §5.A). El topic SNS se crea en ambos (es el destino de
# las alarmas, sin coste propio).

resource "aws_sns_topic" "alerts" {
  name = "otel-lab-alerts"

  tags = { Name = "otel-lab-alerts" }
}

# Presupuesto + CloudWatch necesitan permiso explicito para publicar en el topic.
data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid    = "AllowBudgetsAndCloudWatchPublish"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "cloudwatch.amazonaws.com"]
    }
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_budgets_budget" "daily" {
  count = local.is_lab ? 1 : 0

  name         = "otel-lab-daily"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_daily_limit_usd)
  limit_unit   = "USD"
  time_unit    = "DAILY"

  # AWS BUDGETS reescribe time_period_start al crear: se fija y se ignora el
  # drift para que `terraform plan` quede limpio (misma desviacion documentada
  # que en el root GCP con el budget mensual).
  time_period_start = "2026-01-01_00:00"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  lifecycle {
    ignore_changes = [time_period_start]
  }
}

check "budget_is_tiny" {
  assert {
    condition     = var.budget_daily_limit_usd <= 5
    error_message = "AVISO de coste: el budget diario del lab no deberia superar 5 USD (PLAN §3.5 fija 1 USD/dia)."
  }
}

# Monitor de anomalias de Cost Explorer (deteccion de picos que el budget
# DIARIO no ve) + suscripcion que avisa al MISMO topic SNS (PLAN §3.5). Es
# gratis; el topic ya esta por encima.
resource "aws_ce_anomaly_monitor" "lab" {
  count = local.is_lab ? 1 : 0

  name              = "otel-lab-anomaly"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "lab" {
  count = local.is_lab ? 1 : 0

  name      = "otel-lab-anomaly-sub"
  frequency = "DAILY"

  monitor_arn_list = [aws_ce_anomaly_monitor.lab[0].arn]

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.alerts.arn
  }

  # Avisa si el impacto absoluto estimado de una anomalia llega a 1 USD.
  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["1"]
    }
  }
}

