#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import FreeCAD as App
import Import
import Mesh
import Part

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DesignStudio.constraint_candidates import generate_constraint_candidates
from DesignStudio.live_tools import execute as execute_live_tool
from DesignStudio.physical_validation import export_grip_test_plan, record_observation, create_candidate_decision
from DesignStudio.physical_design import shape_digest


def main():
    with tempfile.TemporaryDirectory(prefix="designstudio-physical-validation-") as directory:
        root = Path(directory)
        doc = App.newDocument("PhysicalValidationRuntime")
        pcb = doc.addObject("PartDesign::Feature", "PcbMain")
        pcb.Shape = Part.makeBox(90, 62, 15, App.Vector(35, 22, 5))
        pcb.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        pcb.DesignStudioSemanticId = "pcb-main"
        zone = doc.addObject("PartDesign::Feature", "ServiceZone")
        zone.Shape = Part.makeBox(100, 70, 24, App.Vector(30, 18, 0))
        zone.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        zone.DesignStudioSemanticId = "service-zone"
        (root / "mechanical").mkdir(parents=True)
        fcstd = root / "mechanical/controller.FCStd"
        doc.recompute(); doc.saveAs(str(fcstd))
        image = root / "assets/controller-sketch.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"synthetic-raster-evidence-not-geometry")
        evidence_digest = hashlib.sha256(image.read_bytes()).hexdigest()
        base_capture = {"product_family": "controller", "workflow_mode": "inside_out",
            "evidence_path": str(image), "evidence_sha256": evidence_digest,
            "evidence_kind": "hand_sketch", "known_length_mm": 160.0, "pixels_per_mm": 4.0,
            "occupied_volumes": [
                {"kind": "pcb", "semantic_id": "pcb-main", "min_mm": [35, 22, 5], "max_mm": [125, 84, 20]},
                {"kind": "service", "semantic_id": "service-zone", "min_mm": [30, 18, 2], "max_mm": [130, 88, 24]}],
            "desire": "Compare controller comfort for representative hand sizes", "material": "PETG",
            "process": "FDM", "created_utc": "2026-08-14T00:00:00+00:00"}
        session_paths = {}
        for name, kind, evidence, workflow in (
                ("runtime-photo", "object_photo", str(image), "outside_in"),
                ("runtime-sketch", "hand_sketch", str(image), "co_design"),
                ("runtime-hardware", "hand_sketch", "", "inside_out")):
            capture = dict(base_capture)
            capture.update({"session_id": name, "evidence_kind": kind,
                            "evidence_path": evidence, "workflow_mode": workflow})
            if not evidence:
                capture["evidence_sha256"] = ""
            response = json.loads(execute_live_tool("create_guided_physical_design_session", json.dumps({
                "mechanical_path": str(fcstd), "workspace_root": str(root), "capture": capture})))
            assert response["ok"], response
            session_paths[name] = response["data"]["path"]
        session_path = session_paths["runtime-sketch"]
        generation = generate_constraint_candidates(doc, root, session_path)
        assert not generation["rejected"], generation
        request = {"plan_id": "controller-grip-test", "observer": "synthetic-runtime",
            "created_utc": "2026-08-14T00:00:00+00:00", "material": "PETG",
            "synthetic_fixture": True,
            "print_settings": {"process": "FDM", "nozzle_mm": 0.4, "layer_height_mm": 0.2,
                "wall_mm": 2.0, "infill_percent": 15, "orientation": "split-plane-down"},
            "grip_regions": {label: {"method": "user_confirmed_section_planes",
                "semantic_ids": [f"candidate-body-runtime-sketch-{label}"],
                "section_boxes_mm": [{"min_mm": [0, 0, 0], "max_mm": [48, 106, 66]},
                                     {"min_mm": [112, 0, 0], "max_mm": [160, 106, 66]}],
                "confirmed_by_user": True} for label in ("compact", "balanced", "comfort")}}
        result = export_grip_test_plan(doc, root, session_path, request)
        assert result["candidate_count"] == 3 and result["ranking_status"] == "provisional"
        plan = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert len({item["grip_buck"]["stl"]["sha256"] for item in plan["candidates"]}) == 3
        for candidate in plan["candidates"]:
            buck = candidate["grip_buck"]
            buck_doc = App.openDocument(str(root / buck["fcstd"]["path"]))
            buck_doc.recompute(); buck_doc.recompute()
            feature = buck_doc.getObject("GripBuck")
            assert feature and feature.Shape.isValid() and shape_digest(feature.Shape) == buck["brep_digest"]
            App.closeDocument(buck_doc.Name)
            step_doc = App.newDocument("RoundTrip_" + candidate["label"])
            Import.insert(str(root / buck["step"]["path"]), step_doc.Name)
            step_doc.recompute()
            shapes = [obj.Shape for obj in step_doc.Objects if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()]
            assert shapes and all(shape.isValid() for shape in shapes)
            App.closeDocument(step_doc.Name)
            mesh = Mesh.Mesh(str(root / buck["stl"]["path"]))
            assert mesh.CountFacets > 0
        candidate = plan["candidates"][0]
        observation = {"observation_id": "synthetic-small-compact", "candidate_id": candidate["candidate_id"],
            "participant": {"anonymous_id": "synthetic-small", "adult_confirmed": True,
                "hand_size_group": "small", "hand_length_mm": 165, "hand_breadth_mm": 76},
            "test_order": 1, "ratings": {metric: 3 for metric in plan["metrics"]},
            "tested_at": "2026-08-14T01:00:00+00:00", "observer": "synthetic-runtime",
            "provenance": {"entered_by": "synthetic-runtime", "entered_utc": "2026-08-14T01:00:00+00:00",
                "source": "synthetic runtime fixture", "synthetic_fixture": True}}
        observation_result = record_observation(root, result["path"], observation)
        try:
            create_candidate_decision(root, result["path"], [observation_result["path"]],
                decision_id="must-not-exist", observer="synthetic-runtime",
                created_utc="2026-08-14T02:00:00+00:00")
            raise AssertionError("synthetic incomplete observations created a winner")
        except Exception:
            pass
        assert not (Path(result["path"]).parent / "physical-candidate-decision.json").exists()
        retained = os.environ.get("DESIGNSTUDIO_PHYSICAL_VALIDATION_OUTPUT", "").strip()
        if retained:
            retained_path = Path(retained).expanduser().resolve()
            if retained_path.exists():
                raise AssertionError("retained evidence destination already exists")
            shutil.copytree(root, retained_path)
            print("PHYSICAL_VALIDATION_EVIDENCE", retained_path)
        App.closeDocument(doc.Name)
        print("PHYSICAL_VALIDATION_RUNTIME_OK", result["plan_digest"])


if __name__ == "__main__":
    main()
