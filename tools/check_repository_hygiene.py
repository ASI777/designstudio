#!/usr/bin/env python3
"""Fail when secrets, build output, or mutable datasets enter source control."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DATA_ROOT = PurePosixPath("testdata")
FORBIDDEN_DIRS = {
    "artifacts",
    "batch_out",
    "build",
    "build_linux",
    "component_layouts",
    "datasets",
    "datasheets",
    "dist",
    "dist_linux",
    "generated",
    "Json Library",
    "out",
    "output",
    "var",
}
SECRET_NAME = re.compile(
    r"(^|/)(\.env(?:\..+)?|id_rsa|id_ed25519|credentials\.json|[^/]+\.pem)$",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    rb"(?i)(api[_-]?key|client[_-]?secret|auth[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-/]{20,}"
)


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode:
        print(result.stderr.decode(errors="replace"), file=sys.stderr)
        return 2

    tracked = [p.decode() for p in result.stdout.split(b"\0") if p]
    errors: list[str] = []
    for value in tracked:
        path = PurePosixPath(value)
        disk_path = ROOT / value
        # A migration worktree can contain unstaged deletions; the committed
        # clean-checkout gate sees only paths that still exist.
        if not disk_path.exists():
            continue
        if SECRET_NAME.search(value) and path.name != ".env.example":
            errors.append(f"tracked secret-shaped file: {value}")
        if not path.is_relative_to(ALLOWED_DATA_ROOT):
            if any(part in FORBIDDEN_DIRS for part in path.parts[:-1]):
                errors.append(f"generated/data directory tracked in source: {value}")

        try:
            payload = disk_path.read_bytes()
        except (OSError, ValueError):
            continue
        if b"\0" not in payload[:8192] and SECRET_VALUE.search(payload):
            errors.append(f"possible embedded credential: {value}")

    for ignored_path in (".env", "var/generated/probe", "datasets/probe"):
        ignored = git("check-ignore", "--quiet", "--no-index", ignored_path)
        if ignored.returncode != 0:
            errors.append(f"required ignore rule missing: {ignored_path}")

    if errors:
        print("Repository hygiene failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Repository hygiene passed ({len(tracked)} tracked paths checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
