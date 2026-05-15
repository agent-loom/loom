#!/usr/bin/env python3
"""Package changed agent directories into distributable artifacts."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def detect_changed_agents() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1"],
        capture_output=True,
        text=True,
        check=False,
    )
    agents: set[str] = set()
    for line in result.stdout.strip().splitlines():
        if line.startswith("agents/"):
            parts = line.split("/")
            if len(parts) >= 2:
                agents.add(parts[1])
    return sorted(agents)


def package_agent(agent_id: str, output_dir: Path) -> Path:
    source = Path("agents") / agent_id
    if not source.exists():
        print(f"  error: agent directory not found: {source}", file=sys.stderr)
        sys.exit(1)

    dest = output_dir / agent_id
    dest.mkdir(parents=True, exist_ok=True)

    for item in source.rglob("*"):
        if item.name == "__pycache__" or item.suffix == ".pyc":
            continue
        rel = item.relative_to(source)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    return dest


def main():
    parser = argparse.ArgumentParser(description="Package agent directories")
    parser.add_argument(
        "--changed-only", action="store_true", help="Only package changed agents"
    )
    parser.add_argument("--agent", help="Specific agent ID to package")
    parser.add_argument("--output", default="dist/agents", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.agent == "changed":
        agents = detect_changed_agents()
        if not agents:
            print("No changed agents to package.")
            return
    elif args.agent:
        agents = [args.agent]
    elif args.changed_only:
        agents = detect_changed_agents()
        if not agents:
            print("No changed agents to package.")
            return
    else:
        agents = [
            d.name for d in Path("agents").iterdir()
            if d.is_dir() and d.name != "__pycache__"
        ]

    output_dir.mkdir(parents=True, exist_ok=True)

    for agent_id in agents:
        print(f"Packaging {agent_id}...")
        dest = package_agent(agent_id, output_dir)
        print(f"  -> {dest}")

    print(f"Packaged {len(agents)} agent(s) to {output_dir}")


if __name__ == "__main__":
    main()
