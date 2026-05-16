#!/usr/bin/env python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_platform.registry.loader import ManifestError, ManifestLoader


def main() -> int:
    loader = ManifestLoader()
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        paths = sorted(Path("agents").glob("*/manifest.yaml"))

    failed = False
    for path in paths:
        try:
            spec = loader.load_file(path)
        except ManifestError as exc:
            failed = True
            print(f"error {path}: {exc}", file=sys.stderr)
            continue
        print(f"ok {path}: {spec.agent_id}@{spec.version}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
