#!/usr/bin/env python3
"""Generate Git audit-trail evidence as JSON/Markdown artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_rows(limit: int) -> list[dict[str, str]]:
    output = _run_git(
        [
            "log",
            f"-n{limit}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%s",
        ]
    )
    rows: list[dict[str, str]] = []
    if not output:
        return rows
    for line in output.splitlines():
        commit_hash, author, email, date, subject = line.split("\x1f", maxsplit=4)
        rows.append(
            {
                "commit": commit_hash,
                "author": author,
                "email": email,
                "date": date,
                "subject": subject,
            }
        )
    return rows


def build_report(limit: int) -> dict[str, object]:
    head = _run_git(["rev-parse", "HEAD"])
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    remote = _run_git(["config", "--get", "remote.origin.url"]) or "unknown"
    commits = _commit_rows(limit)
    unique_authors = sorted({f"{row['author']} <{row['email']}>" for row in commits})
    return {
        "head": head,
        "branch": branch,
        "remote": remote,
        "commit_count_included": len(commits),
        "unique_authors": unique_authors,
        "commits": commits,
    }


def write_markdown(report: dict[str, object], target: Path) -> None:
    commits = report["commits"]
    lines = [
        "# Git Audit Report",
        "",
        f"- Branch: `{report['branch']}`",
        f"- HEAD: `{report['head']}`",
        f"- Remote: `{report['remote']}`",
        f"- Commits included: `{report['commit_count_included']}`",
        "",
        "## Unique Authors",
        "",
    ]
    for author in report["unique_authors"]:
        lines.append(f"- {author}")
    lines.extend(["", "## Recent Commits", ""])
    for commit in commits:
        lines.append(
            f"- `{commit['commit'][:12]}` | {commit['date']} | {commit['author']} | {commit['subject']}"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Git audit evidence artifacts.")
    parser.add_argument("--json-out", required=True, help="Output JSON artifact path.")
    parser.add_argument("--md-out", required=True, help="Output Markdown artifact path.")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Number of latest commits to include (default: 200).",
    )
    args = parser.parse_args()

    report = build_report(args.limit)
    json_target = Path(args.json_out)
    md_target = Path(args.md_out)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.parent.mkdir(parents=True, exist_ok=True)

    json_target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, md_target)
    print(f"Git audit artifacts written: {json_target} and {md_target}")


if __name__ == "__main__":
    main()
