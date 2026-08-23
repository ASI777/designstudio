#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

WORKBENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKBENCH))
from DesignStudio.camera_benchmark import build_benchmark

parser = argparse.ArgumentParser()
parser.add_argument("--manifest", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args, _freecad_arguments = parser.parse_known_args()
result = build_benchmark(args.manifest.resolve(), args.output.resolve())
for report in result["candidates"]:
    import FreeCAD as App
    document = App.openDocument(report["fcstd"])
    display = next((item for item in document.Objects
                    if getattr(item, "SemanticId", "") ==
                    f"camera.{report['candidate']}.rear-display-window"), None)
    assert display is not None and not display.Shape.isNull()
    assert display.SemanticId == f"camera.{report['candidate']}.rear-display-window"
    assert display.Shape.BoundBox.YMax < 32.0
    App.closeDocument(document.Name)
    parts = Path(report["fcstd"]).parent / "review-parts-manifest.json"
    manifest = __import__("json").loads(parts.read_text(encoding="utf-8"))
    assert manifest["cad_authority"] is False
    assert any(item["semantic_id"] == f"camera.{report['candidate']}.rear-display-window"
               and "display" in item["material_intent"] for item in manifest["parts"])
print(result["status"])
