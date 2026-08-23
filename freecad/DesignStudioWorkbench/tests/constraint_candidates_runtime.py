#!/usr/bin/env python3
"""FreeCAD runtime fixture for constraint-driven physical candidates."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile

import FreeCAD as App
import Part

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.constraint_candidates import (  # noqa: E402
    create_physical_design_session_v2,
    generate_constraint_candidates,
    validate_physical_design_session_v2,
)
from DesignStudio.physical_design import shape_digest  # noqa: E402


def _session(root: Path) -> dict:
    image = root / "assets" / "controller-sketch.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"sketch-evidence-not-geometry")
    fcstd = root / "mechanical" / "controller.FCStd"
    return {
        "schema": "design-studio.physical-design-session/2", "session_id": "runtime-candidates",
        "revision": 1, "product_family": "controller", "workflow_mode": "co_design",
        "document": {"document_id": "Controller", "path": "mechanical/controller.FCStd", "sha256": ""},
        "intent": "Build hardware first, then compare a compact, balanced and comfortable enclosure.",
        "input_assets": [{"asset_id": "sketch", "path": "assets/controller-sketch.png", "sha256": "",
                           "media_type": "image/png", "input_kind": "hand_sketch", "role": "silhouette",
                           "geometry_authority": False, "measurement_status": "user_calibrated",
                           "calibration": {"method": "known_length", "pixels_per_mm": 4.0, "verified": True}}],
        "verified_dimensions": [{"dimension_id": "width", "source": "calibrated-sketch", "axis": "X",
                                 "value_mm": 150.0, "tolerance_mm": 0.1, "status": "verified"},
                                {"dimension_id": "height", "source": "calibrated-sketch", "axis": "Z",
                                 "value_mm": 64.0, "tolerance_mm": 0.1, "status": "verified"}],
        "occupied_volumes": [{"volume_id": "pcb", "semantic_id": "pcb-main", "kind": "pcb",
                              "min_mm": [35.0, 22.0, 5.0], "max_mm": [125.0, 84.0, 20.0],
                              "clearance_mm": 2.0, "locked": True, "source": "FreeCAD PCB B-Rep"}],
        "clearance_volumes": [{"volume_id": "service-zone", "semantic_id": "service-zone", "kind": "service",
                                "min_mm": [30.0, 18.0, 2.0], "max_mm": [130.0, 88.0, 24.0],
                                "clearance_mm": 1.0, "locked": True, "source": "service-access requirement"}],
        "desire_capture": [{"desire_id": "comfort", "statement": "Comfortable for small and large hands",
                             "priority": "required", "status": "user_confirmed"}],
        "interaction_objectives": [{"objective_id": "reach", "metric": "reach", "target": 80.0,
                                     "unit": "percentile", "status": "user_confirmed"},
                                    {"objective_id": "grip", "metric": "grip", "target": 3.0,
                                     "unit": "mm-palm-swell", "status": "unresolved"}],
        "material_constraints": [{"region_id": "shell", "candidate_materials": ["PLA", "PETG"],
                                  "requirements": ["0.4 mm nozzle FDM"], "evidence_status": "unverified"}],
        "manufacturing_constraints": {"processes": ["FDM"], "units": "mm", "minimum_wall_mm": 2.0,
                                       "minimum_feature_mm": 1.2, "status": "provisional"},
        "hard_constraints": {"locked_envelope": {"min_mm": [0.0, 0.0, 0.0], "max_mm": [160.0, 106.0, 66.0]},
                              "max_external_dimensions_mm": [160.0, 106.0, 66.0], "minimum_clearance_mm": 2.0,
                              "require_watertight_solid": True, "forbid_interference": True},
        "scoring_weights": {"occupied_volume": 1.0, "reach": 1.0, "clearance": 1.0, "wall": 1.0,
                             "continuity": 1.0, "manufacturability": 1.0, "mass": 1.0,
                             "material_use": 1.0, "user_priority": 2.0},
        "unresolved_evidence": ["hand-size sample not measured", "material density not selected"],
        "geometry_authority": {"authoritative_sources": ["freecad_brep", "typed_mechanical_cad_program", "verified_measurements"],
                               "raster_and_sketch_are_geometry_authority": False,
                               "unknown_dimensions_policy": "blocked_until_measured_or_explicitly_constrained"},
        "candidate_policy": {"exact_candidate_count": 3, "labels": ["compact", "balanced", "comfort"],
                              "ranking_policy": "hard_failures_removed_missing_analysis_incomplete"},
        "candidates": None,
        "provenance": {"created_by": "runtime", "created_utc": "2026-08-14T00:00:00+00:00",
                        "user_prompt": "Use this sketch and measured hardware to compare three shells."},
    }


def main():
    with tempfile.TemporaryDirectory(prefix="designstudio-candidates-") as directory:
        root = Path(directory)
        doc = App.newDocument("ConstraintCandidateRuntime")
        pcb = doc.addObject("PartDesign::Feature", "PcbMain")
        pcb.Label = "Locked PCB volume"
        pcb.Shape = Part.makeBox(90.0, 62.0, 15.0, App.Vector(35.0, 22.0, 5.0))
        pcb.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        pcb.DesignStudioSemanticId = "pcb-main"
        pcb.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
        pcb.DesignStudioRole = "locked_pcb"
        zone = doc.addObject("PartDesign::Feature", "ServiceZone")
        zone.Shape = Part.makeBox(100.0, 70.0, 24.0, App.Vector(30.0, 18.0, 0.0))
        zone.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
        zone.DesignStudioSemanticId = "service-zone"
        zone.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
        zone.DesignStudioRole = "locked_service"
        root.joinpath("mechanical").mkdir(parents=True, exist_ok=True)
        fcstd = root / "mechanical" / "controller.FCStd"
        doc.recompute()
        doc.saveAs(str(fcstd))
        session = _session(root)
        import hashlib
        session["document"]["sha256"] = hashlib.sha256(fcstd.read_bytes()).hexdigest()
        image = root / "assets" / "controller-sketch.png"
        session["input_assets"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
        session_path = create_physical_design_session_v2(root, session)["path"]
        before = {sid: shape_digest(obj.Shape) for sid, obj in (("pcb-main", pcb), ("service-zone", zone))}
        result = generate_constraint_candidates(doc, root, session_path)
        assert result["candidate_count"] == 3
        assert set(result["ranked"]) == {"compact", "balanced", "comfort"}
        saved = json.loads(Path(session_path).read_text(encoding="utf-8"))
        assert len(saved["candidates"]) == 3
        assert all(item["program"]["schema"] == "design-studio.mechanical-cad-program/2" for item in saved["candidates"])
        assert all(item["status"] == "incomplete" for item in saved["candidates"])
        assert all(item["analysis"]["wall"]["status"] == "incomplete" for item in saved["candidates"])
        assert len({item["program"]["commands"][1]["params"]["curve"]["control_points"][1][0]
                     for item in saved["candidates"]}) == 3
        assert before["pcb-main"] == shape_digest(pcb.Shape)
        assert before["service-zone"] == shape_digest(zone.Shape)
        doc.recompute()
        doc.saveAs(str(fcstd))
        App.closeDocument(doc.Name)
        reloaded = App.openDocument(str(fcstd))
        reloaded.recompute()
        assert all(reloaded.getObject(item) is not None for item in ("DS_V2_candidate_compact", "DS_V2_candidate_balanced", "DS_V2_candidate_comfort"))
        assert all(reloaded.getObject(item).Shape.isValid() for item in ("DS_V2_compact_body", "DS_V2_balanced_body", "DS_V2_comfort_body"))
        assert len(reloaded.getObject("DS_V2_candidate_compact").CommandIds) == 4
        App.closeDocument(reloaded.Name)
        # A volume outside the locked envelope is a hard failure and must not
        # leave candidate objects or alter the source session receipt.
        doc = App.openDocument(str(fcstd))
        bad = json.loads(Path(session_path).read_text(encoding="utf-8"))
        bad["candidates"] = None
        bad["occupied_volumes"][0]["min_mm"][0] = -20.0
        bad["document"]["sha256"] = hashlib.sha256(fcstd.read_bytes()).hexdigest()
        Path(session_path).write_text(json.dumps(bad, indent=2), encoding="utf-8")
        try:
            before_bad = {obj.Name for obj in doc.Objects}
            bad_result = generate_constraint_candidates(doc, root, session_path)
            assert set(bad_result["rejected"]) == {"compact", "balanced", "comfort"}
            assert {obj.Name for obj in doc.Objects} == before_bad
        finally:
            App.closeDocument(doc.Name)
        print("CONSTRAINT_CANDIDATES_RUNTIME_OK")


if __name__ == "__main__":
    main()
