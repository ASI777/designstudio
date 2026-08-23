from __future__ import annotations

import copy
import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "DesignStudio" / "mesh_surface_fit.py"
SPEC = importlib.util.spec_from_file_location("mesh_surface_fit", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def valid_evidence():
    evidence = {
        "schema": module.FIT_SCHEMA,
        "request_sha256": "1" * 64,
        "native_mesh_sha256": "a" * 64,
        "deviation_reference_sha256": "a" * 64,
        "density_results": [
            {"kind": "decimated", "faces": 100_000},
            {"kind": "decimated", "faces": 300_000},
            {"kind": "native", "faces": 766_672},
            {"kind": "subdivided", "faces": 1_500_000},
        ],
        "patch_count": 2,
        "patch_hashes": {"body.left": "b" * 64, "body.right": "c" * 64},
        "continuity": {"body.left/body.right": "G1"},
        "deviation_metrics": {
            "median_mm": 0.4, "p95_mm": 0.7, "maximum_mm": 1.4,
        },
        "valid_outer_shell": True,
        "ap242_roundtrip_valid": True,
        "llm_geometry_authority": False,
    }
    evidence["result_sha256"] = module._digest(evidence)
    return evidence


def test_request_has_native_deviation_reference_and_four_ablations():
    request = module.new_fit_request(
        native_mesh_sha256="a" * 64,
        native_vertices=383_292,
        native_faces=766_672,
    )
    assert request["deviation_reference_sha256"] == "a" * 64
    assert len(request["density_ablations"]) == 4
    assert request["segmentation"]["maximum_patches"] == 96


def test_fit_evidence_passes_only_with_native_reference():
    assert module.validate_fit_evidence(
        valid_evidence(), expected_native_sha256="a" * 64
    )["patch_count"] == 2
    stale = valid_evidence()
    stale["deviation_reference_sha256"] = "d" * 64
    stale["result_sha256"] = module._digest({
        key: value for key, value in stale.items() if key != "result_sha256"
    })
    try:
        module.validate_fit_evidence(stale, expected_native_sha256="a" * 64)
        raise AssertionError("non-native deviation reference was accepted")
    except module.MeshSurfaceFitError:
        pass


def test_llm_geometry_authority_and_excessive_patch_count_fail():
    for field, value in (("llm_geometry_authority", True), ("patch_count", 97)):
        candidate = valid_evidence()
        candidate[field] = value
        candidate["result_sha256"] = module._digest({
            key: item for key, item in candidate.items()
            if key != "result_sha256"
        })
        try:
            module.validate_fit_evidence(candidate, expected_native_sha256="a" * 64)
            raise AssertionError(f"unsafe {field} was accepted")
        except module.MeshSurfaceFitError:
            pass


if __name__ == "__main__":
    test_request_has_native_deviation_reference_and_four_ablations()
    test_fit_evidence_passes_only_with_native_reference()
    test_llm_geometry_authority_and_excessive_patch_count_fail()
    print("MESH_SURFACE_FIT_OK")
