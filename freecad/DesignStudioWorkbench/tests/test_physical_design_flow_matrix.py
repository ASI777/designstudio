#!/usr/bin/env python3
"""Release fixtures for the four supported physical-design entry flows.

These are contract fixtures rather than geometry generators.  The FreeCAD
runtime fixtures exercise the geometry authority; this matrix proves that the
user can enter the same contract through photo-first, sketch-first,
hardware-first, and local surface-redesign workflows without raster evidence
becoming CAD authority.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DesignStudio.constraint_candidates import (  # noqa: E402
    validate_physical_design_session_v2,
)
from DesignStudio.local_redesign_v2 import validate_local_redesign_v2  # noqa: E402
from test_constraint_candidates import _session  # noqa: E402
from test_local_redesign_v2 import captured  # noqa: E402


def _public_validator(name: str):
    from jsonschema.validators import Draft202012Validator

    schema_path = ROOT / "docs" / "schemas" / name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_session(root: Path, value: dict, label: str) -> None:
    validate_physical_design_session_v2(value, root)
    _public_validator("physical-design-session-v2.schema.json").validate(value)
    assert value["input_assets"][0]["geometry_authority"] is False, label
    assert value["candidate_policy"]["exact_candidate_count"] == 3, label
    assert value["candidate_policy"]["labels"] == ["compact", "balanced", "comfort"], label


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="designstudio-flow-matrix-") as directory:
        root = Path(directory)

        # Photo-first: visual evidence establishes the outside-in intent, but
        # dimensions remain explicitly measured/verified by the user.
        photo = _session(root)
        photo["session_id"] = "photo-first"
        photo["workflow_mode"] = "outside_in"
        photo["input_assets"][0].update({
            "input_kind": "object_photo",
            "role": "inspiration",
            "measurement_status": "uncalibrated",
        })
        photo["input_assets"][0].pop("calibration", None)
        _validate_session(root, photo, "photo-first")

        # Sketch-first: a calibrated sketch can contribute measured intent,
        # never authoritative geometry.
        sketch = _session(root)
        sketch["session_id"] = "sketch-first"
        sketch["workflow_mode"] = "outside_in"
        sketch["input_assets"][0].update({
            "input_kind": "hand_sketch",
            "role": "silhouette",
            "measurement_status": "user_calibrated",
            "calibration": {"method": "known_length", "pixels_per_mm": 4.0, "verified": True},
        })
        _validate_session(root, sketch, "sketch-first")

        # Hardware-first: locked PCB and service volumes drive the inside-out
        # enclosure workflow.
        hardware = deepcopy(_session(root))
        hardware["session_id"] = "hardware-first"
        hardware["workflow_mode"] = "inside_out"
        hardware["occupied_volumes"][0]["kind"] = "pcb"
        hardware["occupied_volumes"][0]["locked"] = True
        _validate_session(root, hardware, "hardware-first")
        assert hardware["occupied_volumes"][0]["source"] == "verified_component"

        # Local surface redesign: selected topology, boundary evidence, and
        # rollback constraints are captured independently of the session flow.
        redesign = captured()
        redesign["schema"] = "design-studio.local-redesign/2"
        redesign["document"]["file_sha256"] = "a" * 64
        _public_validator("local-redesign-v2.schema.json").validate(redesign)
        assert validate_local_redesign_v2(redesign)["status"] == "captured"
        assert redesign["constraints"]["baseline_must_be_retained"] is True
        assert redesign["constraints"]["unselected_objects_must_remain_unchanged"] is True

    print("PHYSICAL_DESIGN_FLOW_MATRIX_OK")


if __name__ == "__main__":
    main()
