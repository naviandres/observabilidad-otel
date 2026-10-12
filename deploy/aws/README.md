# AWS ECS Fargate — task definition and IAM (PLAN §2.B)

Versioned, offline artifacts to run the whole pipeline as **one ECS Fargate
task** (`awsvpc`): `service-a` → `service-b` → `postgres`, the OTel collector
(ADOT binary) and the `cloudwatch-agent` sidecar. Nothing here creates cloud
resources by itself; the cloud session is driven by the root `Makefile`
targets `aws-*` + `scripts/access.ps1` (temporary SG /32 grant/revoke — SSM
port-forwarding to Fargate does not exist, see `docs/aws-ecs.md` §5).

## Files

| File | Purpose |
|---|---|
| `task-definition.json` | Task-definition template (5 containers, FARGATE/awsvpc, cpu 1024 / memory 2048). Tokens are rendered at deploy time — never edit the rendered copy. |
| `postgres.Dockerfile` | Internal `otel-lab/postgres` image: `postgres:16-alpine` + `init-scripts/` baked into `/docker-entrypoint-initdb.d/` (Fargate has no host bind-mounts). Built by `make aws-push`. |
| `iam/execution-role-policy.json` | Policy for the **execution role** (ECR pull, CloudWatch Logs write, SSM parameter read for `secrets`). |
| `iam/task-role-policy.json` | Policy for the **task role** (X-Ray write+sampling, CloudWatch Logs/metrics write, SSM/EC2 read for `resourcedetection/ecs`). |

Both policies are attachable JSON documents that carry the same
`__AWS_ACCOUNT_ID__` / `__AWS_REGION__` tokens as the task definition. Unlike
the task definition, `aws-start` does **not** render or apply them: the deploy
user has no `iam:*`, so a human substitutes the tokens and attaches each policy
to its role once (trust policy below). Secret material **never** lives here:
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
| `__INVENTORY_URL__` | `INVENTORY_URL` make variable (default `http://localhost:8001` — the in-task `service-b`; pass the other cloud's load-balancer URL for the cross-cloud flow) |

## SSM parameters (SecureString) referenced by ARN

| Parameter | Consumer | Value shape |
|---|---|---|
| `/otel-lab/postgres/password` | `postgres` container (`POSTGRES_PASSWORD`) | raw password of role `postgres` |
| `/otel-lab/database-url/service-a` | `service-a` (`DATABASE_URL`, db `orders`) | `postgresql://postgres:<pw>@localhost:5432/orders` |
| `/otel-lab/database-url/service-b` | `service-b` (`DATABASE_URL`, db `inventory`) | `postgresql://postgres:<pw>@localhost:5432/inventory` |
| `/otel-lab/grafana/prom-url` · `/prom-auth` | collector `prometheusremotewrite` | URL + basic-auth token |
| `/otel-lab/grafana/tempo-endpoint` · `/tempo-auth` | collector `otlphttp/tempo` | URL + basic-auth token |
| `/otel-lab/grafana/loki-endpoint` · `/loki-auth` | collector `otlphttp/loki` | URL + basic-auth token |
| `/otel-lab/cloudwatch-agent/config` | `cloudwatch-agent` (`CW_CONFIG_CONTENT`) | agent JSON |

The role/DB pair **must** match the image seed: the `postgres` container runs
`POSTGRES_USER=postgres` / `POSTGRES_DB=orders` with `init-scripts/` baked into
the internal image `otel-lab/postgres` (Fargate cannot bind-mount host
folders). `01-init.sh` additionally creates the `inventory` DB for `service-b`
— the live run of 2026-10-12 failed exactly here when the template pointed at a
non-existent `otel-lab` role (`FATAL: role "postgres" does not exist` → 500 →
503). Both URLs use `localhost` because `awsvpc` shares one network namespace
per task.

The `otel-collector` container also receives `AWS_REGION` as a plain
`environment` value and the six Grafana parameters above as `secrets`, because
its baked-in config (`deploy/collector/otel-collector-aws.yaml`) expands them
with `${env:...}`; without them the collector fails to start.

## Resource sizing

The task profile is `cpu 1024` / `memory 2048`. Per-container reservations must
fit inside it, so the sums are: CPU `256 + 256 + 256 + 256 = 1024` (the
best-effort `cloudwatch-agent` sidecar is left without a CPU reservation) and
memory `384 + 384 + 512 + 512 + 128 = 1920 < 2048`. Raise the task-level profile
before raising any per-container reservation.

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
