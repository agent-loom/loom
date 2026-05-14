#!/usr/bin/env python
import sys
from pathlib import Path

from agent_platform.registry.loader import ManifestLoader


def main() -> int:
    loader = ManifestLoader()
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        paths = sorted(Path("agents").glob("*/manifest.yaml"))

    for path in paths:
        spec = loader.load_file(path)
        print(f"ok {path}: {spec.agent_id}@{spec.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

