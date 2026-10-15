"""Feature-quality contracts for the repository documentation set (Fase 5).

Covers the acceptance criteria of the "calidad del repositorio y reporte" task:

- Criterion 1: docs/report/report.pdf >= 5 pages, both clouds present.
- Criterion 2: ARCHITECTURE.md + >= 7 ADRs (incl. AWS and GCP provider ADRs) +
  CONTRIBUTING.md + COST.md + README quickstart.
- Criterion 3: CI deploy-aws / deploy-gcp jobs gated (never auto-apply on
  push); Makefile `report` target reproducible and every Makefile target
  documented in CONTRIBUTING.md.
- Criterion 4: visual evidence as PLACEHOLDERS (reserved files with
  PLACEHOLDER-HUMAN-REQUIRED marker + human table).

Plus two regression guards for repo hygiene: the anti-internal-jargon gate
(documented in CONTRIBUTING.md) and a no-secrets scan over the docs set.
All assertions are pure file-content checks: offline, no cloud, no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ADR_DIR = DOCS / "adr"
IMG_DIR = DOCS / "evidence" / "img"
REPORT_PDF = DOCS / "report" / "report.pdf"
REPORT_MD = DOCS / "report" / "report.md"
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Criterion 2 — documentation set
# ---------------------------------------------------------------------------


def test_architecture_contributing_cost_exist() -> None:
    for name in ("ARCHITECTURE.md", "CONTRIBUTING.md", "COST.md"):
        assert (ROOT / name).is_file(), f"missing {name}"


def test_readme_has_quickstart_local_mode() -> None:
    text = _read(ROOT / "README.md")
    assert "## Modo 1 — Local (docker compose)" in text
    assert "docker compose up --build -d" in text


def test_adr_count_at_least_seven_numbered() -> None:
    adrs = sorted(p.name for p in ADR_DIR.glob("*.md") if re.match(r"0\d{2}-", p.name))
    assert len(adrs) >= 7, f"only {len(adrs)} ADRs: {adrs}"


def test_adrs_include_aws_and_gcp_provider_decisions() -> None:
    aws = gcp = 0
    for p in ADR_DIR.glob("0*.md"):
        text = _read(p)
        if re.search(r"Proveedor(es)?:.*AWS", text):
            aws += 1
        if re.search(r"Proveedor(es)?:.*GCP", text):
            gcp += 1
    assert aws >= 1, "no AWS-provider ADR"
    assert gcp >= 1, "no GCP-provider ADR"


def test_every_adr_has_madr_sections() -> None:
    for p in ADR_DIR.glob("0*.md"):
        text = _read(p)
        for section in ("## Contexto", "## Decisión", "## Consecuencias", "## Evidencia"):
            assert section in text, f"{p.name} missing {section}"


# ---------------------------------------------------------------------------
# Criterion 1 — report PDF
# ---------------------------------------------------------------------------


def _pdf_page_count(path: Path) -> int:
    """Page count of the report PDF. Prefers pypdf (dev dep); falls back to a
    raw-object scan for PDFs without compressed object streams."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except ImportError:  # pragma: no cover - pypdf is pinned in requirements-dev
        data = path.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
        if pages == 0:
            counts = re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", data)
            pages = max(int(c) for c in counts) if counts else 0
        return pages


def test_report_pdf_exists_and_at_least_five_pages() -> None:
    assert REPORT_PDF.is_file(), "docs/report/report.pdf missing"
    n = _pdf_page_count(REPORT_PDF)
    assert n >= 5, f"report.pdf has only {n} pages"


def test_report_markdown_is_the_source_and_covers_both_clouds() -> None:
    assert REPORT_MD.is_file(), "markdown source for the PDF must be versioned"
    text = _read(REPORT_MD)
    for token in ("X-Ray", "CloudWatch", "Cloud Trace", "Cloud Monitoring", "Fargate", "GKE"):
        assert token in text, f"report source does not mention {token}"


def test_report_declares_overhead_and_limitations() -> None:
    text = _read(REPORT_MD)
    assert "4,94" in text, "headline overhead figure missing"
    assert "outlier" in text.lower(), "declared outlier missing"
    assert "Limitaciones" in text or "límites" in text.lower(), "limitations section missing"


# ---------------------------------------------------------------------------
# Criterion 3 — CI deploy jobs + Makefile documentation
# ---------------------------------------------------------------------------


def test_deploy_workflow_has_both_jobs_dispatch_only() -> None:
    text = _read(ROOT / ".github" / "workflows" / "deploy.yml")
    assert re.search(r"^  deploy-aws:", text, flags=re.MULTILINE)
    assert re.search(r"^  deploy-gcp:", text, flags=re.MULTILINE)
    # never auto-applies on push: the only trigger is workflow_dispatch
    assert "workflow_dispatch" in text
    assert re.search(r"^on:\s*\n\s*workflow_dispatch:", text, flags=re.MULTILINE)
    assert not re.search(r"^on:[\s\S]*?\n  push:", text, flags=re.MULTILINE)


def test_deploy_workflow_gates_apply_behind_environments_and_guard() -> None:
    text = _read(ROOT / ".github" / "workflows" / "deploy.yml")
    assert "environment: aws-production" in text
    assert "environment: gcp-production" in text
    assert "guard_cost.py --provider aws" in text
    assert "guard_cost.py --provider google" in text


def test_plan_workflows_still_never_apply() -> None:
    """The plan-only contract of the PR workflows must survive alongside the
    new deploy workflow (they are separate files; deploy.yml does not replace
    them)."""
    for name in ("terraform-plan.yml", "terraform-plan-gcp.yml"):
        text = _read(ROOT / ".github" / "workflows" / name)
        run_lines = "\n".join(re.findall(r"^\s*run:\s*(.+)$", text, flags=re.MULTILINE))
        assert "apply" not in run_lines.lower(), f"{name} must never apply"


def test_makefile_report_target_pinned_and_reproducible() -> None:
    assert re.search(r"^report:", MAKEFILE, flags=re.MULTILINE)
    assert "pandoc/latex:" in MAKEFILE
    assert "pandoc/latex:latest" not in MAKEFILE
    assert "docs/report/report.md" in MAKEFILE and "docs/report/report.pdf" in MAKEFILE


def test_every_makefile_target_is_documented_in_contributing() -> None:
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):\s*$", MAKEFILE, flags=re.MULTILINE))
    assert "report" in targets
    doc = _read(ROOT / "CONTRIBUTING.md")
    undocumented = {t for t in targets if f"`{t}`" not in doc}
    assert not undocumented, f"targets missing from CONTRIBUTING table: {undocumented}"


# ---------------------------------------------------------------------------
# Criterion 4 — placeholder figures
# ---------------------------------------------------------------------------


def test_placeholder_figures_reserved_with_marker() -> None:
    svgs = sorted(IMG_DIR.glob("*.svg"))
    assert len(svgs) >= 5, f"only {len(svgs)} placeholder figures"
    for p in svgs:
        assert "PLACEHOLDER-HUMAN-REQUIRED" in _read(p), f"{p.name} lacks the marker"


def test_placeholder_coverage_both_clouds_and_local() -> None:
    names = " ".join(p.name for p in IMG_DIR.glob("*.svg"))
    aws = len(re.findall(r"fig-a\d", names))
    gcp = len(re.findall(r"fig-g\d", names))
    assert aws >= 5, f"only {aws} AWS figures"
    assert gcp >= 5, f"only {gcp} GCP figures"
    assert "fig-l1-jaeger" in names and "fig-l2-grafana" in names


def test_placeholder_table_documents_what_the_human_must_supply() -> None:
    table = _read(IMG_DIR / "README.md")
    for p in IMG_DIR.glob("*.svg"):
        assert p.name.split(".")[0] in table, f"{p.name} not listed in img/README.md"


# ---------------------------------------------------------------------------
# Hygiene regressions — anti-jargon gate and no-secrets
# ---------------------------------------------------------------------------

GATE_PATTERNS = [
    r"feature #",
    r"feat/[0-9]+",
    r"#[0-9]+",
    r"harness",
    r"sub-tarea",
    r"leader spec",
    r"\bpata\b",
    r"progress/",
    r"feature_manager|features_list",
]
GATE_DIRS = ["docs", "deploy", "dashboards", "helm", "infra", "k6", "scripts", "bench"]
GATE_ROOT_FILES = ["README.md", "COST.md", "ARCHITECTURE.md", "Makefile", ".env.example"]
GATE_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".tf", ".txt", ".csv", ".ps1", ".py", ".sh", ".sql"}
# CONTRIBUTING.md documents the gate itself (it must quote the forbidden
# tokens); the raw credentials dump of Phase 0 is a CLI volcado, not docs.
GATE_EXCLUDED_NAMES = {"00-credentials.txt"}
GATE_EXCLUDED_PARTS = {".terraform", "__pycache__"}


def _gate_files():
    files = [ROOT / f for f in GATE_ROOT_FILES if (ROOT / f).is_file()]
    for d in GATE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if (
                p.is_file()
                and p.suffix in GATE_SUFFIXES
                and p.name not in GATE_EXCLUDED_NAMES
                and not GATE_EXCLUDED_PARTS & set(p.parts)
            ):
                files.append(p)
    for p in (ROOT / ".github").rglob("*"):
        if p.suffix in {".yml", ".yaml", ".md"}:
            files.append(p)
    return files


def test_no_internal_jargon_in_deliverable() -> None:
    """The deliverable must not name the internal process that produced it
    (rule documented in CONTRIBUTING.md §6)."""
    offenders = []
    for f in _gate_files():
        text = _read(f)
        for pat in GATE_PATTERNS:
            for m in re.finditer(pat, text):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"{f.relative_to(ROOT)}:{line}: {pat}")
    assert not offenders, "internal jargon found:\n" + "\n".join(offenders)


def test_contributing_documents_the_jargon_gate() -> None:
    doc = _read(ROOT / "CONTRIBUTING.md")
    assert "git grep -nE" in doc, "the anti-jargon gate command must be documented"
    assert "PLACEHOLDER-HUMAN-REQUIRED" in doc or "placeholders" in doc.lower()


def test_new_docs_contain_no_secrets() -> None:
    """No AWS access-key ids, no PEM blocks, no literal passwords in the
    documentation set written for this deliverable."""
    targets = [ROOT / "ARCHITECTURE.md", ROOT / "CONTRIBUTING.md", ROOT / "COST.md", REPORT_MD]
    targets += list(ADR_DIR.glob("*.md"))
    targets += list(IMG_DIR.glob("*.svg"))
    patterns = [rb"AKIA[0-9A-Z]{16}", rb"BEGIN (RSA|EC|OPENSSH) PRIVATE KEY", rb"aws_secret_access_key\s*=\s*\S"]
    for f in targets:
        data = f.read_bytes()
        for pat in patterns:
            assert not re.search(pat, data), f"secret-like match ({pat}) in {f}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
