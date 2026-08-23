from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile


WORKBENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKBENCH))

from DesignStudio import physical_design as module  # noqa: E402


def _session(root: Path):
    mechanical = root / "mechanical" / "product.FCStd"
    mechanical.parent.mkdir(parents=True, exist_ok=True)
    mechanical.write_bytes(b"FCStd-test-document")
    image = root / "assets" / "front.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nphysical-design-reference")
    return {
        "schema": "design-studio.physical-design-session/1",
        "session_id": "controller-physical-design",
        "revision": 1,
        "workflow_mode": "inside_out",
        "document": {
            "document_id": "Product",
            "path": "mechanical/product.FCStd",
            "sha256": hashlib.sha256(mechanical.read_bytes()).hexdigest(),
        },
        "intent": "Place measured hardware first, then design the ergonomic body around it.",
        "input_assets": [{
            "asset_id": "front-photo",
            "path": "assets/front.png",
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "media_type": "image/png",
            "input_kind": "object_photo",
            "role": "inspiration",
            "geometry_authority": False,
            "measurement_status": "uncalibrated",
        }],
        "occupied_volumes": [{
            "volume_id": "pcb-volume",
            "semantic_id": "pcb-main",
            "kind": "pcb",
            "min_mm": [10.0, 10.0, 4.0],
            "max_mm": [80.0, 50.0, 8.0],
            "clearance_mm": 2.0,
            "locked": True,
            "source": "verified_component",
        }],
        "desire_capture": [{
            "desire_id": "small-hand-reach",
            "statement": "Comfortable access for smaller hands",
            "priority": "required",
            "status": "user_confirmed",
        }],
        "material_constraints": [{
            "region_id": "upper-shell",
            "candidate_materials": ["PETG"],
            "requirements": ["FDM prototype"],
            "evidence_status": "unverified",
        }],
        "manufacturing_constraints": {
            "processes": ["FDM"], "units": "mm",
            "minimum_wall_mm": 2.0, "minimum_feature_mm": 1.2,
            "status": "provisional",
        },
        "geometry_authority": {
            "authoritative_sources": [
                "freecad_brep", "typed_mechanical_cad_program", "verified_measurements"],
            "raster_and_sketch_are_geometry_authority": False,
            "unknown_dimensions_policy": "blocked_until_measured_or_explicitly_constrained",
        },
        "provenance": {
            "created_by": "test", "created_utc": "2026-08-13T00:00:00+00:00",
            "user_prompt": "Build the inside hardware first and use this photo for inspiration.",
        },
    }


def _captured_redesign():
    digest = "a" * 64
    return {
        "schema": "design-studio.local-redesign/1",
        "redesign_id": "redesign-grip",
        "revision": 1,
        "status": "captured",
        "document": {"document_id": "Product", "path": "mechanical/product.FCStd",
                     "file_sha256": "b" * 64},
        "physical_design_session_path": "contracts/physical-design-session-controller.json",
        "selection": {
            "semantic_id": "grip-left", "object_name": "GripLeft", "kind": "faces",
            "subelements": ["Face2", "Face3"], "target_shape_sha256": digest,
            "selection_shape_sha256": "c" * 64,
            "bounds_mm": {"min_mm": [0, 0, 0], "max_mm": [10, 20, 30]},
        },
        "intent": "Reduce the pressure point while preserving every adjoining boundary.",
        "constraints": {
            "max_expansion_mm": 2.0, "outside_scope_tolerance_mm3": 0.001,
            "protected_objects": [{"semantic_id": "trigger-left",
                                   "shape_sha256": "d" * 64, "clearance_mm": 1.0}],
            "unselected_objects_must_remain_unchanged": True,
            "baseline_must_be_retained": True,
        },
        "program": None, "candidate_command_id": None,
        "drawing_output": {
            "directory": "generated/redesign/redesign-grip",
            "views": ["front", "rear", "left", "right", "top", "bottom"],
            "formats": ["svg", "dxf"], "source": "candidate_freecad_brep",
        },
        "provenance": {
            "created_by": "test", "created_utc": "2026-08-13T00:00:00+00:00",
            "model_role": "deterministic_host", "raster_geometry_authority": False,
        },
    }


def test_session_accepts_inside_out_photo_and_keeps_it_non_authoritative():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        session = _session(root)
        validated = module.validate_physical_design_session(session, root)
        assert validated["workflow_mode"] == "inside_out"
        assert validated["input_assets"][0]["geometry_authority"] is False
        assert validated["occupied_volumes"][0]["locked"] is True
        result = module.create_physical_design_session(root, session)
        assert Path(result["path"]).is_file()


def test_session_rejects_pixel_authority_tamper_and_stale_asset():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        session = _session(root)
        unsafe = deepcopy(session)
        unsafe["input_assets"][0]["geometry_authority"] = True
        try:
            module.validate_physical_design_session(unsafe, root)
            raise AssertionError("photo became manufacturing geometry")
        except module.PhysicalDesignError:
            pass
        (root / "assets" / "front.png").write_bytes(b"tampered")
        try:
            module.validate_physical_design_session(session, root)
            raise AssertionError("stale reference asset was accepted")
        except module.PhysicalDesignError:
            pass


def test_local_redesign_fails_closed_on_scope_and_baseline_rules():
    valid = _captured_redesign()
    assert module.validate_local_redesign(valid)["status"] == "captured"
    for mutate in (
        lambda value: value["selection"].update({"subelements": ["Edge1"]}),
        lambda value: value["constraints"].update({"baseline_must_be_retained": False}),
        lambda value: value["constraints"].update({"unselected_objects_must_remain_unchanged": False}),
        lambda value: value["provenance"].update({"raster_geometry_authority": True}),
    ):
        invalid = deepcopy(valid)
        mutate(invalid)
        try:
            module.validate_local_redesign(invalid)
            raise AssertionError("unsafe redesign was accepted")
        except module.PhysicalDesignError:
            pass


def test_program_ready_requires_candidate_from_typed_program():
    redesign = _captured_redesign()
    redesign["status"] = "program_ready"
    redesign["program"] = {
        "schema": "design-studio.mechanical-cad-program/1",
        "program_id": "grip-candidate", "units": "mm", "author": "test",
        "commands": [{"id": "candidate", "op": "part.box",
                      "params": {"length_mm": 10, "width_mm": 20, "height_mm": 30},
                      "provenance": ["redesign-grip#measured-drawing"]}],
        "checks": [{"kind": "valid_shape", "target": "candidate"}],
    }
    redesign["candidate_command_id"] = "missing"
    try:
        module.validate_local_redesign(redesign)
        raise AssertionError("unproduced candidate command was accepted")
    except module.PhysicalDesignError:
        pass
    redesign["candidate_command_id"] = "candidate"
    assert module.validate_local_redesign(redesign)["program"]["units"] == "mm"


def test_schema_files_are_valid_json():
    root = Path(__file__).resolve().parents[3]
    for name in ("physical-design-session-v1.schema.json", "local-redesign-v1.schema.json"):
        schema = json.loads((root / "docs" / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        assert schema["$schema"].endswith("2020-12/schema")


if __name__ == "__main__":
    test_session_accepts_inside_out_photo_and_keeps_it_non_authoritative()
    test_session_rejects_pixel_authority_tamper_and_stale_asset()
    test_local_redesign_fails_closed_on_scope_and_baseline_rules()
    test_program_ready_requires_candidate_from_typed_program()
    test_schema_files_are_valid_json()
    print("PHYSICAL_DESIGN_CONTRACT_TESTS_OK")
