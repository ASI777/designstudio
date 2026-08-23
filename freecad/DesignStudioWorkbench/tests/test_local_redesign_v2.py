#!/usr/bin/env python3
"""Contract tests for topology-aware local-redesign/2."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.local_redesign_v2 import (  # noqa: E402
    SCHEMA,
    PhysicalDesignError,
    canonical_digest,
    validate_local_redesign_v2,
)


def digest(seed: str) -> str:
    return (seed * 64)[:64]


def captured() -> dict:
    return {
        "schema": SCHEMA,
        "redesign_id": "local-v2-contract",
        "revision": 1,
        "status": "captured",
        "document": {"document_id": "doc", "path": "mechanical/doc.FCStd",
                     "file_sha256": digest("a")},
        "target": {
            "semantic_id": "target-body", "object_name": "TargetBody",
            "target_shape_sha256": digest("b"),
            "selected_faces": ["Face1"],
            "selected_face_signatures": [{"face": "Face1", "shape_sha256": digest("c"),
                                           "area_mm2": 240.0, "center_mm": [10.0, 6.0, 10.0]}],
            "boundary_edges": [{"edge": "Edge1", "signature_sha256": digest("d"),
                                 "adjacent_faces": ["Face1", "Face2"],
                                 "length_mm": 20.0, "midpoint_mm": [10.0, 0.0, 10.0]}],
            "local_frame": {"origin_mm": [10.0, 6.0, 10.0],
                            "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0],
                            "z_axis": [0.0, 0.0, 1.0]},
        },
        "intent": "Improve the selected surface while preserving its boundary.",
        "constraints": {
            "permitted_expansion_mm": 0.5, "boundary_deviation_mm": 0.05,
            "g1_normal_angle_deg": 1.0, "g2_curvature_delta": 0.001,
            "continuity_required": "G0", "protected_objects": [],
            "manufacturing": {"process": "fdm", "minimum_wall_mm": 2.0,
                               "minimum_blend_radius_mm": 1.2, "max_overhang_deg": 45.0},
            "unselected_objects_must_remain_unchanged": True,
            "baseline_must_be_retained": True,
        },
        "program": None, "candidate_command_id": None, "candidate_faces": [],
        "provenance": {"created_by": "test", "created_utc": "2026-08-13T00:00:00+00:00",
                       "model_role": "user", "raster_geometry_authority": False},
    }


def expect_invalid(value, label):
    try:
        validate_local_redesign_v2(value)
    except PhysicalDesignError:
        return
    raise AssertionError(label + " was accepted")


def main():
    value = captured()
    assert validate_local_redesign_v2(value)["schema"] == SCHEMA
    schema_path = ROOT / "docs" / "schemas" / "local-redesign-v2.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    discard_schema = json.loads((ROOT / "docs" / "schemas" /
        "local-redesign-discard-v2.schema.json").read_text(encoding="utf-8"))
    from jsonschema.validators import Draft202012Validator
    from jsonschema import ValidationError
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(discard_schema)
    Draft202012Validator(schema).validate(value)

    invalid = copy.deepcopy(value)
    invalid["target"]["boundary_edges"] = []
    expect_invalid(invalid, "missing boundary edges")

    invalid = copy.deepcopy(value)
    invalid["target"]["local_frame"]["z_axis"] = [0.0, 0.0, 2.0]
    expect_invalid(invalid, "non-unit local frame")

    invalid = copy.deepcopy(value)
    invalid["constraints"]["continuity_required"] = "G3"
    expect_invalid(invalid, "unsupported continuity")

    invalid = copy.deepcopy(value)
    invalid["status"] = "program_ready"
    invalid["program"] = {"not": "a CAD program"}
    invalid["candidate_command_id"] = "patch"
    invalid["candidate_faces"] = ["Face1"]
    expect_invalid(invalid, "unvalidated candidate program")

    public_invalid = copy.deepcopy(value)
    public_invalid["target"]["selected_faces"] = []
    try:
        Draft202012Validator(schema).validate(public_invalid)
    except ValidationError:
        pass
    else:
        raise AssertionError("public schema accepted an empty selected face set")
    assert canonical_digest(value) == canonical_digest(copy.deepcopy(value))
    print("LOCAL_REDESIGN_V2_CONTRACT_OK")


if __name__ == "__main__":
    main()
