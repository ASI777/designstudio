"""Vector-native CAD contracts and OCCT execution for DesignStudio v2.

The v2 layer is deliberately separate from the v1 mechanical executor.  It
accepts typed NURBS/B-spline definitions, validates them before touching a
FreeCAD document, and commits the complete feature graph atomically.  The
FreeCAD B-Rep is authoritative; drawing files are deterministic evidence
derived from the resulting shape and never become geometry input.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any


MECHANICAL_SCHEMA = "design-studio.mechanical-cad-program/2"
SURFACE_SCHEMA = "design-studio.surface-design/2"
CURVE_CLASSIFICATIONS = {
    "reference-locked", "compatible-interface", "original-designed"
}
OPERATIONS = {
    "sketch.bspline", "curve.bspline3d", "feature.loft",
    "feature.guided_loft", "feature.surface_fill", "feature.thicken",
    "feature.boolean", "feature.compound", "feature.split", "feature.box",
    "feature.rib",
    "drawing.project",
}
MAX_COMMANDS = 512
MAX_POINTS = 2048
MAX_COORDINATE_MM = 1_000_000.0


class VectorNativeCadError(ValueError):
    """Raised for invalid v2 contracts or failed OCCT construction."""


def _finite(value: Any, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise VectorNativeCadError(f"{path} must be a finite number")
    result = float(value)
    if abs(result) > MAX_COORDINATE_MM:
        raise VectorNativeCadError(f"{path} exceeds the ±1,000,000 mm limit")
    return result


def _positive(value: Any, path: str) -> float:
    result = _finite(value, path)
    if result <= 0.0:
        raise VectorNativeCadError(f"{path} must be greater than zero")
    return result


def _nonnegative(value: Any, path: str) -> float:
    result = _finite(value, path)
    if result < 0.0:
        raise VectorNativeCadError(f"{path} must not be negative")
    return result


def _vector(value: Any, path: str, length: int = 3) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise VectorNativeCadError(f"{path} must contain {length} coordinates")
    return [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value):
        raise VectorNativeCadError(
            f"{path} must match [A-Za-z][A-Za-z0-9_.-]{{0,127}}")
    return value


def _string_list(value: Any, path: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise VectorNativeCadError(f"{path} must contain at least {minimum} item(s)")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise VectorNativeCadError(f"{path} must contain non-empty strings")
    return list(value)


def _canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()


def control_datum_digest(point_mm: list[float]) -> str:
    """Return the stable digest required for a locked control datum."""
    return _canonical({"point_mm": [float(value) for value in point_mm]})


def _envelope(value: Any, path: str = "envelope") -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"min_mm", "max_mm"}:
        raise VectorNativeCadError(f"{path} must contain min_mm and max_mm")
    minimum = _vector(value["min_mm"], f"{path}.min_mm")
    maximum = _vector(value["max_mm"], f"{path}.max_mm")
    if any(minimum[index] >= maximum[index] for index in range(3)):
        raise VectorNativeCadError(f"{path}.min_mm must be less than max_mm")
    return {"min_mm": minimum, "max_mm": maximum}


def _control_datums(value: Any, envelope: dict[str, list[float]]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise VectorNativeCadError("control_datums must contain at least one locked datum")
    minimum, maximum = envelope["min_mm"], envelope["max_mm"]
    result, ids = [], set()
    for index, datum in enumerate(value):
        path = f"control_datums[{index}]"
        if not isinstance(datum, dict) or set(datum) != {"id", "point_mm", "locked", "digest"}:
            raise VectorNativeCadError(f"{path} must contain id, point_mm, locked and digest")
        datum_id = _id(datum["id"], f"{path}.id")
        if datum_id in ids:
            raise VectorNativeCadError(f"duplicate control datum {datum_id!r}")
        point = _vector(datum["point_mm"], f"{path}.point_mm")
        if datum["locked"] is not True:
            raise VectorNativeCadError(f"{path}.locked must be true")
        digest = datum["digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise VectorNativeCadError(f"{path}.digest must be a SHA-256 hex digest")
        if digest.lower() != control_datum_digest(point):
            raise VectorNativeCadError(f"{path}.digest does not match point_mm")
        if any(point[axis] < minimum[axis] - 1e-9 or point[axis] > maximum[axis] + 1e-9
               for axis in range(3)):
            raise VectorNativeCadError(f"{path}.point_mm is outside the locked envelope")
        ids.add(datum_id)
        result.append({"id": datum_id, "point_mm": point, "locked": True, "digest": digest.lower()})
    return result


def _curve_definition(value: Any, path: str, *, envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VectorNativeCadError(f"{path} must be an object")
    required = {"degree", "knots", "multiplicities", "weights", "control_points",
                "closed", "periodic", "classification", "provenance"}
    missing = required - set(value)
    if missing:
        raise VectorNativeCadError(f"{path} missing {sorted(missing)}")
    unknown = set(value) - required
    if unknown:
        raise VectorNativeCadError(f"{path} has unknown fields {sorted(unknown)}")
    degree = value["degree"]
    if type(degree) is not int or not 1 <= degree <= 8:
        raise VectorNativeCadError(f"{path}.degree must be an integer from 1 to 8")
    knots = value["knots"]
    mults = value["multiplicities"]
    if not isinstance(knots, list) or len(knots) < 2:
        raise VectorNativeCadError(f"{path}.knots must contain at least two values")
    if not isinstance(mults, list) or len(mults) != len(knots):
        raise VectorNativeCadError(f"{path}.multiplicities must match knots")
    knot_values = [_finite(item, f"{path}.knots[{index}]")
                   for index, item in enumerate(knots)]
    if any(knot_values[index] >= knot_values[index + 1]
           for index in range(len(knot_values) - 1)):
        raise VectorNativeCadError(f"{path}.knots must be strictly increasing")
    multiplicities = []
    for index, item in enumerate(mults):
        if type(item) is not int or item <= 0:
            raise VectorNativeCadError(f"{path}.multiplicities[{index}] must be positive integer")
        multiplicities.append(item)
    points = value["control_points"]
    if not isinstance(points, list) or not degree + 1 <= len(points) <= MAX_POINTS:
        raise VectorNativeCadError(
            f"{path}.control_points must contain degree+1 to {MAX_POINTS} points")
    point_values = [_vector(point, f"{path}.control_points[{index}]")
                    for index, point in enumerate(points)]
    weights = value["weights"]
    if not isinstance(weights, list) or len(weights) != len(point_values):
        raise VectorNativeCadError(f"{path}.weights must match control_points")
    weight_values = [_positive(weight, f"{path}.weights[{index}]")
                     for index, weight in enumerate(weights)]
    if type(value["closed"]) is not bool or type(value["periodic"]) is not bool:
        raise VectorNativeCadError(f"{path}.closed and periodic must be boolean")
    if value["periodic"] and not value["closed"]:
        raise VectorNativeCadError(f"{path}.periodic curves must also be closed")
    classification = value["classification"]
    if classification not in CURVE_CLASSIFICATIONS:
        raise VectorNativeCadError(f"{path}.classification is unsupported")
    provenance = _string_list(value["provenance"], f"{path}.provenance")
    total_mult = sum(multiplicities)
    if value["periodic"]:
        if total_mult != len(knots):
            raise VectorNativeCadError(
                f"{path}.periodic knot multiplicity sum must equal knot count")
    elif total_mult != len(point_values) + degree + 1:
        raise VectorNativeCadError(
            f"{path} knot multiplicity sum must equal poles + degree + 1")
    if envelope is not None:
        minimum = _vector(envelope.get("min_mm"), "envelope.min_mm")
        maximum = _vector(envelope.get("max_mm"), "envelope.max_mm")
        if any(minimum[index] >= maximum[index] for index in range(3)):
            raise VectorNativeCadError("envelope min_mm must be less than max_mm")
        for point_index, point in enumerate(point_values):
            if any(point[index] < minimum[index] - 1.0e-9 or
                   point[index] > maximum[index] + 1.0e-9 for index in range(3)):
                raise VectorNativeCadError(
                    f"{path}.control_points[{point_index}] is outside the locked envelope")
    return {
        "degree": degree, "knots": knot_values, "multiplicities": multiplicities,
        "weights": weight_values, "control_points": point_values,
        "closed": bool(value["closed"]), "periodic": bool(value["periodic"]),
        "classification": classification, "provenance": provenance,
    }


def _continuity(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {"required": "G1", "max_normal_angle_deg": 1.0}
    if not isinstance(value, dict):
        raise VectorNativeCadError(f"{path} must be an object")
    allowed = {"required", "max_normal_angle_deg", "max_curvature_delta"}
    unknown = set(value) - allowed
    if unknown:
        raise VectorNativeCadError(f"{path} has unknown fields {sorted(unknown)}")
    missing = {"required", "max_normal_angle_deg"} - set(value)
    if missing:
        raise VectorNativeCadError(f"{path} missing {sorted(missing)}")
    required = value["required"]
    if required not in {"G0", "G1", "G2"}:
        raise VectorNativeCadError(f"{path}.required must be G0, G1 or G2")
    angle = _nonnegative(value["max_normal_angle_deg"],
                        f"{path}.max_normal_angle_deg")
    if angle > 180.0:
        raise VectorNativeCadError(f"{path}.max_normal_angle_deg is unreasonable")
    curvature = _nonnegative(value.get("max_curvature_delta", 1.0e-3),
                             f"{path}.max_curvature_delta")
    return {"required": required, "max_normal_angle_deg": angle,
            "max_curvature_delta": curvature}


def _reference(value: Any, path: str, previous: set[str]) -> str:
    reference = _id(value, path)
    if reference not in previous:
        raise VectorNativeCadError(f"{path} references {reference!r} before it is created")
    return reference


def _validate_command(command: Any, index: int, previous: set[str], envelope: dict[str, Any] | None) -> dict[str, Any]:
    path = f"commands[{index}]"
    if not isinstance(command, dict) or set(command) != {"id", "op", "params", "provenance"}:
        raise VectorNativeCadError(f"{path} must contain id, op, params and provenance")
    command_id = _id(command["id"], f"{path}.id")
    operation = command["op"]
    if operation not in OPERATIONS:
        raise VectorNativeCadError(f"{path}.op {operation!r} is unsupported")
    params = command["params"]
    if not isinstance(params, dict):
        raise VectorNativeCadError(f"{path}.params must be an object")
    allowed_params = {
        "sketch.bspline": {"curve", "plane"},
        "curve.bspline3d": {"curve"},
        "feature.loft": {"sections", "solid", "ruled", "closed", "continuity"},
        "feature.box": {"length_mm", "width_mm", "height_mm", "origin_mm"},
        "feature.guided_loft": {
            "sections", "guides", "guide_tolerance_mm", "solid", "ruled", "continuity",
        },
        "feature.rib": {"start_mm", "end_mm", "thickness_mm", "height_mm",
                        "draft_deg", "root_fillet_mm"},
        "feature.surface_fill": {"boundaries", "continuity"},
        "feature.thicken": {"base", "thickness_mm", "mode", "remove_faces"},
        "feature.boolean": {"base", "tools", "operation"},
        "feature.compound": {"members"},
        "feature.split": {"base", "plane_origin_mm", "plane_normal", "keep"},
        "drawing.project": {"base", "views", "output_dir"},
    }[operation]
    unknown_params = sorted(set(params) - allowed_params)
    if unknown_params:
        raise VectorNativeCadError(f"{path}.params has unknown parameters {unknown_params}")
    for boolean_name in ("solid", "ruled", "closed"):
        if boolean_name in params and type(params[boolean_name]) is not bool:
            raise VectorNativeCadError(f"{path}.params.{boolean_name} must be a boolean")
    provenance = _string_list(command["provenance"], f"{path}.provenance")

    def required(*names: str) -> None:
        missing = [name for name in names if name not in params]
        if missing:
            raise VectorNativeCadError(f"{path} missing parameters {missing}")

    if operation in {"sketch.bspline", "curve.bspline3d"}:
        required("curve")
        curve = _curve_definition(params["curve"], f"{path}.params.curve", envelope=envelope)
        if operation == "sketch.bspline":
            plane = params.get("plane", "XY")
            if plane not in {"XY", "XZ", "YZ"}:
                raise VectorNativeCadError(f"{path}.params.plane must be XY, XZ or YZ")
            constant_axis = {"XY": 2, "XZ": 1, "YZ": 0}[plane]
            if any(abs(point[constant_axis]
                       - curve["control_points"][0][constant_axis]) > 1.0e-7
                   for point in curve["control_points"]):
                raise VectorNativeCadError(
                    f"{path} sketch.bspline control points must be planar in {plane}")
    elif operation == "feature.box":
        required("length_mm", "width_mm", "height_mm")
        dims = [params["length_mm"], params["width_mm"], params["height_mm"]]
        for index, value in enumerate(dims):
            if type(value) not in (int, float) or float(value) <= 0.0:
                raise VectorNativeCadError(f"{path}.params dim {index} must be positive")
            if float(value) > MAX_COORDINATE_MM:
                raise VectorNativeCadError(f"{path}.params dim {index} exceeds limit")
        origin = params.get("origin_mm", [0.0, 0.0, 0.0])
        if (not isinstance(origin, list) or len(origin) != 3
                or any(type(v) not in (int, float) or not math.isfinite(float(v))
                       for v in origin)):
            raise VectorNativeCadError(f"{path}.params.origin_mm must be 3 finite numbers")
        if envelope:
            corners = [[origin[0] + (0 if i % 2 else dims[0]),
                        origin[1] + (0 if i in (0, 3) else dims[1]),
                        origin[2] + (0 if i < 4 else dims[2])] for i in range(8)]
            for ci, corner in enumerate(corners):
                for axis in range(3):
                    if not (envelope["min_mm"][axis] - 1.0e-7 <= corner[axis]
                            <= envelope["max_mm"][axis] + 1.0e-7):
                        raise VectorNativeCadError(
                            f"{path}.params corner {ci} outside locked envelope")
    elif operation == "feature.rib":
        required("start_mm", "end_mm", "thickness_mm", "height_mm")
        start = _vector(params["start_mm"], f"{path}.params.start_mm")
        end = _vector(params["end_mm"], f"{path}.params.end_mm")
        if math.dist(start, end) <= 1.0e-7:
            raise VectorNativeCadError(f"{path}.params rib path must have positive length")
        _positive(params["thickness_mm"], f"{path}.params.thickness_mm")
        _positive(params["height_mm"], f"{path}.params.height_mm")
        draft = _nonnegative(params.get("draft_deg", 0.0), f"{path}.params.draft_deg")
        if draft > 45.0:
            raise VectorNativeCadError(f"{path}.params.draft_deg must be <= 45 degrees")
        _nonnegative(params.get("root_fillet_mm", 0.0), f"{path}.params.root_fillet_mm")
        if envelope:
            for point_name, point in (("start", start), ("end", end)):
                if any(point[axis] < envelope["min_mm"][axis] - 1.0e-7 or
                       point[axis] > envelope["max_mm"][axis] + 1.0e-7
                       for axis in range(3)):
                    raise VectorNativeCadError(
                        f"{path}.params.{point_name}_mm is outside locked envelope")
    elif operation == "feature.loft":
        required("sections")
        sections = params["sections"]
        if not isinstance(sections, list) or len(sections) < 2:
            raise VectorNativeCadError(f"{path}.params.sections needs at least two ordered sections")
        for section in sections:
            _reference(section, f"{path}.params.sections", previous)
        if len(set(sections)) != len(sections):
            raise VectorNativeCadError(f"{path}.params.sections must be ordered and unique")
        if "continuity" in params:
            _continuity(params["continuity"], f"{path}.params.continuity")
    elif operation == "feature.guided_loft":
        required("sections", "guides")
        sections, guides = params["sections"], params["guides"]
        if not isinstance(sections, list) or len(sections) < 2:
            raise VectorNativeCadError(f"{path}.params.sections needs at least two ordered sections")
        if not isinstance(guides, list) or not guides:
            raise VectorNativeCadError(f"{path}.params.guides needs at least one guide")
        for section in sections:
            _reference(section, f"{path}.params.sections", previous)
        for guide in guides:
            _reference(guide, f"{path}.params.guides", previous)
        if len(set(sections)) != len(sections):
            raise VectorNativeCadError(f"{path}.params.sections must be ordered and unique")
        if len(guides) != 1:
            raise VectorNativeCadError(
                f"{path}.params.guides currently supports exactly one connected guide")
        _positive(params.get("guide_tolerance_mm"), f"{path}.params.guide_tolerance_mm")
        _continuity(params.get("continuity"), f"{path}.params.continuity")
    elif operation == "feature.surface_fill":
        required("boundaries")
        boundaries = params["boundaries"]
        if not isinstance(boundaries, list) or not boundaries:
            raise VectorNativeCadError(f"{path}.params.boundaries needs at least one boundary")
        for boundary in boundaries:
            _reference(boundary, f"{path}.params.boundaries", previous)
        _continuity(params.get("continuity"), f"{path}.params.continuity")
    elif operation == "feature.thicken":
        required("base", "thickness_mm", "mode")
        _reference(params["base"], f"{path}.params.base", previous)
        thickness = _positive(params["thickness_mm"], f"{path}.params.thickness_mm")
        if thickness < 0.01:
            raise VectorNativeCadError(f"{path}.params.thickness_mm is below 0.01 mm")
        faces = params.get("remove_faces", [])
        if not isinstance(faces, list) or any(type(item) is not int or item < 1 for item in faces):
            raise VectorNativeCadError(f"{path}.params.remove_faces must contain positive integers")
        mode = params["mode"]
        if mode not in {"remove_faces", "closed_offset_shell"}:
            raise VectorNativeCadError(
                f"{path}.params.mode must be remove_faces or closed_offset_shell")
        if mode == "remove_faces" and not faces:
            raise VectorNativeCadError(
                f"{path}.params.remove_faces must be non-empty for remove_faces mode")
        if mode == "closed_offset_shell" and faces:
            raise VectorNativeCadError(
                f"{path}.params.remove_faces must be empty for closed_offset_shell mode")
    elif operation == "feature.boolean":
        required("base", "tools", "operation")
        _reference(params["base"], f"{path}.params.base", previous)
        tools = params["tools"]
        if not isinstance(tools, list) or not tools:
            raise VectorNativeCadError(f"{path}.params.tools must contain at least one tool")
        for tool in tools:
            _reference(tool, f"{path}.params.tools", previous)
        if len(set(tools)) != len(tools) or params["base"] in tools:
            raise VectorNativeCadError(f"{path}.params.tools must be unique and differ from base")
        if params["operation"] not in {"cut", "common", "fuse"}:
            raise VectorNativeCadError(f"{path}.params.operation must be cut, common or fuse")
    elif operation == "feature.compound":
        required("members")
        members = params["members"]
        if not isinstance(members, list) or not members:
            raise VectorNativeCadError(f"{path}.params.members needs at least one member")
        for member in members:
            _reference(member, f"{path}.params.members", previous)
        if len(set(members)) != len(members):
            raise VectorNativeCadError(f"{path}.params.members must be unique")
    elif operation == "feature.split":
        required("base", "plane_origin_mm", "plane_normal")
        _reference(params["base"], f"{path}.params.base", previous)
        _vector(params["plane_origin_mm"], f"{path}.params.plane_origin_mm")
        normal = _vector(params["plane_normal"], f"{path}.params.plane_normal")
        nonzero = sum(item * item for item in normal) > 1.0e-18
        if not nonzero:
            raise VectorNativeCadError(f"{path}.params.plane_normal must not be zero")
        non_axis = sum(abs(item) > 1.0e-9 for item in normal) != 1
        if non_axis:
            raise VectorNativeCadError(
                f"{path}.params.plane_normal must currently be axis-aligned for deterministic split")
        if params.get("keep", "both") not in {"both", "negative", "positive"}:
            raise VectorNativeCadError(f"{path}.params.keep must be both, negative or positive")
    elif operation == "drawing.project":
        required("base")
        _reference(params["base"], f"{path}.params.base", previous)
        views = params.get("views", ["front", "top", "right"])
        if not isinstance(views, list) or not views or any(
                view not in {"front", "back", "top", "bottom", "right", "left"}
                for view in views):
            raise VectorNativeCadError(f"{path}.params.views contains an unsupported view")
        if "output_dir" in params and (not isinstance(params["output_dir"], str)
                                        or not params["output_dir"].strip()):
            raise VectorNativeCadError(f"{path}.params.output_dir must be a non-empty path")
    return {"id": command_id, "op": operation, "params": dict(params),
            "provenance": provenance}


def validate_vector_program(program: Any) -> dict[str, Any]:
    """Validate a v2 mechanical CAD program and return a normalized copy."""
    if not isinstance(program, dict):
        raise VectorNativeCadError("program must be an object")
    required = {"schema", "program_id", "units", "author", "envelope", "control_datums", "commands", "checks"}
    if set(program) != required:
        raise VectorNativeCadError(
            f"program fields differ; missing={sorted(required - set(program))}, "
            f"unknown={sorted(set(program) - required)}")
    if program["schema"] != MECHANICAL_SCHEMA:
        raise VectorNativeCadError(f"schema must be {MECHANICAL_SCHEMA!r}")
    program_id = _id(program["program_id"], "program_id")
    if program["units"] != "mm":
        raise VectorNativeCadError("v2 mechanical programs must use millimetres")
    if not isinstance(program["author"], str) or not program["author"].strip():
        raise VectorNativeCadError("author must be a non-empty string")
    envelope = _envelope(program["envelope"])
    control_datums = _control_datums(program["control_datums"], envelope)
    commands = program["commands"]
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise VectorNativeCadError(f"commands must contain 1–{MAX_COMMANDS} items")
    normalized, previous = [], set()
    for index, command in enumerate(commands):
        item = _validate_command(command, index, previous, envelope)
        if item["id"] in previous:
            raise VectorNativeCadError(f"duplicate command id {item['id']!r}")
        normalized.append(item)
        previous.add(item["id"])
    checks = _validate_checks(program["checks"], previous)
    return {"schema": MECHANICAL_SCHEMA, "program_id": program_id, "units": "mm",
            "author": program["author"], "envelope": envelope,
            "control_datums": control_datums, "commands": normalized, "checks": checks}


def _validate_checks(value: Any, previous: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise VectorNativeCadError("checks must be an array")
    result = []
    for index, check in enumerate(value):
        path = f"checks[{index}]"
        if not isinstance(check, dict) or set(check) - {"kind", "target", "value"}:
            raise VectorNativeCadError(f"{path} has unsupported fields")
        if check.get("kind") not in {"valid_shape", "watertight", "non_self_intersecting",
                                      "solid_count", "volume", "bbox", "rebuild", "artifact"}:
            raise VectorNativeCadError(f"{path}.kind is unsupported")
        target = _id(check.get("target"), f"{path}.target")
        if target not in previous:
            raise VectorNativeCadError(f"{path}.target references an unknown command")
        result.append(dict(check))
    return result


def validate_surface_design(document: Any) -> dict[str, Any]:
    """Validate the independent station/guide surface-design/2 document."""
    if not isinstance(document, dict):
        raise VectorNativeCadError("surface design must be an object")
    required = {"schema", "design_id", "units", "author", "envelope", "sections",
                "guides", "continuity", "parameters", "provenance"}
    if set(document) != required:
        raise VectorNativeCadError(
            f"surface design fields differ; missing={sorted(required - set(document))}, "
            f"unknown={sorted(set(document) - required)}")
    if document["schema"] != SURFACE_SCHEMA:
        raise VectorNativeCadError(f"schema must be {SURFACE_SCHEMA!r}")
    design_id = _id(document["design_id"], "design_id")
    if document["units"] != "mm":
        raise VectorNativeCadError("surface designs must use millimetres")
    if not isinstance(document["author"], str) or not document["author"].strip():
        raise VectorNativeCadError("author must be a non-empty string")
    envelope = document["envelope"]
    if not isinstance(envelope, dict) or set(envelope) != {"min_mm", "max_mm"}:
        raise VectorNativeCadError("envelope must contain min_mm and max_mm")
    minimum, maximum = _vector(envelope["min_mm"], "envelope.min_mm"), _vector(envelope["max_mm"], "envelope.max_mm")
    if any(minimum[index] >= maximum[index] for index in range(3)):
        raise VectorNativeCadError("envelope min_mm must be less than max_mm")
    normalized_envelope = {"min_mm": minimum, "max_mm": maximum}
    sections = document["sections"]
    if not isinstance(sections, list) or len(sections) < 2:
        raise VectorNativeCadError("sections must contain at least two stations")
    section_ids, normalized_sections = set(), []
    previous_station = None
    for index, section in enumerate(sections):
        path = f"sections[{index}]"
        if not isinstance(section, dict) or set(section) != {"id", "station_mm", "curve"}:
            raise VectorNativeCadError(f"{path} must contain id, station_mm and curve")
        section_id = _id(section["id"], f"{path}.id")
        if section_id in section_ids:
            raise VectorNativeCadError(f"duplicate section id {section_id!r}")
        station = _finite(section["station_mm"], f"{path}.station_mm")
        if previous_station is not None and station <= previous_station:
            raise VectorNativeCadError("sections must be in strictly increasing station order")
        curve = _curve_definition(section["curve"], f"{path}.curve", envelope=normalized_envelope)
        section_ids.add(section_id)
        previous_station = station
        normalized_sections.append({"id": section_id, "station_mm": station, "curve": curve})
    guides = document["guides"]
    if not isinstance(guides, list):
        raise VectorNativeCadError("guides must be an array")
    guide_ids, normalized_guides = set(), []
    for index, guide in enumerate(guides):
        path = f"guides[{index}]"
        if not isinstance(guide, dict) or set(guide) != {"id", "curve"}:
            raise VectorNativeCadError(f"{path} must contain id and curve")
        guide_id = _id(guide["id"], f"{path}.id")
        if guide_id in guide_ids or guide_id in section_ids:
            raise VectorNativeCadError(f"duplicate curve id {guide_id!r}")
        guide_ids.add(guide_id)
        normalized_guides.append({"id": guide_id,
                                 "curve": _curve_definition(guide["curve"], f"{path}.curve",
                                                             envelope=normalized_envelope)})
    continuity = _continuity(document["continuity"], "continuity")
    parameters = document["parameters"]
    if not isinstance(parameters, dict):
        raise VectorNativeCadError("parameters must be an object")
    provenance = _string_list(document["provenance"], "provenance")
    return {"schema": SURFACE_SCHEMA, "design_id": design_id, "units": "mm",
            "author": document["author"], "envelope": normalized_envelope,
            "sections": normalized_sections, "guides": normalized_guides,
            "continuity": continuity, "parameters": dict(parameters),
            "provenance": provenance}


def program_digest(program: Any) -> str:
    return _canonical(validate_vector_program(program))


def surface_digest(document: Any) -> str:
    return _canonical(validate_surface_design(document))


def _freecad():
    import FreeCAD as App
    import Part
    return App, Part


def _safe_name(command_id: str) -> str:
    return "DS_V2_" + re.sub(r"[^A-Za-z0-9_]", "_", command_id)


def _build_curve(curve_definition: dict[str, Any]):
    App, Part = _freecad()
    poles = [App.Vector(*point) for point in curve_definition["control_points"]]
    curve = Part.BSplineCurve()
    curve.buildFromPolesMultsKnots(
        poles, tuple(curve_definition["multiplicities"]),
        tuple(curve_definition["knots"]), curve_definition["periodic"],
        curve_definition["degree"], tuple(curve_definition["weights"]), True)
    return curve, curve.toShape()


def _add_property(obj, property_type: str, name: str, group: str, value: Any = None):
    if name not in obj.PropertiesList:
        obj.addProperty(property_type, name, group)
    if value is not None:
        setattr(obj, name, value)


def _wire(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise VectorNativeCadError(f"{obj.Name} has no usable shape")
    if shape.ShapeType == "Wire":
        return shape
    if shape.ShapeType == "Edge":
        return Part.Wire([shape])
    if shape.ShapeType == "Face" and shape.OuterWire:
        return shape.OuterWire
    if getattr(shape, "Wires", None):
        return shape.Wires[0]
    raise VectorNativeCadError(f"{obj.Name} is not a curve/wire profile")


def _edge_curve(shape):
    edges = list(getattr(shape, "Edges", []))
    if len(edges) != 1:
        raise VectorNativeCadError("supported guide must contain exactly one B-spline edge")
    edge = edges[0]
    curve = getattr(edge, "Curve", None)
    if curve is None:
        raise VectorNativeCadError("supported guide has no OCCT curve")
    return edge, curve


def _vector_from(value):
    return (float(value.x), float(value.y), float(value.z))


def _distance_between_shapes(first, second) -> float:
    try:
        return float(first.distToShape(second)[0])
    except Exception as exc:
        raise VectorNativeCadError(f"unable to measure guide/section intersection: {exc}") from exc


def _guided_loft_shape(section_objects, guide_objects, params):
    """Build the supported one-guide OCCT loft subset.

    The guide must be one open edge, touch the first and last section within
    the declared tolerance, and progress monotonically along one axis.  An
    intermediate profile is translated by the actual guide midpoint and is
    passed to OCCT's thru-sections builder; therefore guide edits alter the
    resulting B-Rep instead of merely being metadata.
    """
    App, Part = _freecad()
    if len(guide_objects) != 1:
        raise VectorNativeCadError("guided loft supports exactly one guide")
    sections = [_wire(obj) for obj in section_objects]
    guide_shape = guide_objects[0].Shape
    edge, curve = _edge_curve(guide_shape)
    if edge.isClosed():
        raise VectorNativeCadError("guided loft guide must be open")
    tolerance = float(params["guide_tolerance_mm"])
    first_point = curve.value(edge.FirstParameter)
    last_point = curve.value(edge.LastParameter)
    section_distances = [_distance_between_shapes(guide_shape, section) for section in sections]
    if any(distance > tolerance for distance in section_distances):
        raise VectorNativeCadError(
            "guide does not intersect every ordered section within guide_tolerance_mm")
    delta = last_point - first_point
    axis = max(range(3), key=lambda index: abs((delta.x, delta.y, delta.z)[index]))
    if abs((delta.x, delta.y, delta.z)[axis]) <= tolerance:
        raise VectorNativeCadError("guide has no usable longitudinal direction")
    samples = []
    for index in range(9):
        parameter = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * index / 8.0
        point = curve.value(parameter)
        samples.append((point.x, point.y, point.z)[axis])
    direction = 1.0 if samples[-1] > samples[0] else -1.0
    if any((samples[index + 1] - samples[index]) * direction < -tolerance
           for index in range(len(samples) - 1)):
        raise VectorNativeCadError("guide is not monotonic along its longitudinal axis")
    # Preserve every ordered section and add intermediate guide stations. A
    # midpoint-only construction is insufficient: two guides can share a
    # midpoint while diverging elsewhere. The extra stations make the entire
    # supported guide curve part of the OCCT loft input.
    profile_wires = []
    for section_index, section in enumerate(sections):
        parameter = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * (
            section_index / max(len(sections) - 1, 1))
        guide_point = curve.value(parameter)
        anchor = section.Vertexes[0].Point
        translated = section.copy()
        translated.translate(guide_point - anchor)
        if translated.ShapeType != "Wire":
            translated = Part.Wire(list(translated.Edges))
        profile_wires.append((parameter, translated))

    # Add quarter stations (and more for larger networks), using the nearest
    # section profile. They are construction stations, while all original
    # sections remain in the ordered list above.
    station_count = max(0, min(8, len(sections) + 1))
    for station_index in range(1, station_count):
        fraction = station_index / station_count
        if any(abs(fraction - index / max(len(sections) - 1, 1)) < 1.0e-9
               for index in range(len(sections))):
            continue
        parameter = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * fraction
        guide_point = curve.value(parameter)
        nearest_index = min(range(len(sections)),
                            key=lambda index: abs(index / max(len(sections) - 1, 1) - fraction))
        source = sections[nearest_index]
        translated = source.copy()
        translated.translate(guide_point - source.Vertexes[0].Point)
        if translated.ShapeType != "Wire":
            translated = Part.Wire(list(translated.Edges))
        profile_wires.append((parameter, translated))
    profile_wires.sort(key=lambda item: item[0])
    loft_wires = [wire for _, wire in profile_wires]
    midpoint_parameter = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * 0.5
    midpoint = curve.value(midpoint_parameter)
    # Use OCCT's spine-driven pipe builder rather than relabelling an ordinary
    # thru-sections loft.  The ordered profile wires are consumed as stations
    # along the actual guide edge, so guide curvature and section changes both
    # affect the authoritative B-Rep.
    spine = Part.Wire([edge])
    try:
        result = spine.makePipeShell(
            loft_wires, bool(params.get("solid", False)), False)
    except Exception as exc:
        raise VectorNativeCadError(
            f"guide-constrained OCCT pipe construction failed: {exc}") from exc
    if result.isNull() or not result.isValid():
        raise VectorNativeCadError("guide-constrained loft produced an invalid B-Rep")
    return result, {
        "supported_mode": "one_monotonic_occt_spine_pipe_shell",
        "guide_tolerance_mm": tolerance,
        "section_intersections_mm": section_distances,
        "guide_station_count": len(loft_wires),
        "guide_start_mm": _vector_from(first_point),
        "guide_end_mm": _vector_from(last_point),
        "guide_midpoint_mm": _vector_from(midpoint),
    }


def _shape_quality(shape) -> dict[str, Any]:
    """Return conservative kernel validity evidence for a B-Rep."""
    if shape is None or shape.isNull():
        return {"valid": False, "watertight": False, "self_intersecting": True,
                "solid_count": 0, "check_errors": ["null-shape"]}
    errors = []
    try:
        check = shape.check()
        if check:
            errors = [str(item) for item in check]
        analysis_available = True
    except Exception as exc:
        # Missing kernel analysis is not proof of a clean B-Rep.
        analysis_available = False
        errors = ["kernel-check-unavailable: " + str(exc)]
    solids = list(getattr(shape, "Solids", []))
    closed = bool(solids) and all(bool(solid.isClosed()) for solid in solids)
    valid = bool(shape.isValid()) and not shape.isNull() and not errors and analysis_available
    return {"valid": valid, "watertight": bool(closed),
            "self_intersecting": bool(errors) or not analysis_available,
            "solid_count": len(solids), "check_errors": errors,
            "analysis_available": analysis_available}


def _unit_tangent(curve, parameter):
    try:
        tangent = curve.derivative(parameter, 1)
    except Exception:
        delta = max(abs(float(curve.LastParameter - curve.FirstParameter)) * 1.0e-5, 1.0e-6)
        a = curve.value(max(curve.FirstParameter, parameter - delta))
        b = curve.value(min(curve.LastParameter, parameter + delta))
        tangent = b - a
    length = float(tangent.Length)
    if length <= 1.0e-12:
        raise VectorNativeCadError("continuity measurement encountered a zero tangent")
    return tangent / length


def _face_normal_at(face, point):
    surface = getattr(face, "Surface", None)
    if surface is None:
        raise VectorNativeCadError("result face has no parametric surface")
    parameter = surface.parameter(point)
    normal = face.normalAt(float(parameter[0]), float(parameter[1]))
    length = float(normal.Length)
    if length <= 1.0e-12:
        raise VectorNativeCadError("result face has a zero normal")
    return normal / length


def _face_curvature_at(face, point):
    surface = getattr(face, "Surface", None)
    if surface is None:
        raise VectorNativeCadError("result face has no parametric surface")
    parameter = surface.parameter(point)
    if hasattr(surface, "curvature"):
        value = surface.curvature(float(parameter[0]), float(parameter[1]))
        if hasattr(value, "MaxCurvature"):
            return float(value.MaxCurvature)
        if isinstance(value, (tuple, list)):
            return max(abs(float(item)) for item in value)
        return abs(float(value))
    if hasattr(face, "curvatureAt"):
        return abs(float(face.curvatureAt(float(parameter[0]), float(parameter[1]))))
    raise VectorNativeCadError("result surface does not expose curvature")


def _surface_continuity_evidence(shape, params) -> dict[str, Any]:
    requested = _continuity(params.get("continuity"), "continuity")
    interfaces = []
    faces = list(getattr(shape, "Faces", []))
    for edge in getattr(shape, "Edges", []):
        adjacent = [face for face in faces
                    if any(edge.isSame(candidate) for candidate in face.Edges)]
        if len(adjacent) >= 2:
            interfaces.append((edge, adjacent[:2]))
    if not interfaces:
        # A single surface has no patch boundary to compare. Record that fact
        # explicitly; higher-level surface-network checks must provide an
        # interface when continuity is materially required.
        return {"requested": requested, "max_gap_mm": 0.0,
                "max_normal_angle_deg": 0.0, "max_curvature_delta": 0.0,
                "passed": True, "interfaces": 0,
                "analysis_available": True, "source": "no_shared_result_edges"}
    # OCCT lofts commonly contain intentional profile-cap boundaries (a plane
    # meeting the exterior loft face).  Measure those edges for evidence, but
    # apply G1/G2 patch continuity only to interfaces between two exterior
    # non-planar faces; a cap is a termination, not an exterior patch seam.
    patch_interfaces = []
    ignored_cap_interfaces = 0
    for edge, adjacent in interfaces:
        if any(type(getattr(face, "Surface", None)).__name__ == "Plane"
               for face in adjacent):
            ignored_cap_interfaces += 1
        else:
            patch_interfaces.append((edge, adjacent))
    gaps, angles, tangents, curvature_deltas = [], [], [], []
    patch_angles, patch_tangents, patch_curvatures = [], [], []
    samples = []
    try:
        for edge, adjacent in interfaces:
            adjacent_edges = []
            for face in adjacent:
                matching = next((candidate for candidate in face.Edges
                                 if edge.isSame(candidate)), None)
                if matching is None:
                    raise VectorNativeCadError("shared result edge has no face-owned counterpart")
                adjacent_edges.append(matching)
            is_patch = (edge, adjacent) in patch_interfaces
            for fraction in (0.25, 0.5, 0.75):
                parameter = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * fraction
                point = edge.valueAt(parameter)
                counterpart_points = [
                    candidate.valueAt(candidate.FirstParameter +
                                      (candidate.LastParameter - candidate.FirstParameter) * fraction)
                    for candidate in adjacent_edges
                ]
                direct_gap = float(counterpart_points[0].distanceToPoint(counterpart_points[1]))
                reverse_gap = float(counterpart_points[0].distanceToPoint(
                    adjacent_edges[1].valueAt(adjacent_edges[1].LastParameter -
                                              (adjacent_edges[1].LastParameter -
                                               adjacent_edges[1].FirstParameter) * fraction)))
                gap = min(direct_gap, reverse_gap)
                n_first = _face_normal_at(adjacent[0], point)
                n_second = _face_normal_at(adjacent[1], point)
                normal_dot = max(-1.0, min(1.0, float(n_first.dot(n_second))))
                normal_angle = math.degrees(math.acos(normal_dot))
                tangent_first = adjacent_edges[0].tangentAt(
                    adjacent_edges[0].FirstParameter +
                    (adjacent_edges[0].LastParameter - adjacent_edges[0].FirstParameter) * fraction)
                tangent_second = adjacent_edges[1].tangentAt(
                    adjacent_edges[1].FirstParameter +
                    (adjacent_edges[1].LastParameter - adjacent_edges[1].FirstParameter) * fraction)
                tangent_dot = max(-1.0, min(1.0, abs(float(tangent_first.dot(tangent_second)))))
                tangent_angle = math.degrees(math.acos(tangent_dot))
                gaps.append(gap)
                angles.append(normal_angle)
                tangents.append(tangent_angle)
                sample = {"fraction": fraction, "gap_mm": gap,
                          "normal_angle_deg": normal_angle,
                          "tangent_angle_deg": tangent_angle}
                if is_patch:
                    patch_angles.append(normal_angle)
                    patch_tangents.append(tangent_angle)
                if requested["required"] == "G2" and is_patch:
                    curvature_delta = abs(_face_curvature_at(adjacent[0], point) -
                                          _face_curvature_at(adjacent[1], point))
                    curvature_deltas.append(curvature_delta)
                    sample["curvature_delta"] = curvature_delta
                samples.append(sample)
        max_gap = max(gaps, default=0.0)
        max_angle = max(angles, default=0.0)
        max_tangent = max(tangents, default=0.0)
        max_curvature = max(curvature_deltas, default=0.0)
        max_patch_angle = max(patch_angles, default=0.0)
        max_patch_tangent = max(patch_tangents, default=0.0)
        max_patch_curvature = max(curvature_deltas, default=0.0)
        passed = (max_gap <= 0.05 and
                  (requested["required"] == "G0" or
                   max_patch_angle <= requested["max_normal_angle_deg"] and
                   max_patch_tangent <= requested["max_normal_angle_deg"]) and
                  (requested["required"] != "G2" or
                   max_patch_curvature <= requested["max_curvature_delta"]))
        return {"requested": requested, "max_gap_mm": max_gap,
                "max_normal_angle_deg": max_angle,
                "max_tangent_angle_deg": max_tangent,
                "max_curvature_delta": max_curvature,
                "passed": bool(passed), "interfaces": len(interfaces),
                "patch_interfaces": len(patch_interfaces),
                "ignored_cap_interfaces": ignored_cap_interfaces,
                "max_patch_normal_angle_deg": max_patch_angle,
                "max_patch_tangent_angle_deg": max_patch_tangent,
                "max_patch_curvature_delta": max_patch_curvature,
                "samples": samples,
                "analysis_available": True, "source": "result_surface_edges"}
    except Exception as exc:
        return {"requested": requested, "max_gap_mm": float("inf"),
                "max_normal_angle_deg": float("inf"),
                "max_curvature_delta": float("inf"), "passed": False,
                "interfaces": len(interfaces), "analysis_available": False,
                "source": "result_surface_edges", "error": str(exc)}


def _continuity_evidence(source_objects, params, result_shape=None) -> dict[str, Any]:
    if result_shape is not None:
        surface_result = _surface_continuity_evidence(result_shape, params)
        if surface_result["interfaces"] > 0:
            return surface_result
    requested = _continuity(params.get("continuity"), "continuity")
    gaps, angles, curvatures = [], [], []
    # Section stations are not adjacent patch boundaries; their spatial
    # separation is intentional.  Measure each closed profile's own seam
    # (the boundary continuity that OCCT receives), and use adjacent endpoint
    # checks only for open profile pairs.
    pairs = []
    for source in source_objects:
        wire = _wire(source)
        edge, curve = _edge_curve(wire)
        if edge.isClosed() or bool(getattr(curve, "isPeriodic", lambda: False)()):
            pairs.append((curve, curve, curve.LastParameter, curve.FirstParameter))
    if not pairs:
        for index in range(len(source_objects) - 1):
            first = _wire(source_objects[index])
            second = _wire(source_objects[index + 1])
            _, first_curve = _edge_curve(first)
            _, second_curve = _edge_curve(second)
            pairs.append((first_curve, second_curve,
                          first_curve.LastParameter, second_curve.FirstParameter))
    for first_curve, second_curve, first_parameter, second_parameter in pairs:
        p_first = first_curve.value(first_parameter)
        p_second = second_curve.value(second_parameter)
        gaps.append(float(p_first.distanceToPoint(p_second)))
        t_first = _unit_tangent(first_curve, first_parameter)
        t_second = _unit_tangent(second_curve, second_parameter)
        dot = max(-1.0, min(1.0, float(t_first.dot(t_second))))
        angles.append(math.degrees(math.acos(dot)))
        curvature_gap = 0.0
        if requested["required"] == "G2":
            try:
                c_first = first_curve.curvature(first_parameter)
                c_second = second_curve.curvature(second_parameter)
                curvature_gap = float(c_first.distanceToPoint(c_second))
            except Exception:
                curvature_gap = float("inf")
        curvatures.append(curvature_gap)
    max_gap = max(gaps, default=0.0)
    max_angle = max(angles, default=0.0)
    max_curvature = max(curvatures, default=0.0)
    passed = max_gap <= 0.05 and (
        requested["required"] == "G0" or max_angle <= requested["max_normal_angle_deg"]
    ) and (requested["required"] != "G2" or
           max_curvature <= requested["max_curvature_delta"])
    return {"requested": requested, "max_gap_mm": max_gap,
            "max_normal_angle_deg": max_angle,
            "max_curvature_delta": max_curvature,
            "passed": bool(passed), "interfaces": len(gaps),
            "analysis_available": True, "source": "input_curve_seams"}


def _axis(normal):
    values = [abs(item) for item in normal]
    index = max(range(3), key=values.__getitem__)
    if values[index] <= 1.0e-9 or any(values[i] > 1.0e-9 for i in range(3) if i != index):
        raise VectorNativeCadError("split plane must be axis-aligned")
    return index, 1.0 if normal[index] >= 0.0 else -1.0


def _split_shape(shape, origin, normal, keep):
    App, Part = _freecad()
    index, sign = _axis(normal)
    box = shape.BoundBox
    lows = [box.XMin - 1.0, box.YMin - 1.0, box.ZMin - 1.0]
    highs = [box.XMax + 1.0, box.YMax + 1.0, box.ZMax + 1.0]
    coordinate = origin[index]
    if not lows[index] < coordinate < highs[index]:
        raise VectorNativeCadError("split plane does not intersect the base bounding box")
    negative_high = list(highs)
    negative_high[index] = coordinate
    positive_low = list(lows)
    positive_low[index] = coordinate
    negative = Part.makeBox(negative_high[0] - lows[0], negative_high[1] - lows[1],
                            negative_high[2] - lows[2], App.Vector(*lows))
    positive = Part.makeBox(highs[0] - positive_low[0], highs[1] - positive_low[1],
                            highs[2] - positive_low[2], App.Vector(*positive_low))
    pieces = []
    if keep in {"both", "negative"}:
        common = shape.common(negative)
        if not common.isNull():
            pieces.append(common)
    if keep in {"both", "positive"}:
        common = shape.common(positive)
        if not common.isNull():
            pieces.append(common)
    if not pieces:
        raise VectorNativeCadError("split produced no non-empty B-Rep")
    return pieces[0] if len(pieces) == 1 else Part.makeCompound(pieces)


def _projection_payload(shape, views: list[str], digest: str) -> tuple[str, str]:
    # Project actual B-Rep edges.  This intentionally remains a small
    # deterministic HLR-compatible projection (edge ownership and source
    # digest are retained); the authoritative geometry is always the shape.
    mappings = {
        "front": (0, 2, 1.0, 1.0), "back": (0, 2, -1.0, 1.0),
        "top": (0, 1, 1.0, -1.0), "bottom": (0, 1, 1.0, 1.0),
        "right": (1, 2, -1.0, 1.0), "left": (1, 2, 1.0, 1.0),
    }
    def point_tuple(point):
        return (float(point.x), float(point.y), float(point.z))
    projected = []
    # FreeCAD's topological edge iteration order is not guaranteed to survive
    # a save/reload or a fresh kernel process.  Assign projection IDs from a
    # canonical edge signature so SVG metadata cannot encode that incidental
    # ordering.
    ordered_edges = sorted(
        ((_edge_signature(edge), edge) for edge in getattr(shape, "Edges", [])),
        key=lambda item: json.dumps(item[0], sort_keys=True, separators=(",", ":")))
    for view in views:
        axis_u, axis_v, sign_u, sign_v = mappings[view]
        for edge_index, (_, edge) in enumerate(ordered_edges):
            try:
                points = list(edge.discretize(Number=24))
            except Exception:
                points = [edge.Vertexes[0].Point, edge.Vertexes[-1].Point]
            if len(points) < 2:
                continue
            poly = [(point_tuple(point)[axis_u] * sign_u,
                     point_tuple(point)[axis_v] * sign_v) for point in points]
            projected.append((view, edge_index, poly))
    projected.sort(key=lambda item: (item[0],
                                     json.dumps([[round(u, 9), round(v, 9)] for u, v in item[2]],
                                                separators=(",", ":"))))
    all_points = [point for _, _, poly in projected for point in poly]
    if not all_points:
        raise VectorNativeCadError("drawing projection produced no B-Rep edges")
    min_u = min(point[0] for point in all_points)
    max_u = max(point[0] for point in all_points)
    min_v = min(point[1] for point in all_points)
    max_v = max(point[1] for point in all_points)
    width, height = max(max_u - min_u, 1.0), max(max_v - min_v, 1.0)
    margin = 0.05 * max(width, height)
    width += 2 * margin
    height += 2 * margin
    svg_parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%.6f %.6f %.6f %.6f">' %
        (min_u - margin, min_v - margin, width, height),
        '<metadata>source_brep_digest=%s views=%s edge_count=%d</metadata>' %
        (digest, ",".join(views), len(projected)),
    ]
    dxf = ["0", "SECTION", "2", "HEADER", "9", "$COMMENT", "1",
           f"DesignStudio source_brep_digest={digest} views={','.join(views)} edges={len(projected)}",
           "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for view, edge_index, poly in projected:
        coordinates = " ".join("%.6f,%.6f" % (u, v) for u, v in poly)
        svg_parts.append('<polyline data-view="%s" data-edge-index="%d" points="%s" '
                         'fill="none" stroke="black"/>' % (view, edge_index, coordinates))
        dxf.extend(["0", "LWPOLYLINE", "8", view.upper(), "90", str(len(poly))])
        for u, v in poly:
            dxf.extend(["10", "%.6f" % u, "20", "%.6f" % v])
    svg_parts.append('</svg>')
    dxf.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(svg_parts) + "\n", "\n".join(dxf) + "\n"


def _write_artifact(path: Path, content: str, transactions: list[dict[str, Any]]) -> None:
    """Atomically write an output while retaining its exact prior state.

    A failed CAD program must not destroy an unrelated drawing that happened
    to use the same output path.  The transaction record is intentionally kept
    in memory and restored in reverse order by ``execute_vector_program``.
    """
    existed = path.exists()
    record: dict[str, Any] = {
        "path": path,
        "existed": existed,
        "bytes": path.read_bytes() if existed else None,
        "mode": stat.S_IMODE(path.stat().st_mode) if existed else None,
    }
    transactions.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if existed and record["mode"] is not None:
            os.chmod(temporary, record["mode"])
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _linked_sources(obj):
    result = []
    index = 1
    while hasattr(obj, f"Source{index}"):
        source = getattr(obj, f"Source{index}")
        if source is None:
            break
        result.append(source)
        index += 1
    return result


def _rebuild_feature_shape(obj, operation: str, params: dict[str, Any]):
    """Rebuild an editable feature from its persisted typed parameters."""
    App, Part = _freecad()
    if operation in {"curve.bspline3d", "sketch.bspline"}:
        _, edge = _build_curve(params["curve"])
        return Part.Wire([edge])
    if operation == "feature.box":
        ox, oy, oz = params.get("origin_mm", [0.0, 0.0, 0.0])
        return Part.makeBox(float(params["length_mm"]), float(params["width_mm"]),
                            float(params["height_mm"]), App.Vector(ox, oy, oz))
    if operation == "feature.rib":
        from .molded_features import rib_shape
        return rib_shape(Part, App, {"id": getattr(obj, "CommandId", "rib"),
                                     "path_mm": [params["start_mm"], params["end_mm"]],
                                     "thickness_mm": params["thickness_mm"],
                                     "height_mm": params["height_mm"],
                                     "draft_deg": params.get("draft_deg", 0.0),
                                     "root_fillet_mm": params.get("root_fillet_mm", 0.0)})
    sources = _linked_sources(obj)
    if operation in {"feature.loft", "feature.guided_loft"}:
        if operation == "feature.guided_loft":
            guides = []
            index = 1
            while hasattr(obj, f"Guide{index}"):
                guide = getattr(obj, f"Guide{index}")
                if guide is None:
                    break
                guides.append(guide)
                index += 1
            return _guided_loft_shape(sources, guides, params)[0]
        return Part.makeLoft([_wire(source) for source in sources],
                             bool(params.get("solid", False)),
                             bool(params.get("ruled", False)),
                             # OCCT semantics: "closed" sews the LAST section
                             # back to the FIRST (torus/ring lofts).  Program
                             # profiles are individually closed loops by their
                             # own curve definitions; passing a program's
                             # per-profile `closed` flag here produced
                             # degenerate zero-volume solids on two-section
                             # lofts.  Ring behaviour must be requested with
                             # the explicit `section_ring` parameter.
                             bool(params.get("section_ring", False)))
    if operation == "feature.surface_fill":
        wires = [_wire(source) for source in sources]
        face = Part.Face(wires[0])
        for hole in wires[1:]:
            face = face.cut(Part.Face(hole))
        return face
    if operation == "feature.thicken":
        base = sources[0].Shape
        faces = params.get("remove_faces", [])
        if params["mode"] == "closed_offset_shell":
            return base.makeOffsetShape(params["thickness_mm"], 0.01).cut(base)
        removed = [base.Faces[index - 1] for index in faces]
        return base.makeThickness(removed, params["thickness_mm"], 0.01)
    if operation == "feature.boolean":
        shape = sources[0].Shape.copy()
        for tool in sources[1:]:
            if params["operation"] == "cut":
                shape = shape.cut(tool.Shape)
            elif params["operation"] == "common":
                shape = shape.common(tool.Shape)
            else:
                shape = shape.fuse(tool.Shape)
        return shape.removeSplitter()
    if operation == "feature.compound":
        return Part.makeCompound([source.Shape for source in sources])
    if operation == "feature.split":
        return _split_shape(sources[0].Shape, params["plane_origin_mm"],
                            params["plane_normal"], params.get("keep", "both"))
    if operation == "drawing.project":
        return sources[0].Shape.copy()
    raise VectorNativeCadError(f"unsupported editable operation {operation!r}")


class VectorFeatureProxy:
    """FreeCAD FeaturePython proxy preserving a typed, recomputable feature."""
    def __init__(self, operation: str):
        self.operation = operation

    def execute(self, obj):
        try:
            params = json.loads(obj.ParametersJSON)
            if self.operation in {"curve.bspline3d", "sketch.bspline"} and hasattr(obj, "CurveDefinitionJSON"):
                params["curve"] = json.loads(obj.CurveDefinitionJSON)
            obj.Shape = _rebuild_feature_shape(obj, self.operation, params)
            if self.operation in {"feature.loft", "feature.guided_loft", "feature.surface_fill"}:
                evidence = _continuity_evidence(_linked_sources(obj), params, obj.Shape)
                if not evidence["passed"]:
                    raise VectorNativeCadError("continuity requirement failed during recompute")
                _add_property(obj, "App::PropertyString", "ContinuityResultJSON", "Verification",
                              json.dumps(evidence, sort_keys=True, separators=(",", ":")))
            if self.operation == "drawing.project":
                source = _linked_sources(obj)[0]
                digest = _shape_digest(source.Shape)
                svg, dxf = _projection_payload(source.Shape, params.get("views", ["front", "top", "right"]), digest)
                obj.SVGText, obj.DXFText = svg, dxf
                obj.SourceBRepDigest = digest
                if hasattr(obj, "SVGPath") and obj.SVGPath:
                    svg_path = Path(obj.SVGPath)
                    svg_path.parent.mkdir(parents=True, exist_ok=True)
                    svg_path.write_text(svg, encoding="utf-8")
                if hasattr(obj, "DXFPath") and obj.DXFPath:
                    dxf_path = Path(obj.DXFPath)
                    dxf_path.parent.mkdir(parents=True, exist_ok=True)
                    dxf_path.write_text(dxf, encoding="utf-8")
            obj.GeometryDigest = _shape_digest(obj.Shape)
            obj.BuildStatus = "valid"
        except Exception as exc:
            obj.BuildStatus = "invalid: " + str(exc)
            raise

    def onChanged(self, obj, property_name):
        return None


def _create_object(document, command: dict[str, Any], objects: dict[str, Any],
                   file_transactions: list[dict[str, Any]] | None = None):
    App, Part = _freecad()
    command_id, operation, params = command["id"], command["op"], command["params"]
    name = _safe_name(command_id)
    if document.getObject(name) is not None:
        raise VectorNativeCadError(f"document already contains command object {command_id!r}")
    source_ids = []
    if operation in {"feature.loft", "feature.guided_loft"}:
        source_ids = list(params["sections"])
    elif operation == "feature.surface_fill":
        source_ids = list(params["boundaries"])
    elif operation in {"feature.thicken", "feature.split", "drawing.project"}:
        source_ids = [params["base"]]
    elif operation == "feature.boolean":
        source_ids = [params["base"], *params["tools"]]
    elif operation == "feature.compound":
        source_ids = list(params["members"])
    sources = [objects[reference] for reference in source_ids]
    if operation == "sketch.bspline":
        try:
            import Sketcher  # noqa: F401
            curve, _ = _build_curve(params["curve"])
            obj = document.addObject("Sketcher::SketchObject", name)
            obj.Label = f"{command_id} · sketch.bspline"
            obj.addGeometry(curve, False)
            _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
            _add_property(obj, "App::PropertyString", "Operation", "DesignStudio", operation)
            _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                          json.dumps(params, sort_keys=True, separators=(",", ":")))
            _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                          command["provenance"])
            _add_property(obj, "App::PropertyString", "CurveDefinitionJSON", "B-Spline",
                          json.dumps(params["curve"], sort_keys=True, separators=(",", ":")))
            _add_property(obj, "App::PropertyString", "CurveClassification", "B-Spline",
                          params["curve"]["classification"])
            _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
            _add_property(obj, "App::PropertyString", "GeometryDigest", "Verification",
                          _canonical(params["curve"]))
            objects[command_id] = obj
            return obj
        except Exception as exc:
            raise VectorNativeCadError(f"editable B-spline sketch creation failed: {exc}") from exc
    obj = document.addObject("PartDesign::FeaturePython", name)
    obj.Label = f"{command_id} · {operation}"
    _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
    _add_property(obj, "App::PropertyString", "Operation", "DesignStudio", operation)
    _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                  json.dumps(params, sort_keys=True, separators=(",", ":")))
    _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                  command["provenance"])
    _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "pending recompute")
    _add_property(obj, "App::PropertyString", "GeometryDigest", "Verification", "")
    _add_property(obj, "App::PropertyString", "ContinuityJSON", "Verification",
                  json.dumps(params.get("continuity", {}), sort_keys=True))
    for index, source in enumerate(sources):
        _add_property(obj, "App::PropertyLink", f"Source{index + 1}", "References", source)

    if operation in {"sketch.bspline", "curve.bspline3d"}:
        curve, shape = _build_curve(params["curve"])
        obj.Shape = Part.Wire([shape])
        _add_property(obj, "App::PropertyString", "CurveDefinitionJSON", "B-Spline",
                      json.dumps(params["curve"], sort_keys=True, separators=(",", ":")))
        _add_property(obj, "App::PropertyInteger", "Degree", "B-Spline", params["curve"]["degree"])
        _add_property(obj, "App::PropertyStringList", "Classification", "B-Spline",
                      [params["curve"]["classification"]])
        _add_property(obj, "App::PropertyBool", "Closed", "B-Spline", params["curve"]["closed"])
        _add_property(obj, "App::PropertyBool", "Periodic", "B-Spline", params["curve"]["periodic"])
    elif operation in {"feature.loft", "feature.guided_loft"}:
        wires = [_wire(source) for source in sources]
        guide_receipt = None
        if operation == "feature.guided_loft":
            guide_objects = [objects[reference] for reference in params["guides"]]
            shape, guide_receipt = _guided_loft_shape(sources, guide_objects, params)
        else:
            shape = Part.makeLoft(wires, bool(params.get("solid", False)),
                                  bool(params.get("ruled", False)),
                                  bool(params.get("closed", False)))
        if shape.isNull() or not shape.isValid():
            raise VectorNativeCadError(f"{operation} produced an invalid B-Rep")
        obj.Shape = shape
        if operation == "feature.guided_loft":
            guide_objects = [objects[reference] for reference in params["guides"]]
            for index, guide in enumerate(guide_objects):
                _add_property(obj, "App::PropertyLink", f"Guide{index + 1}", "References", guide)
            _add_property(obj, "App::PropertyString", "GuideValidationJSON", "Verification",
                          json.dumps(guide_receipt, sort_keys=True, separators=(",", ":")))
        continuity = _continuity_evidence(sources, params, shape)
        if not continuity["passed"]:
            raise VectorNativeCadError(f"{operation} continuity requirement failed")
        _add_property(obj, "App::PropertyString", "ContinuityResultJSON", "Verification",
                      json.dumps(continuity, sort_keys=True, separators=(",", ":")))
    elif operation == "feature.box":
        ox, oy, oz = params.get("origin_mm", [0.0, 0.0, 0.0])
        shape = Part.makeBox(float(params["length_mm"]), float(params["width_mm"]),
                             float(params["height_mm"]), App.Vector(ox, oy, oz))
        if shape.isNull() or not shape.isValid():
            raise VectorNativeCadError("feature.box produced an invalid B-Rep")
        obj.Shape = shape
    elif operation == "feature.rib":
        from .molded_features import rib_shape
        try:
            obj.Shape = rib_shape(Part, App, {
                "id": command_id,
                "path_mm": [params["start_mm"], params["end_mm"]],
                "thickness_mm": params["thickness_mm"],
                "height_mm": params["height_mm"],
                "draft_deg": params.get("draft_deg", 0.0),
                "root_fillet_mm": params.get("root_fillet_mm", 0.0),
            })
        except Exception as exc:
            raise VectorNativeCadError(f"rib construction failed: {exc}") from exc
        _add_property(obj, "App::PropertyLength", "ThicknessMM", "Manufacturing",
                      params["thickness_mm"])
        _add_property(obj, "App::PropertyLength", "HeightMM", "Manufacturing",
                      params["height_mm"])
        _add_property(obj, "App::PropertyAngle", "DraftDeg", "Manufacturing",
                      params.get("draft_deg", 0.0))
        _add_property(obj, "App::PropertyLength", "RootFilletMM", "Manufacturing",
                      params.get("root_fillet_mm", 0.0))
        _add_property(obj, "App::PropertyString", "DesignStudioRole", "DesignStudio",
                      "manufactured_rib")
    elif operation == "feature.surface_fill":
        wires = [_wire(source) for source in sources]
        if any(not bool(wire.isClosed()) for wire in wires):
            raise VectorNativeCadError("surface_fill boundaries must be closed wires")
        try:
            face = Part.Face(wires[0])
            for hole in wires[1:]:
                face = face.cut(Part.Face(hole))
            obj.Shape = face
        except Exception as exc:
            raise VectorNativeCadError(f"surface_fill failed to sew boundaries: {exc}") from exc
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise VectorNativeCadError("surface_fill produced an invalid face")
        _add_property(obj, "App::PropertyInteger", "BoundaryCount", "Surface", len(wires))
        continuity = _continuity_evidence(sources, params, obj.Shape)
        if not continuity["passed"]:
            raise VectorNativeCadError("surface_fill continuity requirement failed")
        _add_property(obj, "App::PropertyString", "ContinuityResultJSON", "Verification",
                      json.dumps(continuity, sort_keys=True, separators=(",", ":")))
    elif operation == "feature.thicken":
        base = sources[0].Shape
        if base.isNull() or not base.isValid():
            raise VectorNativeCadError("thicken base is invalid")
        faces = params.get("remove_faces", [])
        if params["mode"] == "closed_offset_shell":
            try:
                outer = base.makeOffsetShape(params["thickness_mm"], 0.01)
                obj.Shape = outer.cut(base)
                _add_property(obj, "App::PropertyString", "ThicknessMode", "Manufacturing",
                              "closed_offset_shell")
            except Exception as exc:
                raise VectorNativeCadError(f"closed offset shell failed: {exc}") from exc
        else:
            try:
                removed = [base.Faces[index - 1] for index in faces]
            except IndexError as exc:
                raise VectorNativeCadError("remove_faces contains an out-of-range face index") from exc
            try:
                obj.Shape = base.makeThickness(removed, params["thickness_mm"], 0.01)
                _add_property(obj, "App::PropertyString", "ThicknessMode", "Manufacturing",
                              "remove_faces")
            except Exception as exc:
                raise VectorNativeCadError(f"remove-face thickening failed: {exc}") from exc
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise VectorNativeCadError("thickening produced an invalid B-Rep")
        _add_property(obj, "App::PropertyLength", "ThicknessMM", "Manufacturing",
                      params["thickness_mm"])
    elif operation == "feature.boolean":
        shape = sources[0].Shape.copy()
        try:
            for tool in sources[1:]:
                if params["operation"] == "cut":
                    shape = shape.cut(tool.Shape)
                elif params["operation"] == "common":
                    shape = shape.common(tool.Shape)
                else:
                    shape = shape.fuse(tool.Shape)
            obj.Shape = shape.removeSplitter()
        except Exception as exc:
            raise VectorNativeCadError(
                f"boolean {params['operation']} for {command_id!r} failed: {exc}") from exc
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise VectorNativeCadError("boolean operation produced an invalid B-Rep")
        _add_property(obj, "App::PropertyString", "BooleanOperation", "Construction",
                      params["operation"])
    elif operation == "feature.compound":
        try:
            obj.Shape = Part.makeCompound([source.Shape for source in sources])
        except Exception as exc:
            raise VectorNativeCadError(
                f"compound for {command_id!r} failed: {exc}") from exc
        if obj.Shape.isNull() or not obj.Shape.isValid() or not obj.Shape.Solids:
            raise VectorNativeCadError("compound operation produced an invalid B-Rep")
        _add_property(obj, "App::PropertyInteger", "MemberCount", "Construction",
                      len(sources))
    elif operation == "feature.split":
        obj.Shape = _split_shape(sources[0].Shape, params["plane_origin_mm"],
                                 params["plane_normal"], params.get("keep", "both"))
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise VectorNativeCadError("split produced an invalid B-Rep")
    else:  # drawing.project
        source_shape = sources[0].Shape
        digest = _shape_digest(source_shape)
        svg, dxf = _projection_payload(source_shape, params.get("views", ["front", "top", "right"]), digest)
        output_dir = params.get("output_dir")
        if output_dir:
            # Persist absolute artifact paths so a recompute after FCStd reload
            # cannot redirect output through the process's current directory.
            target = Path(output_dir).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            stem = target / command_id
            svg_path, dxf_path = stem.with_suffix(".svg"), stem.with_suffix(".dxf")
            if file_transactions is None:
                svg_path.write_text(svg, encoding="utf-8")
                dxf_path.write_text(dxf, encoding="utf-8")
            else:
                _write_artifact(svg_path, svg, file_transactions)
                _write_artifact(dxf_path, dxf, file_transactions)
            _add_property(obj, "App::PropertyString", "SVGPath", "Drawing", str(stem.with_suffix(".svg")))
            _add_property(obj, "App::PropertyString", "DXFPath", "Drawing", str(stem.with_suffix(".dxf")))
        _add_property(obj, "App::PropertyString", "SVGText", "Drawing", svg)
        _add_property(obj, "App::PropertyString", "DXFText", "Drawing", dxf)
        obj.Shape = source_shape.copy()
        _add_property(obj, "App::PropertyString", "SourceBRepDigest", "Drawing", digest)
        _add_property(obj, "App::PropertyString", "ProjectionDigest", "Drawing",
                      _canonical({"source": digest, "views": params.get("views", [])}))
    # Keep every non-sketch feature editable and recomputable after FCStd
    # reload.  The persisted JSON and typed links are the source of truth.
    obj.Proxy = VectorFeatureProxy(operation)
    obj.GeometryDigest = _shape_digest(obj.Shape)
    obj.BuildStatus = "valid"
    objects[command_id] = obj
    return obj


def _round_point(point) -> list[float]:
    return [round(float(point.x), 9), round(float(point.y), 9), round(float(point.z), 9)]


def _bbox_signature(box) -> list[float]:
    return [round(float(value), 9) for value in
            (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)]


def _edge_signature(edge) -> dict[str, Any]:
    curve = getattr(edge, "Curve", None)
    samples = []
    if curve is not None:
        first, last = float(edge.FirstParameter), float(edge.LastParameter)
        for fraction in (0.0, .125, .25, .5, .75, .875, 1.0):
            try:
                samples.append(_round_point(curve.value(first + (last - first) * fraction)))
            except Exception:
                samples.append([])
    payload = {"curve_type": type(curve).__name__ if curve is not None else "unknown",
               "samples": samples, "bbox": _bbox_signature(edge.BoundBox),
               "closed": bool(edge.isClosed())}
    if curve is not None:
        for method_name in ("getPoles", "getKnots", "getMultiplicities"):
            method = getattr(curve, method_name, None)
            if method is not None:
                try:
                    value = method()
                    if method_name == "getPoles":
                        value = [_round_point(item) for item in value]
                    else:
                        value = [round(float(item), 9) for item in value]
                    payload[method_name] = value
                except Exception:
                    payload[method_name] = "unavailable"
    return payload


def _face_signature(face) -> dict[str, Any]:
    surface = getattr(face, "Surface", None)
    box = face.BoundBox
    center = type("Point", (), {"x": (box.XMin + box.XMax) / 2.0,
                                 "y": (box.YMin + box.YMax) / 2.0,
                                 "z": (box.ZMin + box.ZMax) / 2.0})()
    payload = {"surface_type": type(surface).__name__ if surface is not None else "unknown",
               "bbox": _bbox_signature(box), "center": _round_point(center),
               "edge_count": len(face.Edges)}
    try:
        payload["normal"] = _round_point(face.normalAt(*surface.parameter(center)))
    except Exception:
        payload["normal"] = "unavailable"
    return payload


def _shape_geometry_signature(shape) -> dict[str, Any]:
    box = shape.BoundBox
    vertices = sorted(_round_point(vertex.Point) for vertex in getattr(shape, "Vertexes", []))
    edges = sorted((_edge_signature(edge) for edge in getattr(shape, "Edges", [])),
                   key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    faces = sorted((_face_signature(face) for face in getattr(shape, "Faces", [])),
                   key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return {"shape_type": shape.ShapeType, "solid_count": len(shape.Solids),
            "volume": round(float(shape.Volume), 9), "bbox": _bbox_signature(box),
            "vertices": vertices, "edges": edges, "faces": faces}


def _shape_digest(shape) -> str:
    return _canonical(_shape_geometry_signature(shape))


def _check_shape(obj, kind: str) -> tuple[bool, Any]:
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return False, None
    if kind == "valid_shape":
        quality = _shape_quality(shape)
        return bool(quality["valid"]), quality
    if kind in {"watertight", "non_self_intersecting"}:
        quality = _shape_quality(shape)
        passed = quality["watertight"] if kind == "watertight" else not quality["self_intersecting"]
        return bool(passed), quality
    if kind == "solid_count":
        return len(shape.Solids) > 0, len(shape.Solids)
    if kind == "volume":
        return float(shape.Volume) > 0.0, float(shape.Volume)
    if kind == "bbox":
        box = shape.BoundBox
        value = [float(box.XLength), float(box.YLength), float(box.ZLength)]
        return all(item > 0.0 for item in value), value
    return True, "recomputed"


def execute_vector_program(document, program: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically execute a v2 mechanical CAD program."""
    normalized = validate_vector_program(program)
    existing = {obj.Name for obj in getattr(document, "Objects", [])}
    created: list[Any] = []
    file_transactions: list[dict[str, Any]] = []
    try:
        App, _ = _freecad()
        controller_name = _safe_name(normalized["program_id"])
        if document.getObject(controller_name) is not None:
            raise VectorNativeCadError(f"program {normalized['program_id']!r} already exists")
        controller = document.addObject("App::Part", controller_name)
        controller.Label = f"Vector CAD Program · {normalized['program_id']}"
        _add_property(controller, "App::PropertyString", "ProgramId", "DesignStudio", normalized["program_id"])
        _add_property(controller, "App::PropertyString", "Schema", "DesignStudio", MECHANICAL_SCHEMA)
        _add_property(controller, "App::PropertyString", "Author", "DesignStudio", normalized["author"])
        _add_property(controller, "App::PropertyString", "ProgramDigest", "DesignStudio", _canonical(normalized))
        _add_property(controller, "App::PropertyString", "EnvelopeJSON", "DesignStudio",
                      json.dumps(normalized["envelope"], sort_keys=True, separators=(",", ":")))
        _add_property(controller, "App::PropertyString", "ControlDatumsJSON", "DesignStudio",
                      json.dumps(normalized["control_datums"], sort_keys=True, separators=(",", ":")))
        _add_property(controller, "App::PropertyStringList", "CommandIds", "DesignStudio",
                      [command["id"] for command in normalized["commands"]])
        _add_property(controller, "App::PropertyString", "BuildStatus", "DesignStudio", "pending recompute")
        created.append(controller)
        objects: dict[str, Any] = {}
        for command in normalized["commands"]:
            obj = _create_object(document, command, objects, file_transactions)
            created.append(obj)
            controller.addObject(obj)
        document.recompute()
        checks = []
        for check in normalized["checks"]:
            target = objects[check["target"]]
            if check["kind"] == "rebuild":
                passed, value = not any("error" in str(item).lower() for item in target.State), list(target.State)
            elif check["kind"] == "artifact":
                passed, value = True, "derived"
            else:
                passed, value = _check_shape(target, check["kind"])
                if passed and check["kind"] == "bbox" and "value" in check:
                    expected = [float(item) for item in check["value"]]
                    passed = (len(expected) == 3 and
                              all(abs(actual - wanted) <= 1.0e-6
                                  for actual, wanted in zip(value, expected)))
            checks.append({"kind": check["kind"], "target": check["target"],
                           "passed": passed, "value": value})
        failed = [item for item in checks if not item["passed"]]
        if failed:
            raise VectorNativeCadError("v2 CAD checks failed: " + ", ".join(item["target"] for item in failed))
        controller.BuildStatus = "valid"
        _add_property(controller, "App::PropertyString", "ChecksResultJSON", "Verification",
                      json.dumps(checks, sort_keys=True, separators=(",", ":")))
        return {"program_id": normalized["program_id"], "program_digest": controller.ProgramDigest,
                "controller": controller.Name, "objects": [obj.Name for obj in created],
                "checks": checks, "solid_valid": all(item["passed"] for item in checks
                                                        if item["kind"] == "valid_shape")}
    except Exception:
        introduced = []
        for candidate in list(getattr(document, "Objects", [])):
            try:
                if candidate.Name not in existing:
                    introduced.append(candidate)
            except Exception:
                pass
        for obj in reversed(introduced):
            try:
                if document.getObject(obj.Name) is not None:
                    document.removeObject(obj.Name)
            except Exception:
                pass
        for transaction in reversed(file_transactions):
            path = transaction["path"]
            try:
                if transaction["existed"]:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(transaction["bytes"])
                    if transaction["mode"] is not None:
                        os.chmod(path, transaction["mode"])
                elif path.exists():
                    path.unlink()
            except OSError:
                pass
        document.recompute()
        raise


def execute_surface_design(document, design: dict[str, Any]) -> dict[str, Any]:
    """Create editable station and guide B-spline objects from surface-design/2."""
    normalized = validate_surface_design(design)
    existing = {obj.Name for obj in getattr(document, "Objects", [])}
    created = []
    try:
        controller = document.addObject("App::Part", _safe_name(normalized["design_id"]))
        controller.Label = f"Surface Design · {normalized['design_id']}"
        _add_property(controller, "App::PropertyString", "DesignId", "DesignStudio", normalized["design_id"])
        _add_property(controller, "App::PropertyString", "Schema", "DesignStudio", SURFACE_SCHEMA)
        _add_property(controller, "App::PropertyString", "EnvelopeJSON", "DesignStudio",
                      json.dumps(normalized["envelope"], sort_keys=True))
        _add_property(controller, "App::PropertyString", "ContinuityJSON", "DesignStudio",
                      json.dumps(normalized["continuity"], sort_keys=True))
        _add_property(controller, "App::PropertyString", "DesignDigest", "DesignStudio", _canonical(normalized))
        created.append(controller)
        for section in normalized["sections"]:
            curve, shape = _build_curve(section["curve"])
            obj = document.addObject("PartDesign::FeaturePython", _safe_name(section["id"]))
            obj.Label = f"Section {section['id']}"
            obj.Shape = _freecad()[1].Wire([shape])
            _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", section["id"])
            _add_property(obj, "App::PropertyString", "Operation", "DesignStudio", "curve.bspline3d")
            _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                          json.dumps({"curve": section["curve"]}, sort_keys=True,
                                     separators=(",", ":")))
            _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                          normalized["provenance"])
            _add_property(obj, "App::PropertyString", "CurveDefinitionJSON", "B-Spline",
                          json.dumps(section["curve"], sort_keys=True, separators=(",", ":")))
            _add_property(obj, "App::PropertyString", "CurveClassification", "B-Spline",
                          section["curve"]["classification"])
            _add_property(obj, "App::PropertyLength", "StationMM", "Surface", section["station_mm"])
            _add_property(obj, "App::PropertyString", "GeometryDigest", "Verification", _shape_digest(obj.Shape))
            _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
            obj.Proxy = VectorFeatureProxy("curve.bspline3d")
            controller.addObject(obj)
            created.append(obj)
        for guide in normalized["guides"]:
            curve, shape = _build_curve(guide["curve"])
            obj = document.addObject("PartDesign::FeaturePython", _safe_name(guide["id"]))
            obj.Label = f"Guide {guide['id']}"
            obj.Shape = _freecad()[1].Wire([shape])
            _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", guide["id"])
            _add_property(obj, "App::PropertyString", "Operation", "DesignStudio", "curve.bspline3d")
            _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                          json.dumps({"curve": guide["curve"]}, sort_keys=True,
                                     separators=(",", ":")))
            _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                          normalized["provenance"])
            _add_property(obj, "App::PropertyString", "CurveDefinitionJSON", "B-Spline",
                          json.dumps(guide["curve"], sort_keys=True, separators=(",", ":")))
            _add_property(obj, "App::PropertyString", "CurveClassification", "B-Spline",
                          guide["curve"]["classification"])
            _add_property(obj, "App::PropertyString", "GeometryDigest", "Verification", _shape_digest(obj.Shape))
            _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
            obj.Proxy = VectorFeatureProxy("curve.bspline3d")
            controller.addObject(obj)
            created.append(obj)
        document.recompute()
        return {"design_id": normalized["design_id"], "design_digest": controller.DesignDigest,
                "controller": controller.Name, "objects": [obj.Name for obj in created]}
    except Exception:
        introduced = []
        for candidate in list(getattr(document, "Objects", [])):
            try:
                if candidate.Name not in existing:
                    introduced.append(candidate)
            except Exception:
                pass
        for obj in reversed(introduced):
            try:
                if document.getObject(obj.Name) is not None:
                    document.removeObject(obj.Name)
            except Exception:
                pass
        document.recompute()
        raise
