from __future__ import annotations

import copy
import importlib.util
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[1]
    / "DesignStudio"
    / "interaction_structure.py"
)
SPEC = importlib.util.spec_from_file_location("interaction_structure", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def placement():
    return {
        "schema": "design-studio.component-placement/2",
        "placement_id": "c6d912dd-a466-433f-8faf-1db47d9b741f",
        "component_id": "component.encoder",
        "component_sha256": "1" * 64,
        "document": {"document_id": "keyboard", "revision": 4, "sha256": "2" * 64},
        "transform": {
            "translation_mm": [20.0, 20.0, 8.0],
            "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
        },
        "coordinate_system": "surface_local",
        "surface_anchor": {
            "object_id": "enclosure.outer",
            "patch_id": "body.center.left",
            "patch_sha256": "3" * 64,
            "baseline_sha256": "2" * 64,
            "uv": [0.5, 0.25],
            "tangent_u_world": [1.0, 0.0, 0.0],
            "tangent_v_world": [0.0, 1.0, 0.0],
            "normal_world": [0.0, 0.0, 1.0],
            "offset_mm": 0.0,
        },
        "snap": {
            "grid": True, "surface": True, "axis": True,
            "symmetry": False, "clearance": True,
        },
        "locks": {"position": True, "orientation": True, "surface_anchor": True},
        "clearance_mm": 1.0,
    }


def support_spec():
    return {
        "schema": "design-studio.support-generation/1",
        "document": {"document_id": "keyboard", "revision": 4, "sha256": "2" * 64},
        "placements": [placement()],
        "design_volume_mm": {"min": [0.0, 0.0, 0.0], "max": [140.0, 125.0, 40.0]},
        "anchors": [
            {"id": "encoder", "position_mm": [20.0, 20.0, 8.0], "kind": "load"},
            {"id": "pcb-a", "position_mm": [8.0, 8.0, 5.0], "kind": "pcb"},
        ],
        "forbidden_bounds_mm": [
            {"min": [45.0, 40.0, 2.0], "max": [90.0, 90.0, 25.0]}
        ],
        "load_cases": [{"anchor_id": "encoder", "force_n": [0.0, 0.0, -12.0]}],
        "manufacturing": {
            "process": "fdm",
            "minimum_wall_mm": 2.4,
            "minimum_rib_mm": 1.8,
            "clearance_mm": 1.0,
        },
        "seed": 7,
    }


def test_deterministic_graph():
    first = module.generate_support_graph(support_spec())
    second = module.generate_support_graph(support_spec())
    assert first == second
    assert first["status"] == "pass"
    assert len(first["ribs"]) == 2
    assert first["ribs"][0]["thickness_mm"] > 1.8
    assert first["cavities"][0]["min"] == [44.0, 39.0, 1.0]
    assert len(first["input_sha256"]) == 64
    assert len(first["result_sha256"]) == 64


def test_stale_or_malformed_inputs_fail_closed():
    unknown = support_spec()
    unknown["host_override"] = True
    try:
        module.generate_support_graph(unknown)
        raise AssertionError("unknown support field was accepted")
    except module.InteractionStructureError:
        pass

    outside = support_spec()
    outside["anchors"][0]["position_mm"] = [200.0, 20.0, 8.0]
    try:
        module.generate_support_graph(outside)
        raise AssertionError("out-of-volume anchor was accepted")
    except module.InteractionStructureError:
        pass

    missing_anchor = copy.deepcopy(support_spec())
    missing_anchor["load_cases"][0]["anchor_id"] = "missing"
    try:
        module.generate_support_graph(missing_anchor)
        raise AssertionError("unknown load anchor was accepted")
    except module.InteractionStructureError:
        pass

    stale = copy.deepcopy(support_spec())
    stale["placements"][0]["document"]["revision"] += 1
    try:
        module.generate_support_graph(stale)
        raise AssertionError("stale placement binding was accepted")
    except module.InteractionStructureError:
        pass

    stale_surface = copy.deepcopy(support_spec())
    stale_surface["placements"][0]["surface_anchor"]["baseline_sha256"] = "4" * 64
    try:
        module.generate_support_graph(stale_surface)
        raise AssertionError("stale surface anchor was accepted")
    except module.InteractionStructureError:
        pass

    unstable_face_index = copy.deepcopy(support_spec())
    unstable_face_index["placements"][0]["surface_anchor"]["face_id"] = "Face17"
    try:
        module.generate_support_graph(unstable_face_index)
        raise AssertionError("unstable face-index anchor was accepted")
    except module.InteractionStructureError:
        pass

    unlocked = copy.deepcopy(support_spec())
    unlocked["placements"][0]["locks"]["orientation"] = False
    try:
        module.generate_support_graph(unlocked)
        raise AssertionError("unlocked placement was accepted")
    except module.InteractionStructureError:
        pass


def test_cloud_result_is_digest_bound_and_reuses_host_cavities():
    spec = support_spec()
    result = module.generate_support_graph(spec)
    result["generator"]["name"] = "test-rocm-worker"
    material = dict(result)
    material.pop("result_sha256")
    result["result_sha256"] = module._canonical_digest(material)
    validated = module.validate_support_result(spec, result)
    assert validated["ribs"] == result["ribs"]
    assert validated["cavities"] == result["cavities"]

    tampered = copy.deepcopy(result)
    tampered["ribs"][0]["thickness_mm"] += 1.0
    try:
        module.validate_support_result(spec, tampered)
        raise AssertionError("tampered cloud result was accepted")
    except module.InteractionStructureError:
        pass

    moved_cavity = copy.deepcopy(result)
    moved_cavity["cavities"][0]["min"][0] += 0.5
    material = dict(moved_cavity)
    material.pop("result_sha256")
    moved_cavity["result_sha256"] = module._canonical_digest(material)
    try:
        module.validate_support_result(spec, moved_cavity)
        raise AssertionError("cloud result moved a protected cavity")
    except module.InteractionStructureError:
        pass


if __name__ == "__main__":
    test_deterministic_graph()
    test_stale_or_malformed_inputs_fail_closed()
    test_cloud_result_is_digest_bound_and_reuses_host_cavities()
    print("INTERACTION_STRUCTURE_OK")
