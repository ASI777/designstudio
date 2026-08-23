#!/usr/bin/env python3
"""Create a deterministic digest manifest for a DesignStudio demo checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("--limitation", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    artifacts = []
    for supplied in sorted(args.artifact, key=str):
        path = supplied if supplied.is_absolute() else root / supplied
        if not path.is_file():
            raise FileNotFoundError(path)
        artifacts.append({
            "path": str(path.resolve().relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    checks = {}
    for item in args.check:
        name, separator, status = item.partition("=")
        if not separator or not name or status not in {"passed", "failed", "blocked"}:
            raise ValueError(f"invalid --check {item!r}")
        checks[name] = status
    material = {
        "schema": "design-studio.demo-evidence-checkpoint/1",
        "artifacts": artifacts,
        "checks": dict(sorted(checks.items())),
        "limitations": sorted(set(args.limitation)),
        "publication_authorized": False,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    material["checkpoint_sha256"] = hashlib.sha256(encoded).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
