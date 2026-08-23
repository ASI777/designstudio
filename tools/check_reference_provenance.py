#!/usr/bin/env python3
"""Keep pinned reference implementation material out of DesignStudio code."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "references" / "kicad-10.0.4.reference.json"
DESIGN_ROOTS = ("core", "app", "modules", "freecad", "services", "swarm", "tools")
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".py", ".cs", ".rs"}
RESOURCE_SUFFIXES = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp", ".xpm"}
FORBIDDEN_HEADER = re.compile(
    rb"(?i)(copyright[^\n]{0,120}(kicad|wayne\s+stambaugh)|spdx-license-identifier:\s*gpl-[23])"
)
GPL_TEXT = re.compile(rb"GNU GENERAL PUBLIC LICENSE", re.IGNORECASE)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|0x[0-9A-Fa-f]+|\d+(?:\.\d+)?")
SHINGLE_SIZE = 12


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def design_files() -> list[Path]:
    files: list[Path] = []
    for root_name in DESIGN_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        files.extend(
            path for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
            and path != Path(__file__).resolve()
        )
    return files


def normalized_shingles(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    tokens = [token.lower() for token in TOKEN.findall(text)]
    return {
        "\x1f".join(tokens[index:index + SHINGLE_SIZE])
        for index in range(max(0, len(tokens) - SHINGLE_SIZE + 1))
    }


def verify_manifest(manifest: dict, reference_root: Path, errors: list[str]) -> None:
    if manifest.get("version") != "10.0.4" or manifest.get("tag") != "10.0.4":
        errors.append("reference manifest is not pinned to KiCad 10.0.4")
    commit = manifest.get("commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("reference manifest commit is not a full 40-character hash")
    for name, artifact in manifest.get("artifacts", {}).items():
        path = reference_root / artifact.get("path", "")
        expected = artifact.get("sha256", "")
        if not path.exists():
            continue
        if expected == "pending-download":
            errors.append(f"{name}: local artifact exists but manifest digest is pending")
        elif not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"{name}: invalid SHA-256 in manifest")
        elif digest(path) != expected:
            errors.append(f"{name}: local artifact SHA-256 does not match manifest")
        installed_path = artifact.get("installed_path")
        installed_expected = artifact.get("installed_sha256")
        if installed_path:
            installed = reference_root / installed_path
            if not installed.is_file():
                errors.append(f"{name}: declared installed artifact is missing")
            elif not re.fullmatch(r"[0-9a-f]{64}", installed_expected or ""):
                errors.append(f"{name}: invalid installed SHA-256 in manifest")
            elif digest(installed) != installed_expected:
                errors.append(f"{name}: installed artifact SHA-256 does not match manifest")


def scan_headers(files: list[Path], errors: list[str]) -> None:
    for path in files:
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if FORBIDDEN_HEADER.search(payload):
            errors.append(f"upstream/GPL copyright header in DesignStudio source: {relative}")
        if GPL_TEXT.search(payload[:64 * 1024]):
            errors.append(f"embedded GPL license text in DesignStudio source: {relative}")


def reference_files(manifest: dict, reference_root: Path) -> list[Path]:
    source_root = reference_root / "source"
    result: list[Path] = []
    for observation in manifest.get("observations", []):
        for relative in observation.get("reference_files", []):
            path = source_root / relative
            if path.is_file():
                result.append(path)
    return result


def scan_exact_copies(design: list[Path], reference: list[Path], errors: list[str]) -> None:
    by_digest: dict[str, list[Path]] = defaultdict(list)
    for path in reference:
        by_digest[digest(path)].append(path)
    if not by_digest:
        return
    for path in design:
        if path.suffix.lower() not in SOURCE_SUFFIXES | RESOURCE_SUFFIXES:
            continue
        match = by_digest.get(digest(path))
        if match:
            errors.append(
                f"exact reference copy: {path.relative_to(ROOT)} == {match[0].name}"
            )


def scan_similarity(design: list[Path], reference: list[Path], errors: list[str]) -> None:
    owners: dict[str, set[Path]] = defaultdict(set)
    reference_sets: dict[Path, set[str]] = {}
    for path in reference:
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        shingles = normalized_shingles(path)
        reference_sets[path] = shingles
        for shingle in shingles:
            owners[shingle].add(path)
    for path in design:
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        shingles = normalized_shingles(path)
        if len(shingles) < 40:
            continue
        candidates: Counter[Path] = Counter()
        for shingle in shingles:
            candidates.update(owners.get(shingle, ()))
        for upstream, shared in candidates.most_common(1):
            denominator = min(len(shingles), len(reference_sets[upstream]))
            if shared >= 40 and denominator and shared / denominator >= 0.45:
                errors.append(
                    "unexplained source similarity "
                    f"({shared}/{denominator} normalized shingles): "
                    f"{path.relative_to(ROOT)} vs {upstream.name}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reference_root = args.reference_root or ROOT / manifest["reference_only_path"]
    design = design_files()
    upstream = reference_files(manifest, reference_root)
    errors: list[str] = []
    verify_manifest(manifest, reference_root, errors)
    scan_headers(design, errors)
    scan_exact_copies(design, upstream, errors)
    scan_similarity(design, upstream, errors)
    if errors:
        print("Reference provenance gate failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"  - {error}", file=sys.stderr)
        return 1
    note = f"; compared {len(upstream)} declared upstream files" if upstream else "; source similarity not available"
    print(f"Reference provenance gate passed ({len(design)} DesignStudio files{note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
