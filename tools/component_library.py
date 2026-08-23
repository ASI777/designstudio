#!/usr/bin/env python3
"""Inspect or bulk-import the DesignStudio SQLite component library."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_library import ComponentLibrary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", help="SQLite path (default: DesignStudio data root)")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest-datasheets")
    ingest.add_argument("directory")
    commands.add_parser("stats")
    args = parser.parse_args()
    with ComponentLibrary(args.database) as library:
        if args.command == "ingest-datasheets":
            result = library.ingest_datasheet_tree(args.directory)
        else:
            result = library.stats()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
