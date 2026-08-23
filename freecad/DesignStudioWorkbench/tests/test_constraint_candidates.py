from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

WORKBENCH = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(WORKBENCH))

from DesignStudio import constraint_candidates as module  # noqa: E402


def _session(root: Path, *, product_family="controller"):
    fcstd = root / "mechanical" / "product.FCStd"
    fcstd.parent.mkdir(parents=True, exist_ok=True)
    fcstd.write_bytes(b"FCStd-v2-constraint-fixture")
    image = root / "assets" / "hand-sketch.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"not-authoritative-reference")
    return {
        "schema": module.SESSION_SCHEMA,
        "session_id": "constraint-fixture",
        "revision": 1,
        "product_family": product_family,
        "workflow_mode": "inside_out",
        "document": {"document_id": "Product", "path": "mechanical/product.FCStd",
                     "sha256": hashlib.sha256(fcstd.read_bytes()).hexdigest()},
        "intent": "Place verified hardware first and compare ergonomic enclosure alternatives.",
        "input_assets": [{"asset_id": "sketch", "path": "assets/hand-sketch.png",
                           "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                           "media_type": "image/png", "input_kind": "hand_sketch",
                           "role": "inspiration", "geometry_authority": False,
                           "measurement_status": "user_calibrated",
                           "calibration": {"method": "known_length", "pixels_per_mm": 4.0, "verified": True}}],
        "verified_dimensions": [{"dimension_id": "body-width", "source": "calibrated-sketch",
                                 "axis": "X", "value_mm": 150.0, "tolerance_mm": 0.2, "status": "user_calibrated"}],
        "occupied_volumes": [{"volume_id": "pcb", "semantic_id": "pcb-main", "kind": "pcb",
                              "min_mm": [40.0, 25.0, 5.0], "max_mm": [120.0, 80.0, 20.0],
                              "clearance_mm": 2.0, "locked": True, "source": "verified_component"}],
        "clearance_volumes": [],
        "desire_capture": [{"desire_id": "comfort", "statement": "Comfortable for a broad range of hands",
                             "priority": "required", "status": "user_confirmed"}],
        "interaction_objectives": [{"objective_id": "reach", "metric": "reach", "target": 80.0,
                                     "unit": "percentile", "status": "user_confirmed"}],
        "material_constraints": [{"region_id": "shell", "candidate_materials": ["PLA", "PETG"],
                                  "requirements": ["FDM prototype"], "evidence_status": "unverified"}],
        "manufacturing_constraints": {"processes": ["FDM"], "units": "mm", "minimum_wall_mm": 2.0,
                                       "minimum_feature_mm": 1.2, "status": "provisional"},
        "hard_constraints": {"locked_envelope": {"min_mm": [0.0, 0.0, 0.0], "max_mm": [160.0, 106.0, 66.0]},
                              "max_external_dimensions_mm": [160.0, 106.0, 66.0], "minimum_clearance_mm": 2.0,
                              "require_watertight_solid": True, "forbid_interference": True},
        "scoring_weights": {"occupied_volume": 1.0, "reach": 1.0, "clearance": 1.0, "wall": 1.0,
                             "continuity": 1.0, "manufacturability": 1.0, "mass": 1.0,
                             "material_use": 1.0, "user_priority": 2.0},
        "unresolved_evidence": ["exact hand anthropometry not supplied"],
        "geometry_authority": {"authoritative_sources": ["freecad_brep", "typed_mechanical_cad_program", "verified_measurements"],
                               "raster_and_sketch_are_geometry_authority": False,
                               "unknown_dimensions_policy": "blocked_until_measured_or_explicitly_constrained"},
        "candidate_policy": {"exact_candidate_count": 3, "labels": ["compact", "balanced", "comfort"],
                              "ranking_policy": "hard_failures_removed_missing_analysis_incomplete"},
        "candidates": None,
        "provenance": {"created_by": "contract-test", "created_utc": "2026-08-14T00:00:00+00:00",
                        "user_prompt": "Build three alternatives from this sketch and hardware volume."},
    }


def test_schema_and_host_contract():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        value = _session(root)
        validated = module.validate_physical_design_session_v2(value, root)
        assert validated["candidate_policy"]["labels"] == ["compact", "balanced", "comfort"]
        result = module.create_physical_design_session_v2(root, value)
        assert Path(result["path"]).is_file()
        persisted = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert persisted["ranking_status"] == "provisional"
        invalid = deepcopy(value)
        invalid["ranking_status"] = "physical_validated"
        try:
            module.validate_physical_design_session_v2(invalid, root)
            raise AssertionError("unbacked physical_validated ranking was accepted")
        except module.ConstraintCandidateError:
            pass
        invalid = deepcopy(value)
        invalid["input_assets"][0]["geometry_authority"] = True
        try:
            module.validate_physical_design_session_v2(invalid, root)
            raise AssertionError("raster authority was accepted")
        except module.ConstraintCandidateError:
            pass
        invalid = deepcopy(value)
        invalid["candidate_policy"]["labels"] = ["compact", "balanced"]
        try:
            module.validate_physical_design_session_v2(invalid, root)
            raise AssertionError("two candidates were accepted")
        except module.ConstraintCandidateError:
            pass


def test_incomplete_metrics_do_not_influence_provisional_score():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        value = _session(root)
        analyses = {
            "occupied_volume": {"status": "pass"},
            "clearance": {"status": "pass"},
            "continuity": {"status": "pass"},
            "user_priority": {"status": "pass"},
            "reach": {"status": "incomplete", "heuristic": 100.0},
            "wall": {"status": "incomplete", "heuristic": 100.0},
            "manufacturability": {"status": "incomplete", "heuristic": 100.0},
            "mass": {"status": "incomplete", "heuristic": 100.0},
            "material_use": {"status": "incomplete", "heuristic": 100.0},
        }
        bounds = ([0.0, 0.0, 0.0], [150.0, 100.0, 60.0])
        first = module._score_candidate(value, "compact", bounds, deepcopy(analyses))
        changed = deepcopy(analyses)
        for metric in ("reach", "wall", "manufacturability", "mass", "material_use"):
            changed[metric]["heuristic"] = -100000.0
        second = module._score_candidate(value, "comfort", bounds, changed)
        assert first == second
        assert "reach" not in first
        assert "material_use" not in first
        assert first["scored_weight"] == 5.0
        assert first["total"] is not None


def test_other_family_requires_three_unique_outcome_labels():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        value = _session(root, product_family="industrial pump")
        value["candidate_policy"]["labels"] = ["lightweight", "serviceable", "rugged"]
        assert module.validate_physical_design_session_v2(value, root)["product_family"] == "industrial pump"
        value["candidate_policy"]["labels"] = ["lightweight", "lightweight", "rugged"]
        try:
            module.validate_physical_design_session_v2(value, root)
            raise AssertionError("duplicate outcome label was accepted")
        except module.ConstraintCandidateError:
            pass


if __name__ == "__main__":
    test_schema_and_host_contract()
    test_incomplete_metrics_do_not_influence_provisional_score()
    test_other_family_requires_three_unique_outcome_labels()
    print("CONSTRAINT_CANDIDATES_CONTRACT_OK")
