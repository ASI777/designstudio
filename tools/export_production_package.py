#!/usr/bin/env python3
"""Generate a verified manufacturer-specific DesignStudio production package."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.production import ProductionExportError, export_package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("profile")
    parser.add_argument("verification")
    parser.add_argument("output")
    parser.add_argument("--board-step-binary", required=True)
    args = parser.parse_args()
    try:
        manifest = export_package(args.project, args.profile, args.verification,
                                  args.output, args.board_step_binary)
        print(json.dumps({"status": "pass", "output": str(Path(args.output).resolve()),
                          "artifacts": len(manifest["artifacts"])}, sort_keys=True))
        return 0
    except ProductionExportError as exc:
        print(f"production export blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
