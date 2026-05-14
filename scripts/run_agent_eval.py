#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import subprocess
from pathlib import Path

from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.registry import AgentRegistry


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run an agent eval suite and write JSON report.")
    parser.add_argument("--agent", default="myj", help="Agent id to evaluate.")
    parser.add_argument("--registry-root", default="agents", help="Agent registry root.")
    parser.add_argument("--report", default="eval-report.json", help="Output report path.")
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Evaluate agents changed in git; falls back to --agent when none are detected.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="eval_all",
        help="Evaluate all registered agents.",
    )
    args = parser.parse_args()

    registry = AgentRegistry(Path(args.registry_root))

    if args.eval_all:
        agent_ids = [spec.agent_id for spec in registry.list_agents()]
    elif args.changed_only:
        agent_ids = _changed_agent_ids(Path(args.registry_root))
    else:
        agent_ids = []
    if not agent_ids:
        agent_ids = [args.agent]

    exit_code = 0
    for agent_id in agent_ids:
        report_path = (
            args.report
            if len(agent_ids) == 1
            else _agent_report_path(args.report, agent_id)
        )
        spec = registry.get(agent_id)
        report = await EvalRunner().run_agent_to_file(spec, report_path)
        print(
            f"eval {report.agent_id}: {report.passed}/{report.total} "
            f"pass_rate={report.pass_rate:.2f} required={report.required_pass_rate:.2f}"
        )
        if not report.gate_passed:
            exit_code = 1
    return exit_code


def _changed_agent_ids(registry_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []

    agent_ids: set[str] = set()
    for line in result.stdout.splitlines():
        path = Path(line)
        if len(path.parts) >= 2 and path.parts[0] == registry_root.name:
            agent_ids.add(path.parts[1])
    return sorted(agent_ids)


def _agent_report_path(report_path: str, agent_id: str) -> str:
    path = Path(report_path)
    return str(path.with_name(f"{path.stem}-{agent_id}{path.suffix or '.json'}"))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
