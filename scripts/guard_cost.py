#!/usr/bin/env python3
"""guard_cost.py — gate de lista negra de coste sobre un plan de Terraform.

Lee el JSON de `terraform show -json` (stdin o --plan-file) y falla (exit 1)
si algun recurso del provider indicado esta en la lista negra del PLAN §3.2
o incumple una condicion de coste (Autopilot, pool no-spot, min-instances>0).

Uso:
    terraform show -json tfplan | python scripts/guard_cost.py --provider google
    python scripts/guard_cost.py --plan-file plan.json --provider google

Providers:
    google  — lista negra GCP del PLAN §3.2 (Cloud NAT, GKE Autopilot, Cloud
              SQL, Cloud Load Balancing gestionado, Cloud Run con
              min-instances>0, node pools no-spot, IP reservada permanente).
    aws     — lista negra AWS del PLAN §3.2 (ALB, NAT Gateway, VPC endpoints
              de interfaz, EIP, EKS, RDS, WAF, Secrets Manager, CloudFront,
              API Gateway, Kinesis, Firehose, ElastiCache, Aurora, MSK,
              DataSync, App Runner, Lightsail, Cloud9, Redshift, SageMaker,
              Bedrock, OpenSearch). Stub: mismo contrato; el root AWS aun no
              produce planes que lo ejerciten.

Disenado para CI y pre-commit: sin dependencias externas (solo stdlib).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Listas negras (PLAN §3.2)
# ---------------------------------------------------------------------------

# Tipos vetados por presencia: basta con que aparezcan en el plan.
GOOGLE_FORBIDDEN_TYPES: dict[str, str] = {
    "google_compute_router_nat": "Cloud NAT (~$0.045/h por gateway + procesamiento; "
    "sustituido por private Google Access)",
    "google_sql_database_instance": "Cloud SQL (DB gestionada de pago; Postgres vive como Pod)",
    "google_filestore_instance": "Filestore (de pago; sin necesidad de NFS)",
    "google_compute_target_https_proxy": "Cloud Load Balancing gestionado (el acceso es "
    "IAP/port-forward, sin LB)",
    "google_compute_target_http_proxy": "Cloud Load Balancing gestionado (HTTP)",
    "google_compute_global_address": "IP externa reservada permanente / LB global "
    "($0.01/h por IP reservada sin usar; solo IP efimera)",
    "google_compute_forwarding_rule": "Cloud Load Balancing gestionado (forwarding rules)",
}

# Cloud Run: vetado solo con min-instances > 0 (reserva = coste continuo).
CLOUDRUN_TYPES = {"google_cloud_run_service", "google_cloud_run_v2_service"}

# GKE Autopilot: google_container_cluster con enable_autopilot/autopilot true.
GKE_CLUSTER_TYPE = "google_container_cluster"
GKE_NODE_POOL_TYPE = "google_container_node_pool"

AWS_FORBIDDEN_TYPES: dict[str, str] = {
    "aws_lb": "Application Load Balancer (~$16.50/mes, sin free tier)",
    "aws_lb_listener": "Application Load Balancer (listener del ALB vetado)",
    "aws_nat_gateway": "NAT Gateway (~$32/mes + $0.045/GB)",
    "aws_vpc_endpoint": "VPC endpoints de interfaz (~$7.20/mes cada uno)",
    "aws_eip": "Elastic IP / IPv4 publico 24/7 ($0.005/h)",
    "aws_eip_association": "Elastic IP asociada (coste continuo)",
    "aws_eks_cluster": "Amazon EKS ($0.10/h por cluster + nodos)",
    "aws_eks_node_group": "Amazon EKS (nodos del cluster vetado)",
    "aws_db_instance": "Amazon RDS gestionado (consumiria el presupuesto de horas)",
    "aws_db_cluster_snapshot": "Amazon RDS/Aurora (perifericos del servicio vetado)",
    "aws_wafv2_web_acl": "AWS WAF (~$5/mes + reglas)",
    "aws_waf_web_acl": "AWS WAF clasico (~$5/mes + reglas)",
    "aws_secretsmanager_secret": "Secrets Manager ($0.40/secreto/mes; usar SSM Parameter Store)",
    "aws_cloudfront_distribution": "CloudFront (fuera del free plan)",
    "aws_api_gateway_rest_api": "API Gateway (fuera del free plan)",
    "aws_kinesis_stream": "Kinesis (fuera del free plan)",
    "aws_kinesis_firehose_delivery_stream": "Firehose (fuera del free plan)",
    "aws_elasticache_cluster": "ElastiCache (fuera del free plan)",
    "aws_msk_cluster": "MSK (fuera del free plan)",
    "aws_redshift_cluster": "Redshift (fuera del free plan)",
    "aws_apprunner_service": "App Runner (fuera del free plan)",
    "aws_lightsail_instance": "Lightsail (de pago directo)",
    "aws_cloud9_environment_ec2": "Cloud9 (de pago directo)",
    "aws_sagemaker_domain": "SageMaker (fuera del free plan)",
    "aws_opensearch_domain": "OpenSearch (fuera del free plan)",
    "aws_cloudwatch_log_metric_filter_and_alarm": "CloudWatch Container Insights / "
    "metric alarms de pago por separado",
}


# ---------------------------------------------------------------------------
# Walk del plan JSON
# ---------------------------------------------------------------------------


def _iter_module_resources(module: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Recorre planned_values.root_module y sus child_modules (anidados)."""
    for res in module.get("resources", []) or []:
        yield res
    for child in module.get("child_modules", []) or []:
        yield from _iter_module_resources(child)


def _planned_resources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    root = plan.get("planned_values", {}).get("root_module")
    if not root:
        return []
    return list(_iter_module_resources(root))


def _changed_resources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """resource_changes con acciones relevantes (no no-op/read)."""
    out = []
    for rc in plan.get("resource_changes", []) or []:
        actions = rc.get("change", {}).get("actions", [])
        if any(a in ("create", "update", "replace", "delete_then_create") for a in actions):
            after = rc.get("change", {}).get("after")
            if isinstance(after, dict):
                out.append(
                    {
                        "address": rc.get("address", "?"),
                        "type": rc.get("type", "?"),
                        "values": after,
                    }
                )
    return out


def _collect(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Une planned_values y resource_changes, deduplicando por address."""
    seen: dict[str, dict[str, Any]] = {}
    for res in _planned_resources(plan) + _changed_resources(plan):
        addr = res.get("address", "?")
        if addr not in seen:
            seen[addr] = res
    return list(seen.values())


# ---------------------------------------------------------------------------
# Checks condicionales (atributos, no solo tipo)
# ---------------------------------------------------------------------------


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _min_instances_gt_zero(values: dict[str, Any]) -> bool:
    """Cloud Run con min-instances > 0 (v1 por anotacion, v2 por scaling)."""
    ann = (
        values.get("template", [{}])[0]
        .get("metadata", [{}])[0]
        .get("annotations", {})
    )
    for key in ("autoscaling.knative.dev/minScale", "autoscaling.knative.dev/min-scale"):
        raw = (ann or {}).get(key)
        try:
            if raw is not None and int(str(raw)) > 0:
                return True
        except ValueError:
            continue
    for tmpl in values.get("template", []) or []:
        scaling = tmpl.get("scaling") or {}
        raw = scaling.get("min_instance_count")
        try:
            if raw is not None and int(str(raw)) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _cluster_autopilot(values: dict[str, Any]) -> bool:
    if _is_true(values.get("enable_autopilot")) or _is_true(values.get("autopilot")):
        return True
    ap = values.get("autopilot")
    return isinstance(ap, dict) and bool(ap)


def _node_pool_missing_spot(values: dict[str, Any]) -> bool:
    """Pool sin spot=true explicito: fail-closed (incluye valores unknown)."""
    node_cfg = (values.get("node_config") or [{}])[0]
    return not _is_true(node_cfg.get("spot"))


def _inline_pools_without_spot(values: dict[str, Any]) -> list[int]:
    """Indices de node_pool[] inline en el cluster sin spot=true."""
    bad: list[int] = []
    for i, pool in enumerate(values.get("node_pool") or []):
        cfg = (pool.get("node_config") or [{}])[0] if isinstance(pool, dict) else {}
        if not _is_true(cfg.get("spot")):
            bad.append(i)
    return bad


# ---------------------------------------------------------------------------
# Evaluacion
# ---------------------------------------------------------------------------


def check_plan(plan: dict[str, Any], provider: str) -> list[str]:
    """Devuelve la lista de violaciones (vacia = plan limpio)."""
    violations: list[str] = []
    forbidden = GOOGLE_FORBIDDEN_TYPES if provider == "google" else AWS_FORBIDDEN_TYPES

    for res in _collect(plan):
        addr = res.get("address", "?")
        rtype = res.get("type", "?")
        values = res.get("values") or {}

        reason = forbidden.get(rtype)
        if reason is not None:
            violations.append(f"{addr}: {rtype} — {reason}")
            continue

        if provider != "google":
            continue

        if rtype == GKE_CLUSTER_TYPE and _cluster_autopilot(values):
            violations.append(
                f"{addr}: {rtype} con Autopilot habilitado — recargo por pod "
                "(usar GKE Standard con pool spot)"
            )
        if rtype == GKE_CLUSTER_TYPE:
            for i in _inline_pools_without_spot(values):
                violations.append(
                    f"{addr} node_pool[{i}]: pool no-spot — paga on-demand completo "
                    "(lista negra §3.2)"
                )
        if rtype == GKE_NODE_POOL_TYPE and _node_pool_missing_spot(values):
            violations.append(
                f"{addr}: {rtype} sin spot=true — node pools no-spot pagan on-demand "
                "(lista negra §3.2)"
            )
        if rtype in CLOUDRUN_TYPES and _min_instances_gt_zero(values):
            violations.append(
                f"{addr}: {rtype} con min-instances > 0 — reserva instancias = "
                "coste continuo (seria con min=0)"
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["google", "aws"], required=True)
    parser.add_argument(
        "--plan-file",
        type=argparse.FileType("r", encoding="utf-8"),
        default=None,
        help="JSON de `terraform show -json`; por defecto se lee stdin.",
    )
    args = parser.parse_args(argv)

    src = args.plan_file if args.plan_file else sys.stdin
    try:
        plan = json.load(src)
    except json.JSONDecodeError as exc:
        print(f"guard_cost: entrada no es JSON de plan valido: {exc}", file=sys.stderr)
        return 2

    if "planned_values" not in plan and "resource_changes" not in plan:
        print(
            "guard_cost: el JSON no parece un plan de terraform show -json "
            "(faltan planned_values y resource_changes)",
            file=sys.stderr,
        )
        return 2

    violations = check_plan(plan, args.provider)
    if violations:
        print(f"guard_cost: {len(violations)} violacion(es) de la lista negra "
              f"(provider={args.provider}):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(f"guard_cost: OK — 0 violaciones (provider={args.provider})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
