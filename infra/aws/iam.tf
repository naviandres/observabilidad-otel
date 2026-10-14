# IAM: roles de la task ECS (least privilege). Las politicas inline se
# materializan desde los JSON canonicos de `deploy/aws/iam/` (unica fuente de
# verdad; los tokens __AWS_REGION__/__AWS_ACCOUNT_ID__ se sustituyen aqui), asi
# que un cambio en el deploy se refleja en Terraform sin duplicar el documento.
#
# La identidad de DESPLIEGUE es el usuario IAM `otel-lab-deploy` (nunca root,
# §3.6): se declara para poder importarlo y se le adjunta una politica
# least-privilege con las acciones que usan el Makefile y los scripts.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

locals {
  task_policy_json      = replace(replace(file("${path.module}/../../deploy/aws/iam/task-role-policy.json"), "__AWS_REGION__", var.aws_region), "__AWS_ACCOUNT_ID__", local.account_id)
  execution_policy_json = replace(replace(file("${path.module}/../../deploy/aws/iam/execution-role-policy.json"), "__AWS_REGION__", var.aws_region), "__AWS_ACCOUNT_ID__", local.account_id)
}

# --- Task role: X-Ray + CloudWatch (EMF/logs) + ECS Exec + resourcedetection ---

resource "aws_iam_role" "task" {
  name               = "otel-lab-task-ecs"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = { Name = "otel-lab-task-ecs" }
}

resource "aws_iam_policy" "task" {
  name   = "otel-lab-task-ecs"
  policy = local.task_policy_json

  tags = { Name = "otel-lab-task-ecs" }
}

resource "aws_iam_role_policy_attachment" "task" {
  role       = aws_iam_role.task.name
  policy_arn = aws_iam_policy.task.arn
}

# --- Execution role: ECR pull + CloudWatch Logs + SSM (secretos de la task) ---

resource "aws_iam_role" "execution" {
  name               = "otel-lab-exec-ecs"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = { Name = "otel-lab-exec-ecs" }
}

resource "aws_iam_policy" "execution" {
  name   = "otel-lab-exec-ecs"
  policy = local.execution_policy_json

  tags = { Name = "otel-lab-exec-ecs" }
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = aws_iam_policy.execution.arn
}

# --- Usuario de despliegue (importable; su politica la amplia este root) ---

resource "aws_iam_user" "deploy" {
  name = "otel-lab-deploy"

  tags = { Name = "otel-lab-deploy" }
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid    = "NetworkLifecycle"
    effect = "Allow"
    actions = [
      "ec2:DescribeVpcs", "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
      "ec2:CreateTags", "ec2:DescribeSubnets", "ec2:CreateSubnet", "ec2:DeleteSubnet",
      "ec2:ModifySubnetAttribute", "ec2:DescribeInternetGateways",
      "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway", "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway", "ec2:DescribeRouteTables", "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable", "ec2:CreateRoute", "ec2:AssociateRouteTable",
      "ec2:DescribeSecurityGroups", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
      "ec2:DescribeNetworkInterfaces",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EcsFargateLifecycle"
    effect = "Allow"
    actions = [
      "ecs:CreateCluster", "ecs:DeleteCluster", "ecs:DescribeClusters", "ecs:ListClusters",
      "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition",
      "ecs:CreateService", "ecs:UpdateService", "ecs:DeleteService", "ecs:DescribeServices",
      "ecs:ListTasks", "ecs:DescribeTasks", "ecs:ListServices",
      "iam:PassRole",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPushPull"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ParamsAndSignals"
    effect = "Allow"
    actions = [
      "ssm:GetParameter", "ssm:GetParameters", "ssm:PutParameter", "ssm:DescribeParameters",
      "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
      "cloudwatch:PutMetricAlarm", "cloudwatch:DescribeAlarms", "cloudwatch:PutDashboard",
      "cloudwatch:GetMetricStatistics", "logs:FilterLogEvents", "logs:DescribeLogGroups",
      "xray:BatchGetTraces", "xray:GetTraceSummaries",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "CostGuardrails"
    effect = "Allow"
    actions = [
      # Cost Explorer / Budgets (gasto diario y anomalias).
      "ce:GetCostAndUsage", "ce:GetAnomalies", "ce:GetAnomalyMonitors",
      "budgets:ViewBudget", "aws-portal:ViewBilling",
      # Lecturas del barrido en vivo de `scripts/cost_guard.ps1` (recursos que
      # facturan ahora mismo). Solo verbos de lectura, sobre los tipos que el
      # script consulta para detectar fugas.
      "ec2:DescribeInstances", "ec2:DescribeAddresses", "ec2:DescribeNatGateways",
      "elasticloadbalancing:DescribeLoadBalancers", "rds:DescribeDBInstances",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deploy" {
  name   = "otel-lab-deploy"
  policy = data.aws_iam_policy_document.deploy.json

  tags = { Name = "otel-lab-deploy" }
}

resource "aws_iam_user_policy_attachment" "deploy" {
  user       = aws_iam_user.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}
