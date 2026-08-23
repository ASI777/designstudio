#!/usr/bin/env python3
"""Exercise the typed mechanical CAD program in the real FreeCAD kernel."""
from __future__ import annotations

from pathlib import Path
import sys

import FreeCAD as App

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.mechanical_cad import execute_program  # noqa: E402


def run() -> None:
    document = App.newDocument("MechanicalCadRuntime")
    program = {
        "schema": "design-studio.mechanical-cad-program/1",
        "program_id": "runtime-feature-fixture",
        "units": "mm",
        "author": "runtime-test",
        "commands": [
            {"id": "base", "op": "part.box",
             "params": {"length_mm": 80, "width_mm": 40, "height_mm": 10},
             "provenance": ["runtime#base"]},
            {"id": "hole", "op": "feature.hole",
             "params": {"base": "base", "radius_mm": 2,
                        "center_mm": [10, 10, 0], "depth_mm": 10},
             "provenance": ["runtime#hole"]},
            {"id": "guide", "op": "sketch.create",
             "params": {"plane": "XY"},
             "provenance": ["runtime#guide"]},
            {"id": "guide-line", "op": "sketch.line",
             "params": {"sketch": "guide", "start_mm": [0, 0, 0],
                        "end_mm": [10, 0, 0]},
             "provenance": ["runtime#guide-line"]},
            {"id": "guide-horizontal", "op": "sketch.constraint.add",
             "params": {"sketch": "guide", "kind": "horizontal",
                        "references": [0]},
             "provenance": ["runtime#guide-horizontal"]},
            {"id": "profile", "op": "sketch.rectangle",
             "params": {"width_mm": 6, "height_mm": 10,
                        "origin_mm": [10, 0, 0]},
             "provenance": ["runtime#profile"]},
            {"id": "pad", "op": "feature.extrude",
             "params": {"profile": "profile", "vector_mm": [0, 0, 4]},
             "provenance": ["runtime#pad"]},
            {"id": "turned-profile", "op": "sketch.rectangle",
             "params": {"width_mm": 5, "height_mm": 10,
                        "origin_mm": [10, 0, 0]},
             "provenance": ["runtime#revolve-profile"]},
            {"id": "turned", "op": "feature.revolve",
             "params": {"profile": "turned-profile",
                        "axis_origin_mm": [0, 0, 0],
                        "axis_direction": [0, 1, 0], "angle_deg": 360},
             "provenance": ["runtime#revolve"]},
            {"id": "pattern", "op": "pattern.linear",
             "params": {"source": "pad", "count": 3,
                        "spacing_mm": 12, "direction": [1, 0, 0]},
             "provenance": ["runtime#pattern"]},
        ],
        "checks": [
            {"kind": "valid_shape", "target": "base"},
            {"kind": "valid_shape", "target": "hole"},
            {"kind": "sketch_constraints", "target": "guide-horizontal"},
            {"kind": "sketch_constraints", "target": "profile"},
            {"kind": "volume", "target": "pad"},
            {"kind": "valid_shape", "target": "turned"},
            {"kind": "bbox", "target": "pattern"},
        ],
    }
    receipt = execute_program(document, program)
    assert receipt["solid_valid"] is True, receipt
    assert len(receipt["objects"]) == 8, receipt
    assert all(item["passed"] for item in receipt["checks"]), receipt
    assert document.getObject("DS_CAD_hole").Shape.Volume > 0
    assert document.getObject("DS_CAD_profile").TypeId == "Sketcher::SketchObject"
    assert document.getObject("DS_CAD_profile").FullyConstrained is True
    assert document.getObject("DS_CAD_guide").ConstraintCount == 1
    assert document.getObject("DS_CAD_turned").Shape.isValid()
    assert document.getObject("DS_CAD_pattern").Shape.BoundBox.XLength > 20
    App.closeDocument(document.Name)
    print("MECHANICAL_CAD_RUNTIME_OK")


if __name__ == "__main__":
    run()
