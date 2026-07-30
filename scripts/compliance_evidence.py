#!/usr/bin/env python3
"""Build SOC2/HIPAA/GDPR-oriented evidence summary from scan artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _count_bandit_issues(path: Path) -> int:
    data = _safe_json(path)
    return len(data.get("results", [])) if data else 0


def _count_bandit_blocking(path: Path) -> int:
    data = _safe_json(path)
    blocking = [
        r
        for r in data.get("results", [])
        if r.get("issue_confidence") in {"MEDIUM", "HIGH"}
        and r.get("issue_severity") in {"MEDIUM", "HIGH"}
    ]
    return len(blocking)


def _count_semgrep_issues(path: Path) -> int:
    data = _safe_json(path)
    if data.get("results") is not None:
        return len(data.get("results", []))
    count = 0
    for run in data.get("runs", []):
        count += len(run.get("results", []))
    return count


def _count_semgrep_blocking(path: Path) -> int:
    data = _safe_json(path)
    return len(
        [
            r
            for r in data.get("results", [])
            if r.get("extra", {}).get("severity") == "ERROR"
        ]
    )


def _count_checkov_issues(path: Path) -> int:
    data = _safe_json(path)
    summary = data.get("summary", {})
    if isinstance(summary, dict) and "failed" in summary:
        return int(summary.get("failed", 0))
    results = data.get("results", {})
    failed_checks = results.get("failed_checks", []) if isinstance(results, dict) else []
    return len(failed_checks)


def _count_zap_warnings(path: Path) -> int:
    data = _safe_json(path)
    site = data.get("site", [])
    if not site:
        return 0
    alerts = site[0].get("alerts", [])
    return len(alerts)


def build_report(artifacts_dir: Path) -> dict[str, Any]:
    sast_dir = artifacts_dir / "sast"
    dast_dir = artifacts_dir / "dast"
    iac_dir = artifacts_dir / "iac"
    audit_dir = artifacts_dir / "audit"

    required_files = [
        sast_dir / "bandit.json",
        sast_dir / "semgrep.json",
        iac_dir / "checkov.json",
        dast_dir / "zap-report.json",
        audit_dir / "git-audit-report.json",
    ]
    missing_artifacts = [str(path) for path in required_files if not path.exists()]

    bandit_count = _count_bandit_issues(sast_dir / "bandit.json")
    semgrep_count = _count_semgrep_issues(sast_dir / "semgrep.json")
    bandit_blocking = _count_bandit_blocking(sast_dir / "bandit.json")
    semgrep_blocking = _count_semgrep_blocking(sast_dir / "semgrep.json")
    checkov_count = _count_checkov_issues(iac_dir / "checkov.json")
    zap_count = _count_zap_warnings(dast_dir / "zap-report.json")
    git_audit_present = (audit_dir / "git-audit-report.json").exists()

    return {
        "status": "pass"
        if (
            bandit_blocking == 0
            and semgrep_blocking == 0
            and checkov_count == 0
            and zap_count == 0
            and not missing_artifacts
        )
        else "fail",
        "findings": {
            "bandit": bandit_count,
            "semgrep": semgrep_count,
            "bandit_blocking": bandit_blocking,
            "semgrep_blocking": semgrep_blocking,
            "checkov": checkov_count,
            "zap_alerts": zap_count,
            "git_audit_present": git_audit_present,
            "missing_artifacts": missing_artifacts,
        },
        "control_mapping": {
            "SOC2": {
                "CC7.1": "Vulnerability scanning (Bandit/Semgrep/Checkov/ZAP)",
                "CC6.6": "Security configuration hardening evidence in code + CI",
                "CC8.1": "Change traceability via Git audit report artifact",
            },
            "HIPAA": {
                "164.308(a)(1)(ii)(A)": "Risk analysis through recurring SAST/DAST/IaC scans",
                "164.312(c)(1)": "Integrity evidence through CI controls and immutable artifacts",
                "164.312(b)": "Audit controls through Git commit history artifacts",
            },
            "GDPR": {
                "Art.5(1)(f)": "Integrity/confidentiality controls with security scanning",
                "Art.24": "Accountability via reproducible security compliance workflow",
                "Art.32": "Security of processing evidenced by vulnerability management",
            },
        },
    }


def write_markdown(report: dict[str, Any], target: Path) -> None:
    findings = report["findings"]
    lines = [
        "# Security Compliance Evidence",
        "",
        f"- Overall status: `{report['status']}`",
        f"- Bandit findings: `{findings['bandit']}` (blocking: `{findings['bandit_blocking']}`)",
        f"- Semgrep findings: `{findings['semgrep']}` (blocking: `{findings['semgrep_blocking']}`)",
        f"- Checkov findings: `{findings['checkov']}`",
        f"- ZAP alerts: `{findings['zap_alerts']}`",
        f"- Git audit artifact present: `{findings['git_audit_present']}`",
    ]
    if findings["missing_artifacts"]:
        lines.append("- Missing artifacts:")
        for path in findings["missing_artifacts"]:
            lines.append(f"  - `{path}`")
    lines.extend(
        [
            "",
            "## Framework Mapping",
            "",
        ]
    )
    for framework, controls in report["control_mapping"].items():
        lines.append(f"### {framework}")
        for control, evidence in controls.items():
            lines.append(f"- `{control}`: {evidence}")
        lines.append("")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compliance evidence summary.")
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        help="Directory containing sast/dast/iac/audit artifact subfolders.",
    )
    parser.add_argument("--json-out", required=True, help="Output JSON report path.")
    parser.add_argument("--md-out", required=True, help="Output Markdown report path.")
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    report = build_report(artifacts_dir)
    json_target = Path(args.json_out)
    md_target = Path(args.md_out)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, md_target)
    print(f"Compliance evidence written: {json_target} and {md_target}")

    if report["status"] != "pass":
        raise SystemExit("Security compliance evidence indicates unresolved findings.")


if __name__ == "__main__":
    main()
