"""Fail-closed contract for mesh-derived editable surface fitting.

This module does not pretend a faceted GLB conversion is an editable fit. It
defines the evidence a Point2CAD-style worker and the exact FreeCAD/OCCT host
must produce before :class:`CarBodyBaseline` can be created.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


FIT_SCHEMA = "design-studio.mesh-surface-fit/1"
DENSITY_TARGETS = (100_000, 300_000, 1_500_000)
MAX_PATCHES = 96


class MeshSurfaceFitError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value):
        raise MeshSurfaceFitError(f"{path} must be a lowercase SHA-256")
    return value


def _finite(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise MeshSurfaceFitError(f"{path} must be finite")
    result = float(value)
    if result < minimum:
        raise MeshSurfaceFitError(f"{path} must be at least {minimum:g}")
    return result


def new_fit_request(
    *, native_mesh_sha256: str, native_vertices: int, native_faces: int,
    seed: int = 0,
) -> dict[str, Any]:
    _sha256(native_mesh_sha256, "native_mesh_sha256")
    if type(native_vertices) is not int or native_vertices < 4:
        raise MeshSurfaceFitError("native_vertices must describe a non-empty mesh")
    if type(native_faces) is not int or native_faces < 4:
        raise MeshSurfaceFitError("native_faces must describe a non-empty mesh")
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise MeshSurfaceFitError("seed is invalid")
    material = {
        "schema": "design-studio.mesh-surface-fit-request/1",
        "native_mesh_sha256": native_mesh_sha256,
        "native_vertices": native_vertices,
        "native_faces": native_faces,
        "density_ablations": [
            {"kind": "decimated", "target_faces": 100_000},
            {"kind": "decimated", "target_faces": 300_000},
            {"kind": "native", "target_faces": native_faces},
            {"kind": "subdivided", "target_faces": 1_500_000},
        ],
        "segmentation": {
            "signals": ["curvature", "dihedral", "normal_discontinuity"],
            "maximum_patches": MAX_PATCHES,
        },
        "fit_loss": {
            "terms": ["point", "normal", "fairness"],
            "geometry_authority": "deterministic_optimizer",
        },
        "deviation_reference_sha256": native_mesh_sha256,
        "seed": seed,
    }
    material["request_sha256"] = _digest(material)
    return material


def validate_fit_evidence(
    evidence: Any, *, expected_native_sha256: str,
) -> dict[str, Any]:
    required = {
        "schema", "request_sha256", "native_mesh_sha256",
        "deviation_reference_sha256", "density_results", "patch_count",
        "patch_hashes", "continuity", "deviation_metrics",
        "valid_outer_shell", "ap242_roundtrip_valid",
        "llm_geometry_authority", "result_sha256",
    }
    if not isinstance(evidence, dict) or set(evidence) != required:
        raise MeshSurfaceFitError("mesh-fit evidence fields are invalid")
    if evidence["schema"] != FIT_SCHEMA:
        raise MeshSurfaceFitError(f"mesh-fit evidence must use {FIT_SCHEMA}")
    expected = _sha256(expected_native_sha256, "expected_native_sha256")
    if _sha256(evidence["native_mesh_sha256"], "native_mesh_sha256") != expected \
            or _sha256(evidence["deviation_reference_sha256"],
                       "deviation_reference_sha256") != expected:
        raise MeshSurfaceFitError("deviation was not measured against the native mesh")
    density = evidence["density_results"]
    if not isinstance(density, list) or len(density) != 4:
        raise MeshSurfaceFitError("all four density ablations are required")
    kinds = [entry.get("kind") for entry in density if isinstance(entry, dict)]
    faces = [entry.get("faces") for entry in density if isinstance(entry, dict)]
    if len(kinds) != 4 or kinds != ["decimated", "decimated", "native", "subdivided"]:
        raise MeshSurfaceFitError("density ablations are incomplete or out of order")
    if any(type(value) is not int or value < 4 for value in faces):
        raise MeshSurfaceFitError("density ablation face counts are invalid")
    if abs(faces[0] - 100_000) > 5_000 or abs(faces[1] - 300_000) > 15_000 \
            or abs(faces[3] - 1_500_000) > 75_000:
        raise MeshSurfaceFitError("density ablations differ from required targets")
    patch_count = evidence["patch_count"]
    patch_hashes = evidence["patch_hashes"]
    if type(patch_count) is not int or not 1 <= patch_count <= MAX_PATCHES:
        raise MeshSurfaceFitError("patch_count must be between 1 and 96")
    if not isinstance(patch_hashes, dict) or len(patch_hashes) != patch_count:
        raise MeshSurfaceFitError("patch hashes do not match patch_count")
    for patch_id, digest in patch_hashes.items():
        if not isinstance(patch_id, str) or not patch_id:
            raise MeshSurfaceFitError("patch identifiers must be stable strings")
        _sha256(digest, f"patch_hashes.{patch_id}")
    continuity = evidence["continuity"]
    if not isinstance(continuity, dict) or any(
            grade not in {"G0", "G1", "G2"} for grade in continuity.values()):
        raise MeshSurfaceFitError("continuity grades are invalid")
    metrics = evidence["deviation_metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {
            "median_mm", "p95_mm", "maximum_mm"}:
        raise MeshSurfaceFitError("deviation metrics are invalid")
    limits = {"median_mm": 0.5, "p95_mm": 0.75, "maximum_mm": 1.5}
    normalized = {
        key: _finite(metrics[key], f"deviation_metrics.{key}")
        for key in limits
    }
    if any(normalized[key] > limit for key, limit in limits.items()):
        raise MeshSurfaceFitError("native-mesh deviation exceeds the CAD gate")
    if evidence["valid_outer_shell"] is not True \
            or evidence["ap242_roundtrip_valid"] is not True:
        raise MeshSurfaceFitError("exact B-Rep and AP242 gates must pass")
    if evidence["llm_geometry_authority"] is not False:
        raise MeshSurfaceFitError("an LLM cannot be geometry authority")
    result_material = dict(evidence)
    claimed_result = result_material.pop("result_sha256")
    if _sha256(claimed_result, "result_sha256") != _digest(result_material):
        raise MeshSurfaceFitError("mesh-fit result digest is invalid")
    return evidence
