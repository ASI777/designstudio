#!/usr/bin/env python3
"""Exercise the AP242 sidecar publisher against real FreeCAD shapes."""
from __future__ import annotations

from pathlib import Path
import math
import sys
import tempfile

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.semantic_assembly import (  # noqa: E402
    build_semantic_assembly,
    write_semantic_assembly_sidecar,
)


def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        document = App.newDocument("SemanticAssemblyRuntime")
        try:
            first = document.addObject("Part::Feature", "U1")
            first.Label = "Silicon controller U1"
            first.Shape = Part.makeBox(4, 4, 2, App.Vector(-8, -2, -1))
            first.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio")
            first.DesignStudioSemanticId = "u1"
            first.addProperty("App::PropertyString", "DesignStudioParentId", "DesignStudio")
            first.DesignStudioParentId = "assembly-root"
            first.addProperty("App::PropertyString", "ReferenceDesignator", "DesignStudio")
            first.ReferenceDesignator = "U1"
            first.addProperty("App::PropertyString", "MaterialName", "DesignStudio")
            first.MaterialName = "silicon"
            first.addProperty("App::PropertyColor", "DesignStudioColor", "DesignStudio")
            first.DesignStudioColor = (0.85, 0.20, 0.05)

            second = document.addObject("Part::Feature", "J1")
            second.Label = "Service connector J1"
            second.Shape = Part.makeBox(5, 3, 1.5, App.Vector(5.5, -1.5, -0.75))
            second.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio")
            second.DesignStudioSemanticId = "j1"
            second.addProperty("App::PropertyString", "DesignStudioParentId", "DesignStudio")
            second.DesignStudioParentId = "assembly-root"
            second.addProperty("App::PropertyString", "ReferenceDesignator", "DesignStudio")
            second.ReferenceDesignator = "J1"
            second.addProperty("App::PropertyString", "MaterialName", "DesignStudio")
            second.MaterialName = "FR4"
            second.addProperty("App::PropertyColor", "DesignStudioColor", "DesignStudio")
            second.DesignStudioColor = (0.05, 0.25, 0.85)
            document.recompute()

            step = root / "assembly-ap242.step"
            App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP").SetString(
                "Scheme", "AP242DIS")
            Part.export([first, second], str(step))
            payload = build_semantic_assembly(
                document, step, {"u1": (0, 12), "j1": (12, 12)}, "b" * 64,
                objects=[first, second])
            assert payload["source_format"] == "AP242"
            assert payload["components"][0]["reference_designator"] == "U1"
            assert all(math.isclose(actual, expected, abs_tol=1e-3)
                       for actual, expected in zip(payload["components"][1]["color_rgba"][:3],
                                                   [0.05, 0.25, 0.85]))
            receipt = write_semantic_assembly_sidecar(
                document, step, {"u1": (0, 12), "j1": (12, 12)}, "b" * 64,
                objects=[first, second])
            assert Path(receipt["path"]).is_file()
        finally:
            App.closeDocument(document.Name)
    print("SEMANTIC_ASSEMBLY_RUNTIME_OK")


if __name__ == "__main__":
    run()
