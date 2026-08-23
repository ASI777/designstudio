#!/usr/bin/env python3
"""FreeCAD-kernel runtime fixture for topology-aware local-redesign/2."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
import sys
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.local_redesign_v2 import (  # noqa: E402
    capture_local_redesign_v2,
    commit_local_redesign_v2,
    discard_local_redesign_v2,
    prepare_local_redesign_v2,
    preview_local_redesign_v2,
    rollback_local_redesign_v2,
    shape_digest,
)
from DesignStudio.vector_native_cad import control_datum_digest  # noqa: E402


class Selection:
    def __init__(self, obj, names):
        self.Object = obj
        self.SubElementNames = list(names)


def patch_curve():
    # Degree-one, clamped B-spline with an explicitly repeated closing pole;
    # this yields the exact rectangular boundary used by the box top face.
    return {
        "degree": 1,
        "knots": [0.0, 1.0, 2.0, 3.0, 4.0],
        "multiplicities": [2, 1, 1, 1, 2],
        "weights": [1.0] * 5,
        "control_points": [[0.0, 0.0, 10.0], [20.0, 0.0, 10.0],
                           [20.0, 12.0, 10.0], [0.0, 12.0, 10.0],
                           [0.0, 0.0, 10.0]],
        "closed": True, "periodic": False,
        "classification": "original-designed",
        "provenance": ["runtime:local-redesign-v2"],
    }


def patch_program(output_id="patch"):
    curve = patch_curve()
    return {
        "schema": "design-studio.mechanical-cad-program/2",
        "program_id": "local-patch-program-" + output_id,
        "units": "mm", "author": "local-redesign-v2-runtime",
        "envelope": {"min_mm": [-1.0, -1.0, 9.0], "max_mm": [21.0, 13.0, 11.0]},
        "control_datums": [{"id": "origin", "point_mm": [0.0, 0.0, 10.0],
                             "locked": True,
                             "digest": control_datum_digest([0.0, 0.0, 10.0])}],
        "commands": [
            {"id": "boundary", "op": "curve.bspline3d", "params": {"curve": curve},
             "provenance": ["runtime:patch-boundary"]},
            {"id": "patch", "op": "feature.surface_fill",
             "params": {"boundaries": ["boundary"],
                         "continuity": {"required": "G0", "max_normal_angle_deg": 1.0}},
             "provenance": ["runtime:patch-face"]},
        ],
        "checks": [{"kind": "valid_shape", "target": "patch"}],
    }


def main():
    with tempfile.TemporaryDirectory(prefix="designstudio-local-v2-") as temp:
        root = Path(temp)
        mechanical_dir = root / "mechanical"
        mechanical_dir.mkdir(parents=True)
        doc = App.newDocument("LocalRedesignV2Runtime")
        target = doc.addObject("PartDesign::Feature", "TargetBody")
        target.Label = "Target body"
        target.Shape = Part.makeBox(20.0, 12.0, 10.0)
        target.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        target.DesignStudioSemanticId = "target-body"
        target.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
        target.DesignStudioRole = "enclosure"
        protected = doc.addObject("PartDesign::Feature", "ProtectedComponent")
        protected.Shape = Part.makeBox(2.0, 2.0, 2.0, App.Vector(30.0, 0.0, 0.0))
        protected.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        protected.DesignStudioSemanticId = "protected-component"
        protected.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
        protected.DesignStudioRole = "electronics"
        mechanical_path = mechanical_dir / "local-v2.FCStd"
        doc.recompute()
        doc.saveAs(str(mechanical_path))
        top_faces = []
        for index, face in enumerate(target.Shape.Faces, 1):
            center = face.CenterOfMass
            if abs(float(center.z) - 10.0) < 1.0e-6:
                top_faces.append(f"Face{index}")
        assert len(top_faces) == 1, top_faces
        selection = Selection(target, top_faces)
        captured = capture_local_redesign_v2(
            doc, root, "Replace the selected top surface while preserving its boundary.",
            permitted_expansion_mm=0.1,
            protected_objects=[{"semantic_id": "protected-component", "clearance_mm": 1.0}],
            continuity_required="G0", selection_ex=[selection],
            redesign_id="runtime-local-patch")
        captured_contract = json.loads(Path(captured["path"]).read_text(encoding="utf-8"))
        assert captured_contract["target"]["selected_faces"] == top_faces
        assert captured_contract["target"]["boundary_edges"]
        assert captured_contract["target"]["local_frame"]["z_axis"] == [0.0, 0.0, 1.0]
        prepared = prepare_local_redesign_v2(
            root, captured["path"], patch_program(), "patch", ["Face1"])
        preview = preview_local_redesign_v2(doc, root, prepared["path"])
        preview_data = preview["preview"]
        assert preview_data["schema"] == "design-studio.local-redesign-preview/2"
        assert preview_data["boundary_check"]["max_deviation_mm"] <= 0.05 + 1.0e-9
        assert preview_data["continuity_check"]["passed"]
        assert preview_data["quality_check"]["watertight"]
        assert preview_data["quality_check"]["self_intersection_check"] == "occt_shape_check"
        assert preview_data["baseline_retained"]
        assert preview_data["result_kind"] in {"solid", "shell"}
        preview_object = doc.getObject(preview_data["preview_object"])
        assert preview_object is not None and preview_object.Shape.isValid()
        baseline_digest = shape_digest(target.Shape)
        discarded = discard_local_redesign_v2(doc, root, preview["receipt_path"])
        assert discarded["discard"]["baseline_retained"] is True
        assert discarded["discard"]["target_shape_sha256"] == baseline_digest
        assert doc.getObject(preview_data["preview_object"]) is None
        assert shape_digest(target.Shape) == baseline_digest
        preview = preview_local_redesign_v2(doc, root, prepared["path"])
        preview_data = preview["preview"]
        preview_object = doc.getObject(preview_data["preview_object"])
        assert preview_object is not None and preview_object.Shape.isValid()
        commit = commit_local_redesign_v2(doc, root, preview["receipt_path"])
        commit_data = commit["commit"]
        assert commit_data["stable_semantic_id"] == "target-body"
        assert commit_data["baseline_deleted"] is False
        assert target.ViewObject is None or target.ViewObject.Visibility is False
        assert preview_object.ViewObject is None or preview_object.ViewObject.Visibility is True
        assert doc.getObject(commit_data["retained_baseline_object"]) is not None
        assert shape_digest(target.Shape) == baseline_digest
        try:
            discard_local_redesign_v2(doc, root, preview["receipt_path"])
        except Exception as exc:
            assert "rollback" in str(exc).lower()
        else:
            raise AssertionError("committed preview was accepted by Reject")
        rollback = rollback_local_redesign_v2(doc, root, commit["receipt_path"])
        assert rollback["rollback"]["stable_semantic_id"] == "target-body"
        assert target.ViewObject is None or target.ViewObject.Visibility is True
        assert preview_object.ViewObject is None or preview_object.ViewObject.Visibility is False
        App.closeDocument(doc.Name)

        # Stale target and excessive boundary movement must fail before any
        # candidate objects are committed.
        stale_doc = App.newDocument("LocalRedesignV2Stale")
        stale = stale_doc.addObject("PartDesign::Feature", "StaleTarget")
        stale.Shape = Part.makeBox(20.0, 12.0, 10.0)
        stale.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        stale.DesignStudioSemanticId = "target-body"
        stale_before = {obj.Name for obj in stale_doc.Objects}
        try:
            preview_local_redesign_v2(stale_doc, root, prepared["path"])
        except Exception:
            pass
        else:
            raise AssertionError("stale document was accepted")
        assert {obj.Name for obj in stale_doc.Objects} == stale_before
        App.closeDocument(stale_doc.Name)
        print("LOCAL_REDESIGN_V2_RUNTIME_OK")


if __name__ == "__main__":
    main()
