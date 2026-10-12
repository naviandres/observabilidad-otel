# AWS ECS Fargate — task definition and IAM (PLAN §2.B)

Versioned, offline artifacts to run the whole pipeline as **one ECS Fargate
task** (`awsvpc`): `service-a` → `service-b` → `postgres`, the OTel collector
(ADOT binary) and the `cloudwatch-agent` sidecar. Nothing here creates cloud
resources by itself; the cloud session is driven by the root `Makefile`
targets `aws-*` + `scripts/tunnel.ps1`.

## Files

| File | Purpose |
|---|---|
| `task-definition.json` | Task-definition template (5 containers, FARGATE/awsvpc, cpu 1024 / memory 2048). Tokens are rendered at deploy time — never edit the rendered copy. |
| `iam/execution-role-policy.json` | Policy for the **execution role** (ECR pull, CloudWatch Logs write, SSM parameter read for `secrets`). |
| `iam/task-role-policy.json` | Policy for the **task role** (X-Ray write+sampling, CloudWatch Logs/metrics write, SSM/EC2 read for `resourcedetection/ecs`). |

Both policies are attachable JSON documents; they carry the same
`__AWS_ACCOUNT_ID__` / `__AWS_REGION__` tokens as the task definition and are
rendered by the `aws-start` target. Secret material **never** lives here:
it is stored as SSM SecureString parameters and only referenced by ARN.

## Template tokens

`aws-start` renders `task-definition.json` into a git-ignored
`task-definition.rendered.json`, then registers it:

| Token | Source |
|---|---|
| `__AWS_ACCOUNT_ID__` | `AWS_ACCOUNT_ID` make variable |
| `__AWS_REGION__` | `AWS_REGION` make variable (`us-east-1`) |
| `__ECR_REGISTRY__` | `$(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com` |
| `__IMAGE_TAG__` | `IMAGE_TAG` make variable (a fixed version, never `latest`) |

## SSM parameters (SecureString) referenced by ARN

| Parameter | Consumer |
|---|---|
| `/otel-lab/postgres/password` | `postgres` container (`POSTGRES_PASSWORD`) |
| `/otel-lab/database-url/service-a` | `service-a` (`DATABASE_URL`, db `orders`) |
| `/otel-lab/database-url/service-b` | `service-b` (`DATABASE_URL`, db `inventory`) |
| `/otel-lab/grafana/prom-url` · `/prom-auth` | collector `prometheusremotewrite` |
| `/otel-lab/grafana/tempo-endpoint` · `/tempo-auth` | collector `otlphttp/tempo` |
| `/otel-lab/grafana/loki-endpoint` · `/loki-auth` | collector `otlphttp/loki` |
| `/otel-lab/cloudwatch-agent/config` | `cloudwatch-agent` (`CW_CONFIG_CONTENT`) |

## Log path — decision (Fargate does not support `json-file`)

PLAN §2.1/§2.B asks for `logDriver: json-file` on the service containers so that
their logs reach CloudWatch **only** through the collector and
`filter-log-events` by `trace_id` returns no duplicates (R-15). **AWS Fargate
does not support the `json-file` driver** — the only drivers available on
`runTask`/services with `launchType=FARGATE` are `awslogs`, `splunk` and
`awsfirelens` (Amazon ECS API reference, `LogConfiguration`).

Decision adopted here:

1. **All 5 containers use `awslogs`** → one CloudWatch group per container
   under `/otel/labs/ecs-platform/<container>`. This is the *only* log path
   active today.
2. **No duplication is possible** because each service has a **single stdout
   sink** (`service-*/app/telemetry.py` attaches exactly one
   `logging.StreamHandler`; the app does not emit logs over OTLP yet), so there
   is exactly one CloudWatch line per business event — R-15 stays satisfied.
3. The collector's OTLP logs pipeline (`awscloudwatchlogs` exporter →
   `/otel/labs/ecs-cluster/{ServiceName}`) stays wired and versioned; it
   carries traffic only once the apps export logs over OTLP. That is the same
   known limitation recorded for the GKE leg and is tracked as follow-up work
   for the cross-signal correlation feature.

Rejected alternative: `awsfirelens` + a Fluent Bit `log_router` sidecar that
drops the app logs. It would add a 6th container and a Fluent Bit config just
to reproduce `json-file`'s "drop stdout" behaviour; not worth the extra cost
and surface on a 2 GB task.

## IMDSv2 / metadata (R-10)

`metadata_options { http_tokens = "required" }` is an **EC2-launch-type**
setting: it configures the EC2 instance metadata service. On **Fargate** there
is no EC2 instance and no IMDS to harden — the task receives its identity from
the **ECS task metadata endpoint** (`169.254.170.2`, injected by the platform,
not configurable, not exposed outside the task). The `resourcedetection`
`ecs` detector reads exactly that endpoint. So R-10 is satisfied *by
construction* on Fargate; the EC2-only `http_tokens=required` knob does not
apply and no equivalent task-definition field exists.

## Trust policies for the two roles

The deploy user does **not** have `iam:*`; the two roles are created once by a
human with the bootstrap credentials. Each role's trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```
