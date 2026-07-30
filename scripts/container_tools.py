#!/usr/bin/env python3
"""Container management helpers for local operations and log analysis."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_COMPOSE_FILE = "infrastructure/docker-compose.yml"
LOG_LEVEL_PATTERNS = {
    "critical": re.compile(r"\bcritical\b", re.IGNORECASE),
    "error": re.compile(r"\berror\b|\bexception\b|\btraceback\b", re.IGNORECASE),
    "warning": re.compile(r"\bwarn(?:ing)?\b", re.IGNORECASE),
}


def run_command(command: Sequence[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command and return CompletedProcess."""
    return subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=capture_output,
    )


def compose_base(compose_file: str, project_name: str | None) -> list[str]:
    base = ["docker", "compose", "-f", compose_file]
    if project_name:
        base.extend(["-p", project_name])
    return base


def cmd_ps(args: argparse.Namespace) -> int:
    cmd = compose_base(args.compose_file, args.project_name) + ["ps"]
    result = run_command(cmd, capture_output=False)
    return result.returncode


def cmd_exec(args: argparse.Namespace) -> int:
    cmd = compose_base(args.compose_file, args.project_name) + ["exec"]
    if args.user:
        cmd.extend(["-u", args.user])
    cmd.append(args.service)
    cmd.extend(args.command)
    result = run_command(cmd, capture_output=False)
    return result.returncode


def cmd_logs(args: argparse.Namespace) -> int:
    cmd = compose_base(args.compose_file, args.project_name) + ["logs", "--no-color"]
    if args.follow:
        cmd.append("-f")
    if args.tail is not None:
        cmd.extend(["--tail", str(args.tail)])
    if args.since:
        cmd.extend(["--since", args.since])
    if args.service:
        cmd.append(args.service)

    result = run_command(cmd, capture_output=args.output is not None)
    if args.output and result.stdout is not None:
        Path(args.output).write_text(result.stdout, encoding="utf-8")
        print(f"Saved logs to: {args.output}")
    return result.returncode


@dataclass
class LogSummary:
    total_lines: int
    level_counts: Counter[str]
    service_counts: Counter[str]
    top_error_lines: dict[str, list[str]]


def analyze_lines(lines: list[str]) -> LogSummary:
    level_counts: Counter[str] = Counter()
    service_counts: Counter[str] = Counter()
    top_error_lines: dict[str, list[str]] = defaultdict(list)

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue

        service = "unknown"
        if "|" in line:
            service = line.split("|", 1)[0].strip()
        service_counts[service] += 1

        matched_level: str | None = None
        for level, pattern in LOG_LEVEL_PATTERNS.items():
            if pattern.search(line):
                matched_level = level
                level_counts[level] += 1
                break

        if matched_level in {"critical", "error"} and len(top_error_lines[service]) < 3:
            top_error_lines[service].append(line)

    return LogSummary(
        total_lines=len(lines),
        level_counts=level_counts,
        service_counts=service_counts,
        top_error_lines=dict(top_error_lines),
    )


def load_log_lines(args: argparse.Namespace) -> list[str]:
    if args.log_file:
        return Path(args.log_file).read_text(encoding="utf-8").splitlines()

    cmd = compose_base(args.compose_file, args.project_name) + ["logs", "--no-color"]
    if args.tail is not None:
        cmd.extend(["--tail", str(args.tail)])
    if args.since:
        cmd.extend(["--since", args.since])
    if args.service:
        cmd.append(args.service)

    result = run_command(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr or "Failed to read compose logs."
        print(stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return (result.stdout or "").splitlines()


def cmd_analyze_logs(args: argparse.Namespace) -> int:
    lines = load_log_lines(args)
    summary = analyze_lines(lines)

    result = {
        "total_lines": summary.total_lines,
        "level_counts": dict(summary.level_counts),
        "top_services_by_volume": summary.service_counts.most_common(5),
        "sample_error_lines": summary.top_error_lines,
    }

    print(json.dumps(result, ensure_ascii=True, indent=2))

    if args.fail_on_error and (summary.level_counts["critical"] > 0 or summary.level_counts["error"] > 0):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Container interaction and log analysis helper")
    parser.add_argument("--compose-file", default=DEFAULT_COMPOSE_FILE, help="Compose file path")
    parser.add_argument("-p", "--project-name", default=None, help="Compose project name override")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    ps_parser = subparsers.add_parser("ps", help="Show container status")
    ps_parser.set_defaults(handler=cmd_ps)

    exec_parser = subparsers.add_parser("exec", help="Execute command inside a service container")
    exec_parser.add_argument("service", help="Compose service name")
    exec_parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run inside container")
    exec_parser.add_argument("--user", default=None, help="Container user (for docker compose exec -u)")
    exec_parser.set_defaults(handler=cmd_exec)

    logs_parser = subparsers.add_parser("logs", help="Print or export compose logs")
    logs_parser.add_argument("--service", default=None, help="Optional single service name")
    logs_parser.add_argument("--tail", type=int, default=200, help="Tail N lines")
    logs_parser.add_argument("--since", default=None, help='Filter logs by duration, e.g. "10m", "1h"')
    logs_parser.add_argument("--follow", action="store_true", help="Follow log output")
    logs_parser.add_argument("--output", default=None, help="Write logs to output file")
    logs_parser.set_defaults(handler=cmd_logs)

    analyze_parser = subparsers.add_parser("analyze-logs", help="Analyze compose logs for anomalies")
    analyze_parser.add_argument("--log-file", default=None, help="Analyze an exported log file")
    analyze_parser.add_argument("--service", default=None, help="Optional single service name")
    analyze_parser.add_argument("--tail", type=int, default=500, help="Tail N lines when reading from compose")
    analyze_parser.add_argument("--since", default=None, help='Filter logs by duration, e.g. "10m", "1h"')
    analyze_parser.add_argument("--fail-on-error", action="store_true", help="Exit non-zero if errors/critical found")
    analyze_parser.set_defaults(handler=cmd_analyze_logs)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "exec" and not args.command:
        parser.error("exec requires a command, e.g. exec api_gateway sh")

    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
