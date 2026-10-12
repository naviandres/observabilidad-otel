"""Verificacion OFFLINE del despliegue AWS ECS (Fase 2).

Determinista: no toca ninguna API de AWS. Valida que los artefactos
versionables del despliegue Fargate existen, el template de task definition
tiene la forma registrable y el YAML del collector ADOT apunta a los backends
AWS. Complementa a `make validate-collector` (valida el YAML por arranque).
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_DEF = ROOT / "deploy" / "aws" / "task-definition.json"
RENDERED_FIXTURE = ROOT / "tests" / "data" / "aws" / "task-definition.json"
COLLECTOR_AWS_YAML = ROOT / "deploy" / "collector" / "otel-collector-aws.yaml"
MAKEFILE = ROOT / "Makefile"
GITIGNORE = ROOT / ".gitignore"
TUNNEL = ROOT / "scripts" / "tunnel.ps1"
DOCS = ROOT / "docs" / "aws-ecs.md"

CONTAINERS = {"service-a", "service-b", "postgres", "otel-collector", "cloudwatch-agent"}
EXPECTED_PORTS = {
    "service-a": {8080},
    "service-b": {8081},
    "otel-collector": {4317},
}


def _task_def(data_path=TASK_DEF):
    with open(data_path, encoding="utf-8") as fh:
        return json.load(fh)


def _containers(task_def):
    return {c["name"]: c for c in task_def["containerDefinitions"]}


def test_task_definition_family_and_launch_type():
    td = _task_def()
    assert td["family"] == "otel-lab"
    assert "FARGATE" in td["requiresCompatibilities"]
    assert td["networkMode"] == "awsvpc"
    assert td["cpu"] == "1024"
    assert td["memory"] == "2048"
    assert td["runtimePlatform"]["operatingSystemFamily"] == "LINUX"


def test_task_definition_has_exactly_the_5_expected_containers():
    td = _task_def()
    names = set(_containers(td))
    assert names == CONTAINERS


def test_no_latest_tags_anywhere():
    td = _task_def()
    tag_re = re.compile(r":latest\b")
    for c in _containers(td).values():
        assert not tag_re.search(c["image"]), f"{c['name']} usa :latest"
    # postgres y cw-agent son publicos fijados; los propios usan __IMAGE_TAG__
    collector = _containers(td)["otel-collector"]
    assert "__IMAGE_TAG__" in collector["image"]
    assert collector["essential"] is True


def test_port_mappings_of_app_services():
    ports = _containers(_task_def())
    for name, expected in EXPECTED_PORTS.items():
        got = {pm["containerPort"] for pm in ports[name]["portMappings"]}
        assert got == expected


def test_roles_and_exec_command():
    td = _task_def()
    assert "otel-lab-exec-ecs" in td["executionRoleArn"]
    assert "otel-lab-task-ecs" in td["taskRoleArn"]
    assert "__AWS_ACCOUNT_ID__" in td["taskRoleArn"]
    assert td["enableExecuteCommand"] is True


def test_all_containers_use_awslogs_driver():
    td = _task_def()
    for c in _containers(td).values():
        lc = c.get("logConfiguration", {})
        assert lc.get("logDriver") == "awslogs", f"{c['name']} no usa awslogs"
        assert lc["options"]["awslogs-group"].startswith("/otel/labs/ecs-platform/")
        assert lc["options"]["awslogs-create-group"] == "true"


def test_cloudwatch_agent_sidecar_shape():
    cw = _containers(_task_def())["cloudwatch-agent"]
    assert cw["essential"] is False
    assert cw["image"].startswith("public.ecr.aws/cloudwatch-agent/cloudwatch-agent:")
    secret = cw["secrets"][0]
    assert secret["name"] == "CW_CONFIG_CONTENT"
    assert "/otel-lab/cloudwatch-agent/config" in secret["valueFrom"]


def test_postgres_volume_and_healthcheck():
    pg = _containers(_task_def())["postgres"]
    assert pg["mountPoints"][0]["sourceVolume"] == "postgres-data"
    assert "pg_isready" in pg["healthCheck"]["command"][1]
    assert any(v["name"] == "postgres-data" for v in _task_def()["volumes"])


def test_rendered_fixture_matches_template():
    """El fixture tests/data/aws es exactamente el template con tokens resueltos:
    lo que `make aws-start` enviara a register-task-definition."""
    expected = _task_def()
    rendered = _task_def(RENDERED_FIXTURE)
    tpl = json.dumps(expected, sort_keys=True, indent=2)
    data = json.dumps(rendered, sort_keys=True, indent=2)
    for token, value in [
        ("__AWS_ACCOUNT_ID__", "621236283643"),
        ("__AWS_REGION__", "us-east-1"),
        ("__ECR_REGISTRY__", "621236283643.dkr.ecr.us-east-1.amazonaws.com"),
        ("__IMAGE_TAG__", "0.1.0"),
    ]:
        tpl = tpl.replace(token, value)
    assert tpl == data


def test_makefile_exposes_aws_lifecycle_targets():
    text = MAKEFILE.read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z][a-z-]+):", text, re.M))
    required = {
        "aws-net-create", "aws-net-destroy", "aws-push", "aws-start",
        "aws-stop", "aws-destroy", "tunnel", "validate-collector",
    }
    assert required <= targets
    assert re.search(r"^IMAGE_TAG\s+\?=\s+0\.1\.0\s*$", text, re.M)
    # Ninguna imagen construida/pusheada usa :latest (los comentarios del
    # Makefile SI mencionan la palabra, asi que se mira el contexto de comando).
    assert not re.search(r"(?:build -t|push)\s+\S+:latest\b", text)


def test_collector_yaml_aws_targets_aws_backends():
    text = COLLECTOR_AWS_YAML.read_text(encoding="utf-8")
    for marker in ("awsxray:", "awsemf:", "awscloudwatchlogs:"):
        assert marker in text, f"falta el exporter {marker!r} en el YAML ADOT"
    assert "xray:" in text  # pipeline de trazas tiene salida xray
    assert "emf:" in text   # pipeline de metricas tiene salida EMF
    assert "resourcedetection" in text and "ecs" in text


def test_tunnel_and_docs_present():
    assert TUNNEL.exists()
    assert DOCS.exists()
    assert "awslogs" in DOCS.read_text(encoding="utf-8")
    rendered_ignored = "task-definition.rendered.json" in GITIGNORE.read_text(encoding="utf-8")
    assert rendered_ignored


def test_no_secrets_in_versioned_aws_artifacts():
    """Codigo versionado sin credenciales: nada de AKIA ni claves privadas."""
    files = [
        TASK_DEF, COLLECTOR_AWS_YAML, MAKEFILE, TUNNEL, DOCS,
        GITIGNORE, RENDERED_FIXTURE,
    ]
    secret_patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY"),
        re.compile(r"(aws_secret_access_key|aws_access_key_id)\s*=\s*\S"),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in secret_patterns:
            assert not pat.search(text), f"{path.name} parece contener un secreto: {pat.pattern}"