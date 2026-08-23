#!/usr/bin/env python3
"""Create or verify an exhaustive SHA-256 inventory without following symlinks."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def inventory(root: Path) -> list[dict]:
    return [
        {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
        and path.name != "artifact-inventory.json"
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--write", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if bool(args.write) == bool(args.verify):
        parser.error("choose exactly one of --write or --verify")
    actual = inventory(args.directory.resolve())
    if args.write:
        value = {"schema": "multi-gpu-campaign-artifact-inventory/1",
                 "files": actual}
        args.write.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    expected = json.loads(args.verify.read_text(encoding="utf-8"))
    expected_files = expected.get("files")
    if expected_files != actual:
        expected_map = {item["path"]: item for item in expected_files or []}
        actual_map = {item["path"]: item for item in actual}
        report = {
            "missing": sorted(set(expected_map) - set(actual_map)),
            "unexpected": sorted(set(actual_map) - set(expected_map)),
            "mismatched": sorted(
                path for path in set(expected_map) & set(actual_map)
                if expected_map[path] != actual_map[path]
            ),
        }
        print(json.dumps(report, indent=2))
        return 2
    print(f"ARTIFACTS_VERIFIED files={len(actual)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
