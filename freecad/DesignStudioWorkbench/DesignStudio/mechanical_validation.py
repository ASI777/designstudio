"""Fail-closed manufacturing and geometry validation for mechanical results."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .manufacturing_rules import canonical_digest, check_feature, validate_profile
from .rib_network import validate_rib_network


SCHEMA = "design-studio.mechanical-validation/1"


class MechanicalValidationError(ValueError):
    """Raised when validation input is malformed."""


def _finding(code: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, **extra}


def validate_network(network: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    profile = validate_profile(profile)
    validate_rib_network(network, profile)
    findings = []
    for rib in network["ribs"]:
        findings.extend(check_feature({"id": rib["id"], "kind": "rib",
                                       "thickness_mm": rib["thickness_mm"],
                                       "draft_deg": rib["draft_deg"],
                                       "root_fillet_mm": rib["root_fillet_mm"],
                                       "clearance_mm": rib["clearance_mm"]}, profile))
    node_ids = {node["id"] for node in network["nodes"]}
    for rib in network["ribs"]:
        if rib["start_node"] not in node_ids and not rib["start_node"].endswith(":start"):
            findings.append(_finding("UNKNOWN_RIB_START", "error",
                                     f"{rib['id']} references an unknown start node"))
        if rib["end_node"] not in node_ids and not rib["end_node"].startswith("shell:") \
                and not rib["end_node"].endswith(":end"):
            findings.append(_finding("UNKNOWN_RIB_END", "error",
                                     f"{rib['id']} references an unknown end node"))
    return {"network_digest": network["result_sha256"],
            "profile_digest": canonical_digest(profile), "findings": findings,
            "status": "pass" if not any(item["severity"] == "error" for item in findings)
            else "fail"}


def _shape_valid(shape: Any) -> tuple[bool, str]:
    if shape is None or shape.isNull():
        return False, "shape is null"
    if not shape.isValid():
        return False, "B-Rep validity check failed"
    if not getattr(shape, "Solids", None):
        return False, "shape contains no solid"
    if not math.isfinite(float(getattr(shape, "Volume", 0.0))) or shape.Volume <= 0.0:
        return False, "solid has no positive volume"
    return True, "valid solid"


def validate_freecad_shapes(*, frame_shape: Any, protected_shapes: list[tuple[str, Any]] | None = None,
                            rib_shapes: list[tuple[str, Any]] | None = None,
                            minimum_clearance_mm: float = 0.0,
                            require_single_solid: bool = False) -> dict[str, Any]:
    """Run exact host-side checks on already reconstructed B-Reps."""
    findings = []
    valid, message = _shape_valid(frame_shape)
    findings.append(_finding("BREP_VALID" if valid else "BREP_INVALID",
                             "pass" if valid else "error", message))
    if valid and require_single_solid and len(frame_shape.Solids) != 1:
        findings.append(_finding("FRAME_DISCONNECTED", "error",
                                 "frame result contains multiple disconnected solids",
                                 solid_count=len(frame_shape.Solids)))

    protected_shapes = protected_shapes or []
    rib_shapes = rib_shapes or []
    for rib_id, rib_shape in rib_shapes:
        good, detail = _shape_valid(rib_shape)
        findings.append(_finding("RIB_BREP_VALID" if good else "RIB_BREP_INVALID",
                                 "pass" if good else "error", detail, rib_id=rib_id))
        for protected_id, protected_shape in protected_shapes:
            try:
                common_volume = float(rib_shape.common(protected_shape).Volume)
                distance = float(rib_shape.distToShape(protected_shape)[0])
            except Exception as exc:
                findings.append(_finding("CLEARANCE_CHECK_ERROR", "error",
                                         f"exact clearance check failed: {exc}",
                                         rib_id=rib_id, protected_id=protected_id))
                continue
            if common_volume > 1.0e-7:
                findings.append(_finding("RIB_INTERFERENCE", "error",
                                         f"{rib_id} intersects protected object {protected_id}",
                                         rib_id=rib_id, protected_id=protected_id,
                                         common_volume_mm3=common_volume))
            elif distance + 1.0e-7 < minimum_clearance_mm:
                findings.append(_finding("RIB_CLEARANCE_TOO_SMALL", "error",
                                         f"{rib_id} is too close to {protected_id}",
                                         rib_id=rib_id, protected_id=protected_id,
                                         distance_mm=distance,
                                         required_mm=minimum_clearance_mm))
    errors = [item for item in findings if item["severity"] == "error"]
    return {"schema": SCHEMA, "status": "pass" if not errors else "fail",
            "release_ready": not errors, "findings": findings,
            "result_sha256": canonical_digest({"findings": findings,
                                                "status": "pass" if not errors else "fail"})}


def validate_wall_metadata(objects: list[tuple[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """Validate explicit wall metadata and flag missing exact measurements.

    A CAD host must not claim a wall-thickness pass from a bounding box.  If a
    feature has no measured `WallThicknessMM` property, this gate is explicitly
    incomplete so an engineer can request a mesh/section-based measurement.
    """
    profile = validate_profile(profile)
    findings = []
    for object_id, obj in objects:
        if not hasattr(obj, "WallThicknessMM"):
            findings.append(_finding("WALL_THICKNESS_INCOMPLETE", "incomplete",
                                     f"no exact wall measurement is attached to {object_id}",
                                     object_id=object_id))
            continue
        value = float(obj.WallThicknessMM)
        if value < profile["wall"]["minimum_mm"]:
            findings.append(_finding("WALL_TOO_THIN", "error",
                                     f"{object_id} is below the process minimum",
                                     object_id=object_id, actual_mm=value,
                                     required_mm=profile["wall"]["minimum_mm"]))
        else:
            findings.append(_finding("WALL_MEASURED", "pass",
                                     f"{object_id} has a valid measured wall",
                                     object_id=object_id, actual_mm=value))
    errors = [item for item in findings if item["severity"] == "error"]
    incomplete = [item for item in findings if item["severity"] == "incomplete"]
    return {"status": "fail" if errors else "incomplete" if incomplete else "pass",
            "release_ready": not errors and not incomplete, "findings": findings}


__all__ = ["SCHEMA", "MechanicalValidationError", "validate_network",
           "validate_freecad_shapes", "validate_wall_metadata"]
