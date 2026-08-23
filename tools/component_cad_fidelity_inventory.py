#!/usr/bin/env python3
"""Build a digest-bound inventory of component CAD fidelity for demo review."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = json.loads(args.project.read_text())
    occurrences = Counter(
        str(footprint.get("mpn") or footprint.get("value") or "")
        for footprint in project.get("footprints", [])
    )
    records: list[dict[str, Any]] = []
    for component_path in sorted(args.components.glob("*/component.json")):
        component = json.loads(component_path.read_text())
        identity = component.get("component") or {}
        mpn = str(identity.get("mpn") or component_path.parent.name)
        construction = (component.get("package_3d") or {}).get("construction") or {}
        primitives = construction.get("primitives") or []
        roles = Counter(str(item.get("role") or "detail") for item in primitives)
        shapes = Counter(str(item.get("shape") or "unknown") for item in primitives)
        step = component_path.parent / "package.step"
        program = component_path.parent / "cad-program.json"
        verification = (component.get("extraction") or {}).get("verification") or {}
        assumptions = list(component.get("assumptions") or []) + list(
            construction.get("assumptions") or []
        )
        visually_distinct = bool(
            roles.keys() & {"actuator", "shield", "housing", "lens", "connector"}
        )
        distinctive_categories = {
            "battery_connector", "connector", "joystick", "keyswitch",
            "mcu_module", "sensor_connector",
        }
        declared_fidelity = str((component.get("package_3d") or {}).get("fidelity") or "")
        fidelity = declared_fidelity or (
            "mechanical_dimensions_verified"
            if verification.get("geometry_confidence") == "verified"
            and not assumptions and step.is_file() and program.is_file()
            else "envelope_only"
        )
        if identity.get("category") in distinctive_categories and not visually_distinct:
            fidelity = "envelope_only"
        records.append({
            "mpn": mpn,
            "manufacturer": identity.get("manufacturer"),
            "category": identity.get("category"),
            "board_occurrences": occurrences.get(mpn, 0),
            "fidelity": fidelity,
            "primitive_count": len(primitives),
            "primitive_roles": dict(sorted(roles.items())),
            "primitive_shapes": dict(sorted(shapes.items())),
            "visually_distinct": visually_distinct,
            "assumption_count": len(assumptions),
            "datasheet_sha256": (component.get("evidence") or {}).get("sha256"),
            "cad_program_sha256": sha256(program),
            "step_sha256": sha256(step),
            "step_bytes": step.stat().st_size if step.is_file() else 0,
        })
    material = {
        "schema": "design-studio.component-cad-fidelity-inventory/1",
        "project_sha256": sha256(args.project),
        "component_count": len(records),
        "components": records,
        "review_constraints": {
            "allowed_fidelity": [
                "supplier_step_verified",
                "mechanical_dimensions_verified",
                "envelope_verified_visual_detail_provisional",
                "envelope_only",
            ],
            "llm_geometry_authority": False,
            "maximum_hero_components": 5,
        },
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    material["inventory_sha256"] = hashlib.sha256(encoded).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
