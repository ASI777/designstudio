#!/usr/bin/env python3
"""Apply an approved contracted-fabricator profile as a new project revision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.production import ProductionExportError, apply_fabrication_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("profile")
    parser.add_argument("output")
    args = parser.parse_args()
    try:
        print(json.dumps(apply_fabrication_profile(args.project, args.profile, args.output),
                         indent=2, sort_keys=True))
        return 0
    except ProductionExportError as exc:
        print(f"production profile not applied: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
