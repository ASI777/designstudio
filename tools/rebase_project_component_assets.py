#!/usr/bin/env python3
"""Rebase a controller package onto durable, project-local component assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_binding import binding_digest, validate_binding


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path,
                        help="directory containing project, manifest, components")
    args = parser.parse_args()
    output = args.output.resolve()
    project_path = output / "agent-workflow-controller.dsproj"
    manifest_path = output / "evidence-manifest.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    bindings: dict[str, dict] = {}
    for component_dir in sorted((output / "components").iterdir()):
        if not component_dir.is_dir():
            continue
        record_path = component_dir / "binding.json"
        step_path = component_dir / "package.step"
        if not record_path.is_file() or not step_path.is_file():
            raise SystemExit(f"incomplete component asset directory: {component_dir}")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["model_3d"]["asset_uri"] = "package.step"
        if isinstance(record.get("source_component"), dict):
            record["source_component"]["uri"] = "component.json"
        record["binding_digest"] = binding_digest(record)
        write_json(record_path, record)
        validate_binding(record, require_complete=True, verify_asset=True,
                         library_root=component_dir)
        bindings[str((record.get("component") or {}).get("mpn"))] = record

    for footprint in project.get("footprints", []):
        mpn = str(footprint.get("mpn", ""))
        record = bindings.get(mpn)
        if record is None:
            raise SystemExit(f"{footprint.get('ref')}: no durable binding for {mpn}")
        directory = safe(mpn)
        compact = footprint["bound_component"]
        compact["record_uri"] = f"components/{directory}/binding.json"
        compact["binding_digest"] = record["binding_digest"]
        compact["model_3d"]["asset_uri"] = (
            f"components/{directory}/package.step")
        compact["model_3d"]["sha256"] = record["model_3d"]["sha256"]
        compact["model_3d"]["model_to_footprint"] = (
            record["model_3d"]["model_to_footprint"])

    for item in manifest.get("components", []):
        mpn = str(item["mpn"])
        directory = output / "components" / safe(mpn)
        record = bindings.get(mpn)
        if record is None:
            raise SystemExit(f"manifest has no durable binding for {mpn}")
        component = directory / "component.json"
        step = directory / "package.step"
        footprint_files = sorted(directory.glob("*.kicad_mod"))
        if len(footprint_files) != 1:
            raise SystemExit(f"{mpn}: expected one symbol/footprint asset")
        item["binding"] = {
            "path": str((directory / "binding.json").resolve()),
            "digest": record["binding_digest"],
        }
        item["component"] = {
            "path": str(component.resolve()), "sha256": digest(component)}
        item["step"]["path"] = str(step.resolve())
        item["step"]["sha256"] = digest(step)
        item["symbol_and_footprint"] = {
            "path": str(footprint_files[0].resolve()),
            "sha256": digest(footprint_files[0]),
        }

    write_json(project_path, project)
    write_json(manifest_path, manifest)
    print(json.dumps({"ok": True, "bindings": len(bindings),
                      "footprints": len(project.get("footprints", [])),
                      "project": str(project_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
