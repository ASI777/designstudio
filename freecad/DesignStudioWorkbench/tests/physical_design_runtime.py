#!/usr/bin/env python3
"""Exercise bounded preview and baseline-preserving commit in FreeCAD."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import FreeCAD as App
import Part


ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.physical_design import (  # noqa: E402
    capture_local_redesign,
    commit_local_redesign,
    ensure_semantic_id,
    prepare_local_redesign,
    preview_local_redesign,
    semantic_assembly_manifest,
    shape_digest,
)


def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "product.dsworkspace"
        mechanical = root / "mechanical" / "product.FCStd"
        mechanical.parent.mkdir(parents=True)
        (root / "contracts").mkdir()
        document = App.newDocument("PhysicalDesignRuntime")
        target = document.addObject("PartDesign::Feature", "GripBaseline")
        target.Label = "Grip baseline"
        target.Shape = Part.makeBox(10, 20, 30)
        target_id = ensure_semantic_id(target, "ergonomic_grip")
        protected = document.addObject("PartDesign::Feature", "TriggerProtected")
        protected.Label = "Protected trigger"
        protected.Shape = Part.makeBox(5, 5, 5, App.Vector(40, 0, 0))
        protected_id = ensure_semantic_id(protected, "control")
        document.recompute()
        document.saveAs(str(mechanical))
        protected_digest = shape_digest(protected.Shape)
        selection = [SimpleNamespace(Object=target, SubElementNames=[])]
        captured = capture_local_redesign(
            document, root, "Round the grip while preserving its envelope.", 0.0,
            [protected_id], selection_ex=selection)
        redesign_path = Path(captured["path"])
        program = {
            "schema": "design-studio.mechanical-cad-program/1",
            "program_id": "runtime-redesign-candidate", "units": "mm", "author": "runtime",
            "commands": [{"id": "candidate", "op": "part.box",
                          "params": {"length_mm": 10, "width_mm": 20, "height_mm": 30,
                                     "origin_mm": [0, 0, 0]},
                          "provenance": ["runtime#measured-candidate"]}],
            "checks": [{"kind": "valid_shape", "target": "candidate"},
                       {"kind": "volume", "target": "candidate"}],
        }
        ready = prepare_local_redesign(root, redesign_path, program, "candidate")
        ready_path = Path(ready["path"])
        assert ready_path != redesign_path
        assert json.loads(redesign_path.read_text(encoding="utf-8"))["status"] == "captured"
        preview = preview_local_redesign(document, root, ready_path)
        receipt = preview["preview"]
        assert receipt["baseline_retained"] is True
        assert receipt["baseline_visible"] is True
        assert shape_digest(protected.Shape) == protected_digest
        assert len(receipt["drawings"]["artifacts"]) == 12
        for artifact in receipt["drawings"]["artifacts"]:
            assert Path(artifact["path"]).is_file()
        commit = commit_local_redesign(document, root, preview["receipt_path"])["commit"]
        candidate = document.getObject(commit["candidate_object"])
        retained = document.getObject(commit["retained_baseline_object"])
        assert candidate.DesignStudioSemanticId == target_id
        assert retained is not None
        assert retained.DesignStudioSemanticId == commit["retained_baseline_semantic_id"]
        assert commit["baseline_deleted"] is False
        assert shape_digest(protected.Shape) == protected_digest
        manifest = semantic_assembly_manifest(document)
        ids = [item["semantic_id"] for item in manifest["objects"]]
        assert len(ids) == len(set(ids))
        assert target_id in ids
        assert commit["retained_baseline_semantic_id"] in ids
        App.closeDocument(document.Name)
    print("PHYSICAL_DESIGN_RUNTIME_OK")


if __name__ == "__main__":
    run()
