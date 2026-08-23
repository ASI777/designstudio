"""Typed, deterministic mechanical CAD programs for the DesignStudio workbench.

The command vocabulary is intentionally inspired by the semantic feature layer
of commercial CAD systems, not by their private UI command IDs.  A model may
emit this JSON program, but only this trusted host module turns it into
FreeCAD shapes.  FreeCAD remains the geometry authority and every generated
object keeps the command ID and provenance that created it.

The first version covers the robust feature primitives needed for brackets,
mounts, enclosures and simple product hardware.  More specialized operations
such as sheet metal, weldments and assemblies can be added without changing
the program envelope.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


SCHEMA = "design-studio.mechanical-cad-program/1"
SCHEMA_V2 = "design-studio.mechanical-cad-program/2"
MAX_COMMANDS = 512
MAX_PROVENANCE_ITEMS = 32
MAX_COORDINATE_MM = 1_000_000.0

SUPPORTED_OPERATIONS = (
    "part.box",
    "part.cylinder",
    "part.sphere",
    "part.ellipsoid",
    "part.cone",
    "sketch.create",
    "sketch.line",
    "sketch.circle",
    "sketch.rectangle",
    "sketch.constraint.add",
    "feature.extrude",
    "feature.revolve",
    "feature.cut",
    "feature.fuse",
    "feature.hole",
    "feature.fillet",
    "feature.chamfer",
    "pattern.linear",
    "pattern.circular",
    "assembly.create",
    "assembly.component",
    "assembly.mate",
    "assembly.check_clearance",
    "assembly.check_interference",
    "tolerance.stack.create",
    "tolerance.stack.item",
    "tolerance.stack.check",
)
V2_SUPPORTED_OPERATIONS = (
    "sketch.bspline", "curve.bspline3d", "feature.loft", "feature.guided_loft",
    "feature.surface_fill", "feature.thicken", "feature.split", "drawing.project",
)


class MechanicalCadProgramError(ValueError):
    """Raised when an untrusted CAD program is invalid or cannot be built."""


def _finite(value: Any, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise MechanicalCadProgramError(f"{path} must be a finite number")
    result = float(value)
    if abs(result) > MAX_COORDINATE_MM:
        raise MechanicalCadProgramError(f"{path} exceeds the ±1,000,000 mm limit")
    return result


def _positive(value: Any, path: str) -> float:
    result = _finite(value, path)
    if result <= 0.0:
        raise MechanicalCadProgramError(f"{path} must be greater than zero")
    return result


def _nonnegative(value: Any, path: str) -> float:
    result = _finite(value, path)
    if result < 0.0:
        raise MechanicalCadProgramError(f"{path} must not be negative")
    return result


def _vector(value: Any, path: str, *, nonzero: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise MechanicalCadProgramError(f"{path} must contain three coordinates")
    result = [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if nonzero and sum(item * item for item in result) <= 1.0e-18:
        raise MechanicalCadProgramError(f"{path} must not be the zero vector")
    return result


def _point2(value: Any, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise MechanicalCadProgramError(f"{path} must contain two coordinates")
    return [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value):
        raise MechanicalCadProgramError(
            f"{path} must match [A-Za-z][A-Za-z0-9_.-]{{0,127}}")
    return value


def _reference(params: dict[str, Any], keys: tuple[str, ...], path: str) -> str:
    for key in keys:
        if key in params:
            return _id(params[key], f"{path}.{key}")
    raise MechanicalCadProgramError(f"{path} requires one of {keys}")


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_program(program: Any) -> dict[str, Any]:
    """Validate and return a normalized copy of a mechanical CAD program."""
    if isinstance(program, dict) and program.get("schema") == SCHEMA_V2:
        from .vector_native_cad import validate_vector_program
        return validate_vector_program(program)
    if not isinstance(program, dict):
        raise MechanicalCadProgramError("program must be an object")
    required = {"schema", "program_id", "units", "author", "commands", "checks"}
    if set(program) != required:
        raise MechanicalCadProgramError(
            f"program fields differ; missing={sorted(required - set(program))}, "
            f"unknown={sorted(set(program) - required)}")
    if program["schema"] != SCHEMA:
        raise MechanicalCadProgramError(f"schema must be {SCHEMA!r}")
    program_id = _id(program["program_id"], "program_id")
    if program["units"] != "mm":
        raise MechanicalCadProgramError("mechanical CAD programs must use millimetres")
    if not isinstance(program["author"], str) or not program["author"].strip():
        raise MechanicalCadProgramError("author must be a non-empty string")
    commands = program["commands"]
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise MechanicalCadProgramError(f"commands must contain 1–{MAX_COMMANDS} items")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, command in enumerate(commands):
        path = f"commands[{index}]"
        if not isinstance(command, dict) or set(command) != {"id", "op", "params", "provenance"}:
            raise MechanicalCadProgramError(f"{path} must contain id, op, params and provenance")
        command_id = _id(command["id"], f"{path}.id")
        if command_id in ids:
            raise MechanicalCadProgramError(f"duplicate command id {command_id!r}")
        operation = command["op"]
        if operation not in SUPPORTED_OPERATIONS:
            raise MechanicalCadProgramError(
                f"{path}.op {operation!r} is unsupported; choose from {SUPPORTED_OPERATIONS}")
        params = command["params"]
        if not isinstance(params, dict):
            raise MechanicalCadProgramError(f"{path}.params must be an object")
        provenance = command["provenance"]
        if not isinstance(provenance, list) or not 1 <= len(provenance) <= MAX_PROVENANCE_ITEMS:
            raise MechanicalCadProgramError(
                f"{path}.provenance must contain 1–{MAX_PROVENANCE_ITEMS} strings")
        if any(not isinstance(item, str) or not item.strip() for item in provenance):
            raise MechanicalCadProgramError(f"{path}.provenance contains an invalid item")
        normalized_command = {"id": command_id, "op": operation,
                              "params": dict(params), "provenance": list(provenance)}
        _validate_command(normalized_command, index, ids)
        normalized.append(normalized_command)
        ids.add(command_id)

    checks = program["checks"]
    if not isinstance(checks, list) or len(checks) > MAX_COMMANDS:
        raise MechanicalCadProgramError(f"checks must contain at most {MAX_COMMANDS} items")
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != {"kind", "target"}:
            raise MechanicalCadProgramError(f"checks[{index}] must contain kind and target")
        if check["kind"] not in {
            "valid_shape", "bbox", "volume", "rebuild", "solid_count", "sketch_constraints",
            "tolerance_stack"
        }:
            raise MechanicalCadProgramError(f"checks[{index}].kind is unsupported")
        _id(check["target"], f"checks[{index}].target")
    object_names = {_safe_name(program_id)}
    for command in normalized:
        object_name = _safe_name(command["id"])
        if object_name in object_names:
            raise MechanicalCadProgramError(
                f"program and command IDs must produce unique FreeCAD object names: {object_name}")
        object_names.add(object_name)
    return {"schema": SCHEMA, "program_id": program_id, "units": "mm",
            "author": program["author"], "commands": normalized,
            "checks": [dict(check) for check in checks]}


def _validate_command(command: dict[str, Any], index: int, previous: set[str]) -> None:
    path = f"commands[{index}]"
    op = command["op"]
    params = command["params"]

    def required(*names: str) -> None:
        missing = [name for name in names if name not in params]
        if missing:
            raise MechanicalCadProgramError(f"{path} missing parameters {missing}")

    if op == "part.box":
        required("length_mm", "width_mm", "height_mm")
        for name in ("length_mm", "width_mm", "height_mm"):
            _positive(params[name], f"{path}.params.{name}")
        origin = params.get("origin_mm", [0.0, 0.0, 0.0])
        _vector(origin, f"{path}.params.origin_mm")
        basis_names = ("basis_x", "basis_y", "basis_z")
        supplied_basis = [name in params for name in basis_names]
        if any(supplied_basis) and not all(supplied_basis):
            raise MechanicalCadProgramError(
                f"{path}.params oriented box requires basis_x, basis_y and basis_z")
        if all(supplied_basis):
            basis = [
                _vector(params[name], f"{path}.params.{name}", nonzero=True)
                for name in basis_names
            ]
            norms = [math.sqrt(sum(component * component for component in vector))
                     for vector in basis]
            if any(abs(norm - 1.0) > 1.0e-8 for norm in norms):
                raise MechanicalCadProgramError(
                    f"{path}.params box basis vectors must be unit length")
            if any(abs(sum(basis[first][axis] * basis[second][axis]
                           for axis in range(3))) > 1.0e-8
                   for first, second in ((0, 1), (0, 2), (1, 2))):
                raise MechanicalCadProgramError(
                    f"{path}.params box basis vectors must be orthogonal")
            cross_xy = [
                basis[0][1] * basis[1][2] - basis[0][2] * basis[1][1],
                basis[0][2] * basis[1][0] - basis[0][0] * basis[1][2],
                basis[0][0] * basis[1][1] - basis[0][1] * basis[1][0],
            ]
            handedness = sum(cross_xy[axis] * basis[2][axis] for axis in range(3))
            if handedness < 1.0 - 1.0e-8:
                raise MechanicalCadProgramError(
                    f"{path}.params box basis must be right-handed")
    elif op == "part.cylinder":
        required("radius_mm", "height_mm")
        _positive(params["radius_mm"], f"{path}.params.radius_mm")
        _positive(params["height_mm"], f"{path}.params.height_mm")
        _vector(params.get("base_mm", [0.0, 0.0, 0.0]), f"{path}.params.base_mm")
        _vector(params.get("direction", [0.0, 0.0, 1.0]),
                f"{path}.params.direction", nonzero=True)
    elif op == "part.sphere":
        required("radius_mm")
        _positive(params["radius_mm"], f"{path}.params.radius_mm")
        _vector(params.get("center_mm", [0.0, 0.0, 0.0]),
                f"{path}.params.center_mm")
    elif op == "part.ellipsoid":
        required("radial_radius_mm", "axial_radius_mm")
        _positive(params["radial_radius_mm"], f"{path}.params.radial_radius_mm")
        _positive(params["axial_radius_mm"], f"{path}.params.axial_radius_mm")
        _vector(params.get("center_mm", [0.0, 0.0, 0.0]),
                f"{path}.params.center_mm")
        _vector(params.get("axis", [0.0, 0.0, 1.0]),
                f"{path}.params.axis", nonzero=True)
    elif op == "part.cone":
        required("radius1_mm", "radius2_mm", "height_mm")
        _positive(params["radius1_mm"], f"{path}.params.radius1_mm")
        _positive(params["radius2_mm"], f"{path}.params.radius2_mm")
        _positive(params["height_mm"], f"{path}.params.height_mm")
        _vector(params.get("base_mm", [0.0, 0.0, 0.0]), f"{path}.params.base_mm")
        _vector(params.get("direction", [0.0, 0.0, 1.0]),
                f"{path}.params.direction", nonzero=True)
    elif op == "sketch.create":
        plane = params.get("plane", "XY")
        if plane not in {"XY", "XZ", "YZ"}:
            raise MechanicalCadProgramError(f"{path}.params.plane must be XY, XZ or YZ")
        _vector(params.get("origin_mm", [0.0, 0.0, 0.0]),
                f"{path}.params.origin_mm")
    elif op == "sketch.line":
        required("start_mm", "end_mm")
        start = _vector(params["start_mm"], f"{path}.params.start_mm")
        end = _vector(params["end_mm"], f"{path}.params.end_mm")
        if start == end:
            raise MechanicalCadProgramError(f"{path} line endpoints must differ")
        _optional_sketch_reference(params, path, previous)
    elif op == "sketch.circle":
        required("center_mm", "radius_mm")
        _vector(params["center_mm"], f"{path}.params.center_mm")
        _positive(params["radius_mm"], f"{path}.params.radius_mm")
        _vector(params.get("normal", [0.0, 0.0, 1.0]),
                f"{path}.params.normal", nonzero=True)
        _optional_sketch_reference(params, path, previous)
    elif op == "sketch.rectangle":
        required("width_mm", "height_mm")
        _positive(params["width_mm"], f"{path}.params.width_mm")
        _positive(params["height_mm"], f"{path}.params.height_mm")
        _vector(params.get("origin_mm", [0.0, 0.0, 0.0]), f"{path}.params.origin_mm")
        _optional_sketch_reference(params, path, previous)
    elif op == "sketch.constraint.add":
        required("sketch", "kind", "references")
        _require_prior(params["sketch"], path, previous)
        kind = params["kind"]
        if kind not in {
            "coincident", "horizontal", "vertical", "parallel", "perpendicular",
            "equal", "distance", "distance_x", "distance_y", "radius", "diameter", "angle"
        }:
            raise MechanicalCadProgramError(f"{path}.params.kind is unsupported")
        references = params["references"]
        if not isinstance(references, list):
            raise MechanicalCadProgramError(f"{path}.params.references must be an array")
        expected = {
            "coincident": 4, "horizontal": 1, "vertical": 1, "parallel": 2,
            "perpendicular": 2, "equal": 2, "distance": 1, "distance_x": 2,
            "distance_y": 2, "radius": 1, "diameter": 1, "angle": 1,
        }[kind]
        if len(references) != expected or any(type(item) is not int or item < 0
                                               for item in references):
            raise MechanicalCadProgramError(
                f"{path}.params.references must contain {expected} non-negative integer(s)")
        if kind in {"distance", "distance_x", "distance_y", "radius", "diameter"}:
            _positive(params.get("value"), f"{path}.params.value")
        elif kind == "angle":
            angle = _finite(params.get("value"), f"{path}.params.value")
            if abs(angle) <= 0.0 or abs(angle) > 360.0:
                raise MechanicalCadProgramError(f"{path}.params.value must be in (0, 360]")
    elif op == "feature.extrude":
        required("profile", "vector_mm")
        _require_prior(params["profile"], path, previous)
        _vector(params["vector_mm"], f"{path}.params.vector_mm", nonzero=True)
    elif op == "feature.revolve":
        required("profile", "axis_origin_mm", "axis_direction", "angle_deg")
        _require_prior(params["profile"], path, previous)
        _vector(params["axis_origin_mm"], f"{path}.params.axis_origin_mm")
        _vector(params["axis_direction"], f"{path}.params.axis_direction", nonzero=True)
        angle = _finite(params["angle_deg"], f"{path}.params.angle_deg")
        if abs(angle) <= 0.0 or abs(angle) > 360.0:
            raise MechanicalCadProgramError(f"{path}.params.angle_deg must be in (0, 360]")
    elif op in {"feature.cut", "feature.fuse"}:
        required("base", "tool")
        _require_prior(params["base"], path, previous)
        _require_prior(params["tool"], path, previous)
        if params["base"] == params["tool"]:
            raise MechanicalCadProgramError(f"{path} base and tool must differ")
    elif op == "feature.hole":
        required("base", "radius_mm", "center_mm", "depth_mm")
        _require_prior(params["base"], path, previous)
        _positive(params["radius_mm"], f"{path}.params.radius_mm")
        _positive(params["depth_mm"], f"{path}.params.depth_mm")
        _vector(params["center_mm"], f"{path}.params.center_mm")
        _vector(params.get("direction", [0.0, 0.0, 1.0]),
                f"{path}.params.direction", nonzero=True)
    elif op == "feature.fillet":
        required("base", "radius_mm")
        _require_prior(params["base"], path, previous)
        _positive(params["radius_mm"], f"{path}.params.radius_mm")
        _validate_edge_indices(params, path)
    elif op == "feature.chamfer":
        required("base", "size_mm")
        _require_prior(params["base"], path, previous)
        _positive(params["size_mm"], f"{path}.params.size_mm")
        _validate_edge_indices(params, path)
    elif op == "pattern.linear":
        required("source", "count", "spacing_mm", "direction")
        _require_prior(params["source"], path, previous)
        if type(params["count"]) is not int or not 2 <= params["count"] <= 128:
            raise MechanicalCadProgramError(f"{path}.params.count must be an integer from 2 to 128")
        _positive(params["spacing_mm"], f"{path}.params.spacing_mm")
        _vector(params["direction"], f"{path}.params.direction", nonzero=True)
    elif op == "pattern.circular":
        required("source", "count", "angle_deg", "center_mm", "axis")
        _require_prior(params["source"], path, previous)
        if type(params["count"]) is not int or not 2 <= params["count"] <= 128:
            raise MechanicalCadProgramError(f"{path}.params.count must be an integer from 2 to 128")
        angle = _finite(params["angle_deg"], f"{path}.params.angle_deg")
        if abs(angle) <= 0.0 or abs(angle) > 360.0:
            raise MechanicalCadProgramError(f"{path}.params.angle_deg must be in (0, 360]")
        _vector(params["center_mm"], f"{path}.params.center_mm")
        _vector(params["axis"], f"{path}.params.axis", nonzero=True)
    elif op == "assembly.create":
        name = params.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise MechanicalCadProgramError(f"{path}.params.name must be a non-empty string")
    elif op == "assembly.component":
        required("assembly", "source")
        _require_prior(params["assembly"], path, previous)
        _require_prior(params["source"], path, previous)
        _vector(params.get("position_mm", [0.0, 0.0, 0.0]),
                f"{path}.params.position_mm")
        _vector(params.get("rotation_axis", [0.0, 0.0, 1.0]),
                f"{path}.params.rotation_axis", nonzero=True)
        _finite(params.get("rotation_deg", 0.0), f"{path}.params.rotation_deg")
        if "component_ref" in params and (
                not isinstance(params["component_ref"], str) or not params["component_ref"].strip()):
            raise MechanicalCadProgramError(
                f"{path}.params.component_ref must be a non-empty string")
        if "clearance_mm" in params:
            _nonnegative(params["clearance_mm"], f"{path}.params.clearance_mm")
    elif op == "assembly.mate":
        required("assembly", "first", "second", "kind")
        for name in ("assembly", "first", "second"):
            _require_prior(params[name], path, previous)
        if params["first"] == params["second"]:
            raise MechanicalCadProgramError(f"{path} mate references must differ")
        kind = params["kind"]
        if kind not in {
            "fixed", "coincident", "concentric", "distance", "face_coincident",
            "face_distance", "edge_align"
        }:
            raise MechanicalCadProgramError(f"{path}.params.kind is unsupported")
        topology_kinds = {"face_coincident", "face_distance", "edge_align"}
        has_first_reference = "first_reference" in params
        has_second_reference = "second_reference" in params
        if has_first_reference != has_second_reference:
            raise MechanicalCadProgramError(
                f"{path}.params.first_reference and second_reference must be provided together")
        if kind in topology_kinds or has_first_reference:
            required("first_reference", "second_reference")
            first_kind = _validate_topology_reference(
                params["first_reference"], f"{path}.params.first_reference", previous)
            second_kind = _validate_topology_reference(
                params["second_reference"], f"{path}.params.second_reference", previous)
            if kind in {"face_coincident", "face_distance"} and (
                    first_kind != "Face" or second_kind != "Face"):
                raise MechanicalCadProgramError(
                    f"{path} {kind} requires FaceN references")
            if kind == "edge_align" and (first_kind != "Edge" or second_kind != "Edge"):
                raise MechanicalCadProgramError(f"{path} edge_align requires EdgeN references")
        if kind == "distance":
            required("direction", "distance_mm")
            _vector(params["direction"], f"{path}.params.direction", nonzero=True)
            _nonnegative(params["distance_mm"], f"{path}.params.distance_mm")
        elif kind == "face_distance":
            required("distance_mm")
            _nonnegative(params["distance_mm"], f"{path}.params.distance_mm")
        elif kind == "concentric" and "axial_offset_mm" in params:
            _finite(params["axial_offset_mm"], f"{path}.params.axial_offset_mm")
        elif kind == "edge_align" and "offset_mm" in params:
            _vector(params["offset_mm"], f"{path}.params.offset_mm")
        elif "offset_mm" in params:
            _vector(params["offset_mm"], f"{path}.params.offset_mm")
        if "reverse" in params and type(params["reverse"]) is not bool:
            raise MechanicalCadProgramError(f"{path}.params.reverse must be boolean")
    elif op in {"assembly.check_clearance", "assembly.check_interference"}:
        required("assembly", "first", "second")
        for name in ("assembly", "first", "second"):
            _require_prior(params[name], path, previous)
        if params["first"] == params["second"]:
            raise MechanicalCadProgramError(f"{path} check references must differ")
        if op == "assembly.check_clearance":
            required("min_clearance_mm")
            _nonnegative(params["min_clearance_mm"], f"{path}.params.min_clearance_mm")
        else:
            _nonnegative(params.get("tolerance_mm", 1.0e-9),
                         f"{path}.params.tolerance_mm")
    elif op == "tolerance.stack.create":
        required("assembly")
        _require_prior(params["assembly"], path, previous)
        name = params.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise MechanicalCadProgramError(f"{path}.params.name must be a non-empty string")
    elif op == "tolerance.stack.item":
        required("stack", "nominal_mm", "plus_mm", "minus_mm")
        _require_prior(params["stack"], path, previous)
        _finite(params["nominal_mm"], f"{path}.params.nominal_mm")
        _nonnegative(params["plus_mm"], f"{path}.params.plus_mm")
        _nonnegative(params["minus_mm"], f"{path}.params.minus_mm")
        sense = params.get("sense", 1)
        if type(sense) is not int or sense not in {-1, 1}:
            raise MechanicalCadProgramError(f"{path}.params.sense must be 1 or -1")
        if "source" in params and (not isinstance(params["source"], str)
                                    or not params["source"].strip()):
            raise MechanicalCadProgramError(f"{path}.params.source must be a non-empty string")
    elif op == "tolerance.stack.check":
        required("stack", "lower_limit_mm", "upper_limit_mm")
        _require_prior(params["stack"], path, previous)
        lower = _finite(params["lower_limit_mm"], f"{path}.params.lower_limit_mm")
        upper = _finite(params["upper_limit_mm"], f"{path}.params.upper_limit_mm")
        if lower > upper:
            raise MechanicalCadProgramError(f"{path} lower_limit_mm must not exceed upper_limit_mm")
        method = params.get("method", "worst_case")
        if method not in {"worst_case", "rss"}:
            raise MechanicalCadProgramError(f"{path}.params.method must be worst_case or rss")


def _require_prior(value: Any, path: str, previous: set[str]) -> str:
    reference = _id(value, f"{path}.params.reference")
    if reference not in previous:
        raise MechanicalCadProgramError(f"{path} references {reference!r} before it is created")
    return reference


def _optional_sketch_reference(params: dict[str, Any], path: str,
                               previous: set[str]) -> None:
    if "sketch" in params:
        _require_prior(params["sketch"], path, previous)


def _validate_topology_reference(value: Any, path: str, previous: set[str]) -> str:
    if not isinstance(value, dict) or set(value) != {"component", "subelement"}:
        raise MechanicalCadProgramError(
            f"{path} must contain exactly component and subelement")
    _require_prior(value["component"], path, previous)
    subelement = value["subelement"]
    match = re.fullmatch(r"(Face|Edge)([1-9][0-9]*)", subelement) \
        if isinstance(subelement, str) else None
    if match is None:
        raise MechanicalCadProgramError(f"{path}.subelement must be FaceN or EdgeN")
    return match.group(1)


def _validate_edge_indices(params: dict[str, Any], path: str) -> None:
    indices = params.get("edge_indices")
    if indices is None:
        return
    if (not isinstance(indices, list) or not indices or
            any(type(index) is not int or index < 1 for index in indices)):
        raise MechanicalCadProgramError(
            f"{path}.params.edge_indices must contain positive integer edge numbers")


def program_digest(program: dict[str, Any]) -> str:
    """Return the digest of the normalized program."""
    return _canonical_digest(validate_program(program))


def _safe_name(command_id: str) -> str:
    return "DS_CAD_" + re.sub(r"[^A-Za-z0-9_]", "_", command_id)


def _app_part():
    import FreeCAD as App
    import Part
    return App, Part


def _vector3(App, values: list[float]):
    return App.Vector(*values)


def _sketcher_modules():
    App, Part = _app_part()
    import Sketcher
    return App, Part, Sketcher


def _sketch_object(obj: Any) -> bool:
    return getattr(obj, "TypeId", "") == "Sketcher::SketchObject"


def _set_sketch_placement(obj, plane: str, origin: list[float]) -> None:
    App, _, _ = _sketcher_modules()
    rotations = {
        "XY": App.Rotation(),
        "XZ": App.Rotation(App.Vector(1, 0, 0), 90),
        "YZ": App.Rotation(App.Vector(0, 1, 0), -90),
    }
    obj.Placement = App.Placement(App.Vector(0.0, 0.0, origin[2]), rotations[plane])


def _annotate_sketch(obj, command: dict[str, Any]) -> None:
    command_ids = list(getattr(obj, "CommandIds", []))
    provenance = list(getattr(obj, "Provenance", []))
    constraint_ids = list(getattr(obj, "ConstraintCommandIds", []))
    _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio",
                  command["id"])
    _add_property(obj, "App::PropertyString", "Operation", "DesignStudio",
                  command["op"])
    _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                  json.dumps(command["params"], sort_keys=True, separators=(",", ":")))
    if "Provenance" not in obj.PropertiesList:
        _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                      command["provenance"])
    if "CommandIds" not in obj.PropertiesList:
        _add_property(obj, "App::PropertyStringList", "CommandIds", "DesignStudio")
    if "ConstraintCommandIds" not in obj.PropertiesList:
        _add_property(obj, "App::PropertyStringList", "ConstraintCommandIds", "DesignStudio")
    _add_property(obj, "App::PropertyInteger", "GeometryCount", "Verification", 0)
    _add_property(obj, "App::PropertyInteger", "ConstraintCount", "Verification", 0)
    _add_property(obj, "App::PropertyString", "ConstraintStatus", "Verification",
                  "pending recompute")
    _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
    if command["id"] not in command_ids:
        command_ids.append(command["id"])
    obj.CommandIds = command_ids
    for item in command["provenance"]:
        if item not in provenance:
            provenance.append(item)
    obj.Provenance = provenance
    if command["op"] == "sketch.constraint.add":
        if command["id"] not in constraint_ids:
            constraint_ids.append(command["id"])
        obj.ConstraintCommandIds = constraint_ids
    try:
        obj.solve()
        obj.GeometryCount = len(obj.Geometry)
        obj.ConstraintCount = len(obj.Constraints)
        obj.ConstraintStatus = (
            "fully-constrained" if bool(obj.FullyConstrained) else "under-constrained"
        )
    except Exception as exc:
        obj.ConstraintStatus = f"invalid: {exc}"
        obj.BuildStatus = f"invalid: {exc}"


def _add_sketch_geometry(obj, operation: str, params: dict[str, Any]) -> None:
    App, Part, Sketcher = _sketcher_modules()
    if not _sketch_object(obj):
        raise MechanicalCadProgramError("sketch geometry requires a Sketcher::SketchObject")
    if operation == "sketch.line":
        start, end = params["start_mm"], params["end_mm"]
        if abs(start[2] - end[2]) > 1.0e-9:
            raise MechanicalCadProgramError("native sketch lines must be planar")
        obj.addGeometry(
            Part.LineSegment(App.Vector(start[0], start[1], 0),
                             App.Vector(end[0], end[1], 0)),
            bool(params.get("construction", False)),
        )
    elif operation == "sketch.circle":
        center = params["center_mm"]
        obj.addGeometry(
            Part.Circle(App.Vector(center[0], center[1], 0), App.Vector(0, 0, 1),
                        params["radius_mm"]),
            bool(params.get("construction", False)),
        )
        index = len(obj.Geometry) - 1
        obj.addConstraint(Sketcher.Constraint("Radius", index, params["radius_mm"]))
    elif operation == "sketch.rectangle":
        x, y, _ = params.get("origin_mm", [0.0, 0.0, 0.0])
        width, height = params["width_mm"], params["height_mm"]
        points = [(x, y), (x + width, y), (x + width, y + height),
                  (x, y + height)]
        first = len(obj.Geometry)
        for index in range(4):
            start, end = points[index], points[(index + 1) % 4]
            obj.addGeometry(Part.LineSegment(App.Vector(start[0], start[1], 0),
                                             App.Vector(end[0], end[1], 0)), False)
        constraints = [
            Sketcher.Constraint("Horizontal", first),
            Sketcher.Constraint("Vertical", first + 1),
            Sketcher.Constraint("Horizontal", first + 2),
            Sketcher.Constraint("Vertical", first + 3),
            Sketcher.Constraint("Coincident", first, 2, first + 1, 1),
            Sketcher.Constraint("Coincident", first + 1, 2, first + 2, 1),
            Sketcher.Constraint("Coincident", first + 2, 2, first + 3, 1),
            Sketcher.Constraint("Coincident", first + 3, 2, first, 1),
            Sketcher.Constraint("Distance", first, width),
            Sketcher.Constraint("Distance", first + 1, height),
            Sketcher.Constraint("DistanceX", first, 1, x),
            Sketcher.Constraint("DistanceY", first, 1, y),
        ]
        for constraint in constraints:
            obj.addConstraint(constraint)
    elif operation == "sketch.create":
        return
    else:
        raise MechanicalCadProgramError(f"{operation} is not sketch geometry")


def _add_sketch_constraint(obj, params: dict[str, Any]) -> None:
    _, _, Sketcher = _sketcher_modules()
    if not _sketch_object(obj):
        raise MechanicalCadProgramError("constraints require a Sketcher::SketchObject")
    references = params["references"]
    kind = params["kind"]
    if kind in {"horizontal", "vertical"}:
        constraint = Sketcher.Constraint(kind.capitalize(), references[0])
    elif kind == "coincident":
        constraint = Sketcher.Constraint("Coincident", *references)
    elif kind in {"parallel", "perpendicular", "equal"}:
        constraint = Sketcher.Constraint(kind.capitalize(), references[0], references[1])
    elif kind == "distance":
        constraint = Sketcher.Constraint("Distance", references[0], params["value"])
    elif kind in {"distance_x", "distance_y"}:
        constraint_type = {"distance_x": "DistanceX", "distance_y": "DistanceY"}[kind]
        constraint = Sketcher.Constraint(constraint_type, references[0], references[1],
                                         params["value"])
    elif kind in {"radius", "diameter"}:
        constraint = Sketcher.Constraint(kind.capitalize(), references[0], params["value"])
    elif kind == "angle":
        constraint = Sketcher.Constraint("Angle", references[0],
                                         math.radians(params["value"]))
    else:
        raise MechanicalCadProgramError(f"unsupported sketch constraint {kind!r}")
    obj.addConstraint(constraint)


def _assembly_shape(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise MechanicalCadProgramError(
            f"assembly component {getattr(obj, 'Name', '<unknown>')} has no shape")
    return shape


def _shape_distance(first, second) -> float:
    first_shape, second_shape = _assembly_shape(first), _assembly_shape(second)
    try:
        return float(first_shape.distToShape(second_shape)[0])
    except Exception:
        a, b = first_shape.BoundBox, second_shape.BoundBox
        gaps = (max(0.0, a.XMin - b.XMax, b.XMin - a.XMax),
                max(0.0, a.YMin - b.YMax, b.YMin - a.YMax),
                max(0.0, a.ZMin - b.ZMax, b.ZMin - a.ZMax))
        return math.sqrt(sum(gap * gap for gap in gaps))


def _intersection_volume(first, second) -> float:
    try:
        return max(0.0, float(first.Shape.common(second.Shape).Volume))
    except Exception:
        return 0.0


def _unit_runtime(vector, label: str):
    result = vector.copy() if hasattr(vector, "copy") else vector
    if result.Length <= 1.0e-12:
        raise MechanicalCadProgramError(f"{label} must not be the zero vector")
    result.normalize()
    return result


def _element_mid_parameter(element) -> float:
    try:
        return (float(element.FirstParameter) + float(element.LastParameter)) / 2.0
    except Exception:
        return 0.5


def _element_tangent(element):
    parameter = _element_mid_parameter(element)
    try:
        return _unit_runtime(element.tangentAt(parameter), "topology tangent")
    except Exception as exc:
        raise MechanicalCadProgramError(f"topology reference has no usable tangent: {exc}") from exc


def _face_normal(face):
    try:
        parameters = tuple(float(value) for value in face.ParameterRange)
        if len(parameters) == 4:
            u = (parameters[0] + parameters[1]) / 2.0
            v = (parameters[2] + parameters[3]) / 2.0
            return _unit_runtime(face.normalAt(u, v), "face normal")
    except Exception:
        pass
    return _unit_runtime(face.normalAt(0.0, 0.0), "face normal")


def _project_tangent(vector, normal):
    projected = vector - normal * vector.dot(normal)
    if projected.Length <= 1.0e-12:
        return None
    return _unit_runtime(projected, "topology tangent")


def _face_tangent(face, normal):
    App, _ = _app_part()
    for edge in face.Edges:
        tangent = _project_tangent(_element_tangent(edge), normal)
        if tangent is not None:
            return tangent
    candidate = App.Vector(1, 0, 0)
    if abs(candidate.dot(normal)) > 0.9:
        candidate = App.Vector(0, 1, 0)
    return _unit_runtime(_project_tangent(candidate, normal), "face tangent")


def _reference_subshape(component, reference):
    shape = _assembly_shape(component)
    subelement = reference["subelement"]
    kind = "Face" if subelement.startswith("Face") else "Edge"
    index = int(subelement[len(kind):])
    elements = shape.Faces if kind == "Face" else shape.Edges
    if index < 1 or index > len(elements):
        raise MechanicalCadProgramError(
            f"{component.Name}.{subelement} is outside the current topology ({len(elements)} {kind}s)")
    return kind, elements[index - 1]


def _reference_frame(component, reference):
    App, _ = _app_part()
    kind, element = _reference_subshape(component, reference)
    center = element.CenterOfMass
    if kind == "Face":
        normal = _face_normal(element)
        tangent = _face_tangent(element, normal)
        binormal = _unit_runtime(normal.cross(tangent), "face binormal")
        return {"kind": kind, "shape": element, "center": center,
                "normal": normal, "tangent": tangent, "binormal": binormal}

    tangent = _element_tangent(element)
    curve = getattr(element, "Curve", None)
    axis = getattr(curve, "Axis", None)
    if axis is not None and abs(_unit_runtime(axis, "edge axis").dot(tangent)) < 1.0e-6:
        normal = _unit_runtime(axis, "edge axis")
    else:
        candidate = App.Vector(0, 0, 1)
        if abs(candidate.dot(tangent)) > 0.9:
            candidate = App.Vector(0, 1, 0)
        normal = _unit_runtime(tangent.cross(candidate), "edge reference normal")
    binormal = _unit_runtime(normal.cross(tangent), "edge reference binormal")
    return {"kind": kind, "shape": element, "center": center,
            "normal": normal, "tangent": tangent, "binormal": binormal,
            "axis": normal if axis is not None else None}


def _axis_reference(component, reference):
    App, _ = _app_part()
    frame = _reference_frame(component, reference)
    kind, element = frame["kind"], frame["shape"]
    carrier = getattr(element, "Curve", None) if kind == "Edge" else getattr(element, "Surface", None)
    axis = getattr(carrier, "Axis", None)
    if axis is None:
        raise MechanicalCadProgramError(
            f"{component.Name}.{reference['subelement']} is not an analytic circular/cylindrical axis")
    axis = _unit_runtime(axis, "concentric axis")
    tangent = frame["tangent"]
    tangent = _project_tangent(tangent, axis)
    if tangent is None:
        candidate = App.Vector(1, 0, 0)
        if abs(candidate.dot(axis)) > 0.9:
            candidate = App.Vector(0, 1, 0)
        tangent = _unit_runtime(_project_tangent(candidate, axis), "concentric tangent")
    center = getattr(carrier, "Center", frame["center"])
    return {"kind": kind, "shape": element, "center": center, "axis": axis,
            "tangent": tangent, "binormal": _unit_runtime(axis.cross(tangent),
                                                             "concentric binormal")}


def _basis_rotation(basis):
    App, _ = _app_part()
    return App.Rotation(basis[0], basis[1], basis[2], "ZXY")


def _delta_for_bases(source_basis, target_basis):
    source_rotation = _basis_rotation(source_basis)
    target_rotation = _basis_rotation(target_basis)
    return target_rotation.multiply(source_rotation.inverted())


def _set_component_placement(component, base, rotation) -> None:
    App, _ = _app_part()
    current = component.Placement
    same_base = (current.Base - base).Length <= 1.0e-7
    same_rotation = current.Rotation.isSame(rotation, 1.0e-7)
    if not same_base or not same_rotation:
        component.Placement = App.Placement(base, rotation)


def _move_component_by_reference(component, source_center, target_center, delta):
    App, _ = _app_part()
    placement = component.Placement
    new_base = delta.multVec(placement.Base) + target_center - delta.multVec(source_center)
    _set_component_placement(component, new_base, delta.multiply(placement.Rotation))


def _solve_mate(obj, params: dict[str, Any]) -> dict[str, Any]:
    App, _ = _app_part()
    first, second = getattr(obj, "First", None), getattr(obj, "Second", None)
    if first is None or second is None:
        raise MechanicalCadProgramError("assembly mate has no component references")
    kind = params["kind"]
    if kind == "fixed":
        first.Fixed = True
        second.Fixed = True
        return {"kind": kind, "solved": True, "residual_mm": 0.0}
    if bool(getattr(second, "Fixed", False)):
        raise MechanicalCadProgramError(
            f"cannot solve {kind} mate because {params['second']} is fixed")

    if "first_reference" not in params:
        if kind in {"coincident", "concentric"}:
            offset = App.Vector(*params.get("offset_mm", [0.0, 0.0, 0.0]))
            _set_component_placement(second, first.Placement.Base + offset,
                                     first.Placement.Rotation)
        elif kind == "distance":
            direction = App.Vector(*params["direction"])
            direction.normalize()
            _set_component_placement(second,
                first.Placement.Base + direction * params["distance_mm"],
                second.Placement.Rotation)
        return {"kind": kind, "solved": True, "residual_mm": 0.0,
                "mode": "origin"}

    if kind in {"face_coincident", "face_distance"}:
        first_ref = _reference_frame(first, params["first_reference"])
        second_ref = _reference_frame(second, params["second_reference"])
        target_normal = first_ref["normal"] * -1.0
        target_tangent = first_ref["tangent"]
        target_binormal = _unit_runtime(target_normal.cross(target_tangent),
                                        "face mate binormal")
        delta = _delta_for_bases(
            (second_ref["tangent"], second_ref["binormal"], second_ref["normal"]),
            (target_tangent, target_binormal, target_normal),
        )
        distance = params.get("distance_mm", 0.0)
        target_center = first_ref["center"] + first_ref["normal"] * distance
        _move_component_by_reference(second, second_ref["center"], target_center, delta)
        return {"kind": kind, "solved": True, "residual_mm": 0.0,
                "mode": "topology", "first_reference": params["first_reference"],
                "second_reference": params["second_reference"],
                "distance_mm": distance}

    if kind == "edge_align":
        first_ref = _reference_frame(first, params["first_reference"])
        second_ref = _reference_frame(second, params["second_reference"])
        target_tangent = first_ref["tangent"] * (-1.0 if params.get("reverse", False) else 1.0)
        target_normal = first_ref["normal"]
        target_binormal = _unit_runtime(target_normal.cross(target_tangent),
                                        "edge mate binormal")
        delta = _delta_for_bases(
            (second_ref["tangent"], second_ref["binormal"], second_ref["normal"]),
            (target_tangent, target_binormal, target_normal),
        )
        offset = App.Vector(*params.get("offset_mm", [0.0, 0.0, 0.0]))
        _move_component_by_reference(second, second_ref["center"],
                                     first_ref["center"] + offset, delta)
        return {"kind": kind, "solved": True, "residual_mm": 0.0,
                "mode": "topology", "first_reference": params["first_reference"],
                "second_reference": params["second_reference"],
                "reverse": bool(params.get("reverse", False))}

    if kind == "concentric":
        first_ref = _axis_reference(first, params["first_reference"])
        second_ref = _axis_reference(second, params["second_reference"])
        target_axis = first_ref["axis"]
        target_tangent = first_ref["tangent"]
        target_binormal = _unit_runtime(target_axis.cross(target_tangent),
                                        "concentric mate binormal")
        delta = _delta_for_bases(
            (second_ref["tangent"], second_ref["binormal"], second_ref["axis"]),
            (target_tangent, target_binormal, target_axis),
        )
        axial_offset = params.get("axial_offset_mm", 0.0)
        target_center = first_ref["center"] + target_axis * axial_offset
        _move_component_by_reference(second, second_ref["center"], target_center, delta)
        return {"kind": kind, "solved": True, "residual_mm": 0.0,
                "mode": "topology", "first_reference": params["first_reference"],
                "second_reference": params["second_reference"],
                "axial_offset_mm": axial_offset}

    raise MechanicalCadProgramError(f"unsupported mate kind {kind!r}")


class AssemblyMateProxy:
    """Recompute proxy for origin- or topology-referenced assembly mates."""

    def __init__(self, params: dict[str, Any]):
        self.params = dict(params)

    def execute(self, obj) -> None:
        try:
            result = _solve_mate(obj, self.params)
            obj.SolveStatus = "solved"
            obj.ResidualMM = result["residual_mm"]
            obj.ResultJSON = json.dumps(result, sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = "valid"
        except Exception as exc:
            obj.SolveStatus = f"invalid: {exc}"
            obj.ResidualMM = 0.0
            obj.ResultJSON = json.dumps({"solved": False, "error": str(exc)},
                                        sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = f"invalid: {exc}"

    def onChanged(self, obj, property_name: str) -> None:
        return None


def _assembly_check(obj, operation: str, params: dict[str, Any]) -> dict[str, Any]:
    first, second = getattr(obj, "First", None), getattr(obj, "Second", None)
    if first is None or second is None:
        raise MechanicalCadProgramError("assembly check has no component references")
    intersection = _intersection_volume(first, second)
    distance = _shape_distance(first, second)
    if operation == "assembly.check_clearance":
        minimum = params["min_clearance_mm"]
        passed = intersection <= 1.0e-9 and distance + 1.0e-9 >= minimum
        result = {"kind": "clearance", "minimum_mm": minimum}
    else:
        tolerance = params.get("tolerance_mm", 1.0e-9)
        passed = intersection <= tolerance
        result = {"kind": "interference", "tolerance_mm": tolerance}
    result.update({"first": params["first"], "second": params["second"],
                   "distance_mm": round(distance, 9),
                   "intersection_volume_mm3": round(intersection, 9),
                   "passed": passed})
    return result


class AssemblyCheckProxy:
    """Recompute proxy for a measured assembly clearance/interference check."""

    def __init__(self, operation: str, params: dict[str, Any]):
        self.operation = operation
        self.params = dict(params)

    def execute(self, obj) -> None:
        try:
            result = _assembly_check(obj, self.operation, self.params)
            obj.Passed = result["passed"]
            obj.MeasuredDistanceMM = result["distance_mm"]
            obj.IntersectionVolumeMM3 = result["intersection_volume_mm3"]
            obj.ResultJSON = json.dumps(result, sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = "valid" if result["passed"] else "invalid: assembly check failed"
        except Exception as exc:
            obj.Passed = False
            obj.ResultJSON = json.dumps({"passed": False, "error": str(exc)},
                                        sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = f"invalid: {exc}"

    def onChanged(self, obj, property_name: str) -> None:
        return None


def _numeric_property(obj, name: str) -> float:
    value = getattr(obj, name)
    return float(getattr(value, "Value", value))


def _tolerance_stack_result(obj, params: dict[str, Any]) -> dict[str, Any]:
    items = list(getattr(obj, "Items", []))
    if not items:
        raise MechanicalCadProgramError("tolerance stack must contain at least one item")
    nominal = 0.0
    plus_terms, minus_terms = [], []
    item_receipts = []
    for item in items:
        sense = int(getattr(item, "Sense", 1))
        item_nominal = sense * _numeric_property(item, "NominalMM")
        item_plus = _numeric_property(item, "PlusMM")
        item_minus = _numeric_property(item, "MinusMM")
        nominal += item_nominal
        plus_terms.append(item_plus)
        minus_terms.append(item_minus)
        item_receipts.append({
            "id": str(getattr(item, "CommandId", item.Name)),
            "source": str(getattr(item, "Source", "")),
            "sense": sense,
            "nominal_mm": item_nominal,
            "plus_mm": item_plus,
            "minus_mm": item_minus,
        })
    method = params.get("method", "worst_case")
    if method == "rss":
        plus = math.sqrt(sum(value * value for value in plus_terms))
        minus = math.sqrt(sum(value * value for value in minus_terms))
    else:
        plus, minus = sum(plus_terms), sum(minus_terms)
    lower_bound, upper_bound = nominal - minus, nominal + plus
    lower_limit, upper_limit = params["lower_limit_mm"], params["upper_limit_mm"]
    passed = lower_bound >= lower_limit - 1.0e-9 and upper_bound <= upper_limit + 1.0e-9
    return {
        "kind": "tolerance_stack",
        "method": method,
        "nominal_mm": round(nominal, 9),
        "plus_deviation_mm": round(plus, 9),
        "minus_deviation_mm": round(minus, 9),
        "lower_bound_mm": round(lower_bound, 9),
        "upper_bound_mm": round(upper_bound, 9),
        "lower_limit_mm": lower_limit,
        "upper_limit_mm": upper_limit,
        "passed": passed,
        "items": item_receipts,
    }


class ToleranceCheckProxy:
    """Recompute proxy for a worst-case or RSS tolerance stack."""

    def __init__(self, params: dict[str, Any]):
        self.params = dict(params)

    def execute(self, obj) -> None:
        try:
            result = _tolerance_stack_result(obj, self.params)
            obj.Passed = result["passed"]
            obj.NominalMM = result["nominal_mm"]
            obj.LowerBoundMM = result["lower_bound_mm"]
            obj.UpperBoundMM = result["upper_bound_mm"]
            obj.ResultJSON = json.dumps(result, sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = "valid" if result["passed"] else "invalid: tolerance limits failed"
        except Exception as exc:
            obj.Passed = False
            obj.ResultJSON = json.dumps({"passed": False, "error": str(exc)},
                                        sort_keys=True, separators=(",", ":"))
            obj.BuildStatus = f"invalid: {exc}"

    def onChanged(self, obj, property_name: str) -> None:
        return None


def _append_string_property(obj, name: str, group: str, value: str) -> None:
    current = list(getattr(obj, name, [])) if name in obj.PropertiesList else []
    if name not in obj.PropertiesList:
        _add_property(obj, "App::PropertyStringList", name, group)
    if value not in current:
        current.append(value)
    setattr(obj, name, current)


def _create_tolerance_object(document, command: dict[str, Any], objects: dict[str, Any]):
    command_id, operation, params = command["id"], command["op"], command["params"]
    name = _safe_name(command_id)
    if document.getObject(name) is not None:
        raise MechanicalCadProgramError(
            f"document already contains command object {command_id!r}; use a new program revision")
    if operation == "tolerance.stack.create":
        assembly = objects.get(params["assembly"])
        if assembly is None or getattr(assembly, "TypeId", "") != "App::Part":
            raise MechanicalCadProgramError("tolerance stack requires an assembly.create target")
        obj = document.addObject("App::Part", name)
        obj.Label = params.get("name") or f"Tolerance stack · {command_id}"
        _add_property(obj, "App::PropertyString", "StackId", "DesignStudio", command_id)
        _add_property(obj, "App::PropertyString", "AssemblyId", "DesignStudio",
                      params["assembly"])
        _add_property(obj, "App::PropertyStringList", "ItemIds", "Tolerance", [])
        _add_property(obj, "App::PropertyStringList", "CheckIds", "Tolerance", [])
        _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
        assembly.addObject(obj)
    else:
        stack = objects.get(params["stack"])
        if stack is None or getattr(stack, "TypeId", "") != "App::Part":
            raise MechanicalCadProgramError("tolerance command requires a stack.create target")
        if operation == "tolerance.stack.item":
            obj = document.addObject("App::FeaturePython", name)
            obj.Label = params.get("source") or f"Tolerance item · {command_id}"
            _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
            _add_property(obj, "App::PropertyLength", "NominalMM", "Tolerance",
                          params["nominal_mm"])
            _add_property(obj, "App::PropertyLength", "PlusMM", "Tolerance",
                          params["plus_mm"])
            _add_property(obj, "App::PropertyLength", "MinusMM", "Tolerance",
                          params["minus_mm"])
            _add_property(obj, "App::PropertyInteger", "Sense", "Tolerance",
                          params.get("sense", 1))
            _add_property(obj, "App::PropertyString", "Source", "Tolerance",
                          params.get("source", ""))
            _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                          command["provenance"])
            _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
            stack.addObject(obj)
            _append_string_property(stack, "ItemIds", "Tolerance", command_id)
        else:
            items = [item for item in stack.Group
                     if all(name in getattr(item, "PropertiesList", [])
                            for name in ("NominalMM", "PlusMM", "MinusMM"))]
            obj = document.addObject("App::FeaturePython", name)
            obj.Label = f"Tolerance check · {command_id}"
            _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
            _add_property(obj, "App::PropertyString", "StackId", "Tolerance", params["stack"])
            _add_property(obj, "App::PropertyString", "Method", "Tolerance",
                          params.get("method", "worst_case"))
            _add_property(obj, "App::PropertyLinkList", "Items", "Tolerance", items)
            _add_property(obj, "App::PropertyBool", "Passed", "Verification", False)
            _add_property(obj, "App::PropertyLength", "NominalMM", "Verification", 0.0)
            _add_property(obj, "App::PropertyLength", "LowerBoundMM", "Verification", 0.0)
            _add_property(obj, "App::PropertyLength", "UpperBoundMM", "Verification", 0.0)
            _add_property(obj, "App::PropertyString", "ResultJSON", "Verification", "{}")
            _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio",
                          "pending recompute")
            _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                          command["provenance"])
            obj.Proxy = ToleranceCheckProxy(params)
            stack.addObject(obj)
            _append_string_property(stack, "CheckIds", "Tolerance", command_id)
    objects[command_id] = obj
    return obj


def _create_assembly_object(document, command: dict[str, Any], objects: dict[str, Any]):
    App, _ = _app_part()
    command_id, operation, params = command["id"], command["op"], command["params"]
    name = _safe_name(command_id)
    if document.getObject(name) is not None:
        raise MechanicalCadProgramError(
            f"document already contains command object {command_id!r}; use a new program revision")

    if operation == "assembly.create":
        obj = document.addObject("App::Part", name)
        obj.Label = params.get("name") or f"Assembly · {command_id}"
        _add_property(obj, "App::PropertyString", "AssemblyId", "DesignStudio", command_id)
        _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
        _add_property(obj, "App::PropertyStringList", "ComponentIds", "DesignStudio")
        _add_property(obj, "App::PropertyStringList", "MateIds", "DesignStudio")
        _add_property(obj, "App::PropertyStringList", "CheckIds", "DesignStudio")
        objects[command_id] = obj
        return obj

    assembly = objects.get(params["assembly"])
    if assembly is None or getattr(assembly, "TypeId", "") != "App::Part":
        raise MechanicalCadProgramError("assembly command requires an assembly.create target")

    if operation == "assembly.component":
        source = objects.get(params["source"])
        if source is None:
            raise MechanicalCadProgramError(f"assembly source {params['source']!r} was not created")
        if not hasattr(source, "Shape"):
            raise MechanicalCadProgramError(
                f"assembly source {params['source']!r} does not expose a FreeCAD shape")
        obj = document.addObject("App::Link", name)
        obj.Label = params.get("component_ref") or f"Component · {command_id}"
        obj.LinkedObject = source
        obj.Placement = App.Placement(
            App.Vector(*params.get("position_mm", [0.0, 0.0, 0.0])),
            App.Rotation(App.Vector(*params.get("rotation_axis", [0.0, 0.0, 1.0])),
                         params.get("rotation_deg", 0.0)),
        )
        _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
        _add_property(obj, "App::PropertyString", "SourceCommandId", "DesignStudio",
                      params["source"])
        _add_property(obj, "App::PropertyString", "ComponentRef", "DesignStudio",
                      params.get("component_ref", command_id))
        _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                      command["provenance"])
        _add_property(obj, "App::PropertyBool", "Fixed", "Assembly",
                      bool(params.get("fixed", False)))
        _add_property(obj, "App::PropertyLength", "ClearanceMM", "Assembly",
                      params.get("clearance_mm", 0.0))
        _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "valid")
        assembly.addObject(obj)
        _append_string_property(assembly, "ComponentIds", "Assembly", command_id)
    elif operation == "assembly.mate":
        first, second = objects.get(params["first"]), objects.get(params["second"])
        if first is None or second is None:
            raise MechanicalCadProgramError("assembly mate components were not created")
        if getattr(first, "TypeId", "") != "App::Link" or getattr(second, "TypeId", "") != "App::Link":
            raise MechanicalCadProgramError("assembly mates currently require assembly components")
        obj = document.addObject("App::FeaturePython", name)
        obj.Label = f"Mate · {params['kind']} · {command_id}"
        _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
        _add_property(obj, "App::PropertyString", "MateKind", "Assembly", params["kind"])
        _add_property(obj, "App::PropertyLink", "First", "Assembly", first)
        _add_property(obj, "App::PropertyLink", "Second", "Assembly", second)
        _add_property(obj, "App::PropertyString", "ReferenceJSON", "Assembly",
                      json.dumps({key: params[key] for key in
                                  ("first_reference", "second_reference") if key in params},
                                 sort_keys=True, separators=(",", ":")))
        _add_property(obj, "App::PropertyString", "SolveStatus", "Verification",
                      "pending recompute")
        _add_property(obj, "App::PropertyDistance", "ResidualMM", "Verification", 0.0)
        _add_property(obj, "App::PropertyString", "ResultJSON", "Verification", "{}")
        _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                      command["provenance"])
        _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio",
                      "pending recompute")
        obj.Proxy = AssemblyMateProxy(params)
        assembly.addObject(obj)
        _append_string_property(assembly, "MateIds", "Assembly", command_id)
    else:
        first, second = objects.get(params["first"]), objects.get(params["second"])
        obj = document.addObject("App::FeaturePython", name)
        obj.Label = f"Assembly check · {command_id}"
        _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
        _add_property(obj, "App::PropertyString", "CheckKind", "Assembly",
                      "clearance" if operation == "assembly.check_clearance" else "interference")
        # Do not add a back-link to the containing App::Part: a child link to
        # its parent creates a cycle in FreeCAD's dependency graph.
        _add_property(obj, "App::PropertyString", "AssemblyId", "Assembly",
                      params["assembly"])
        _add_property(obj, "App::PropertyLink", "First", "Assembly", first)
        _add_property(obj, "App::PropertyLink", "Second", "Assembly", second)
        _add_property(obj, "App::PropertyBool", "Passed", "Verification", False)
        _add_property(obj, "App::PropertyDistance", "MeasuredDistanceMM", "Verification", 0.0)
        _add_property(obj, "App::PropertyVolume", "IntersectionVolumeMM3", "Verification", 0.0)
        _add_property(obj, "App::PropertyString", "ResultJSON", "Verification", "{}")
        _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio",
                      "pending recompute")
        _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                      command["provenance"])
        obj.Proxy = AssemblyCheckProxy(operation, params)
        assembly.addObject(obj)
        _append_string_property(assembly, "CheckIds", "Assembly", command_id)
    objects[command_id] = obj
    return obj


class MechanicalFeatureProxy:
    """Recompute proxy for one typed mechanical feature command."""

    def __init__(self, operation: str, params: dict[str, Any]):
        self.operation = operation
        self.params = dict(params)

    def execute(self, obj) -> None:
        try:
            obj.Shape = _build_shape(obj, self.operation, self.params)
            obj.BuildStatus = "valid"
        except Exception as exc:
            obj.Shape = _empty_shape()
            obj.BuildStatus = f"invalid: {exc}"

    def onChanged(self, obj, property_name: str) -> None:
        # Parameters are intentionally immutable after program acceptance.  A
        # new program revision creates a new command object, which preserves
        # the audit trail and avoids hidden edits to downstream references.
        return None


def _empty_shape():
    try:
        _, Part = _app_part()
        return Part.Shape()
    except Exception:
        return None


def _source(obj, property_name: str):
    source = getattr(obj, property_name, None)
    if source is None or getattr(source, "Shape", None) is None or source.Shape.isNull():
        raise MechanicalCadProgramError(f"source object {property_name} has no shape")
    return source


def _build_shape(obj, operation: str, params: dict[str, Any]):
    App, Part = _app_part()
    if operation == "part.box":
        origin = _vector3(App, params.get("origin_mm", [0.0, 0.0, 0.0]))
        if all(name in params for name in ("basis_x", "basis_y", "basis_z")):
            basis_x = _vector3(App, params["basis_x"])
            basis_y = _vector3(App, params["basis_y"])
            basis_z = _vector3(App, params["basis_z"])
            first = origin + basis_x * params["length_mm"]
            second = first + basis_y * params["width_mm"]
            third = origin + basis_y * params["width_mm"]
            profile = Part.makePolygon([origin, first, second, third, origin])
            return Part.Face(profile).extrude(basis_z * params["height_mm"])
        return Part.makeBox(params["length_mm"], params["width_mm"], params["height_mm"],
                            origin)
    if operation == "part.cylinder":
        return Part.makeCylinder(params["radius_mm"], params["height_mm"],
                                 _vector3(App, params.get("base_mm", [0.0, 0.0, 0.0])),
                                 _vector3(App, params.get("direction", [0.0, 0.0, 1.0])))
    if operation == "part.sphere":
        return Part.makeSphere(params["radius_mm"],
                               _vector3(App, params.get("center_mm", [0.0, 0.0, 0.0])))
    if operation == "part.ellipsoid":
        radial = params["radial_radius_mm"]
        axial = params["axial_radius_mm"]
        transform = App.Matrix()
        transform.A11 = radial
        transform.A22 = radial
        transform.A33 = axial
        shape = Part.makeSphere(1.0).transformGeometry(transform)
        direction = _vector3(App, params.get("axis", [0.0, 0.0, 1.0]))
        shape.Placement = App.Placement(
            _vector3(App, params.get("center_mm", [0.0, 0.0, 0.0])),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), direction),
        )
        return shape
    if operation == "part.cone":
        first_radius = params["radius1_mm"]
        second_radius = params["radius2_mm"]
        base = _vector3(App, params.get("base_mm", [0.0, 0.0, 0.0]))
        direction = _vector3(App, params.get("direction", [0.0, 0.0, 1.0]))
        # Open CASCADE rejects a cone whose two radii are equal even though a
        # prismatic endpoint is a valid member of the higher-level tapered-link
        # contract.  Materialize that limiting case as the equivalent cylinder.
        if math.isclose(first_radius, second_radius, rel_tol=1.0e-12, abs_tol=1.0e-9):
            return Part.makeCylinder(first_radius, params["height_mm"], base, direction)
        return Part.makeCone(first_radius, second_radius, params["height_mm"],
                             base, direction)
    if operation == "sketch.line":
        return Part.makeLine(_vector3(App, params["start_mm"]),
                             _vector3(App, params["end_mm"]))
    if operation == "sketch.circle":
        edge = Part.makeCircle(params["radius_mm"], _vector3(App, params["center_mm"]),
                               _vector3(App, params.get("normal", [0.0, 0.0, 1.0])))
        return Part.Wire([edge])
    if operation == "sketch.rectangle":
        x, y, z = params.get("origin_mm", [0.0, 0.0, 0.0])
        width, height = params["width_mm"], params["height_mm"]
        points = [_vector3(App, [x, y, z]), _vector3(App, [x + width, y, z]),
                  _vector3(App, [x + width, y + height, z]),
                  _vector3(App, [x, y + height, z]), _vector3(App, [x, y, z])]
        return Part.makePolygon(points)
    if operation == "feature.extrude":
        profile = _source(obj, "Profile")
        shape = profile.Shape
        if shape.ShapeType == "Wire":
            shape = Part.Face(shape)
        return shape.extrude(_vector3(App, params["vector_mm"]))
    if operation == "feature.revolve":
        profile = _source(obj, "Profile")
        shape = profile.Shape
        if shape.ShapeType == "Wire":
            shape = Part.Face(shape)
        return shape.revolve(_vector3(App, params["axis_origin_mm"]),
                             _vector3(App, params["axis_direction"]),
                             params["angle_deg"])
    if operation == "feature.cut":
        return _source(obj, "Base").Shape.cut(_source(obj, "Tool").Shape)
    if operation == "feature.fuse":
        return _source(obj, "Base").Shape.fuse(_source(obj, "Tool").Shape)
    if operation == "feature.hole":
        base = _source(obj, "Base")
        cutter = Part.makeCylinder(params["radius_mm"], params["depth_mm"],
                                   _vector3(App, params["center_mm"]),
                                   _vector3(App, params.get("direction", [0.0, 0.0, 1.0])))
        return base.Shape.cut(cutter)
    if operation == "feature.fillet":
        base = _source(obj, "Base")
        edges = base.Shape.Edges
        requested = params.get("edge_indices")
        if requested is not None:
            edges = [edges[index - 1] for index in requested]
        if not edges:
            raise MechanicalCadProgramError("fillet requires at least one edge")
        return base.Shape.makeFillet(params["radius_mm"], edges)
    if operation == "feature.chamfer":
        base = _source(obj, "Base")
        edges = base.Shape.Edges
        requested = params.get("edge_indices")
        if requested is not None:
            edges = [edges[index - 1] for index in requested]
        if not edges:
            raise MechanicalCadProgramError("chamfer requires at least one edge")
        return base.Shape.makeChamfer(params["size_mm"], edges)
    if operation == "pattern.linear":
        source = _source(obj, "Source")
        direction = _vector3(App, params["direction"])
        direction.normalize()
        copies = []
        for index in range(params["count"]):
            copy = source.Shape.copy()
            copy.translate(direction * (params["spacing_mm"] * index))
            copies.append(copy)
        return Part.makeCompound(copies)
    if operation == "pattern.circular":
        source = _source(obj, "Source")
        center = _vector3(App, params["center_mm"])
        axis = _vector3(App, params["axis"])
        axis.normalize()
        copies = []
        step = params["angle_deg"] / (params["count"] - 1)
        for index in range(params["count"]):
            copy = source.Shape.copy()
            copy.rotate(center, axis, step * index)
            copies.append(copy)
        return Part.makeCompound(copies)
    raise MechanicalCadProgramError(f"unsupported operation {operation!r}")


def _add_property(obj, property_type: str, name: str, group: str, value: Any = None):
    if name not in obj.PropertiesList:
        obj.addProperty(property_type, name, group)
    if value is not None:
        setattr(obj, name, value)


def _create_object(document, command: dict[str, Any], objects: dict[str, Any]):
    command_id, operation, params = command["id"], command["op"], command["params"]
    if operation.startswith("tolerance.stack."):
        return _create_tolerance_object(document, command, objects)
    if operation.startswith("assembly."):
        return _create_assembly_object(document, command, objects)
    name = _safe_name(command_id)
    if operation == "sketch.constraint.add":
        target = objects.get(params["sketch"])
        if target is None:
            raise MechanicalCadProgramError(
                f"sketch constraint target {params['sketch']!r} was not created")
        _add_sketch_constraint(target, params)
        _annotate_sketch(target, command)
        objects[command_id] = target
        return target

    existing = document.getObject(name)
    if existing is not None:
        raise MechanicalCadProgramError(
            f"document already contains command object {command_id!r}; use a new program revision")

    if operation == "sketch.create":
        obj = document.addObject("Sketcher::SketchObject", name)
        _set_sketch_placement(obj, params.get("plane", "XY"),
                              params.get("origin_mm", [0.0, 0.0, 0.0]))
        _annotate_sketch(obj, command)
        objects[command_id] = obj
        return obj

    if operation.startswith("sketch."):
        target = objects.get(params.get("sketch")) if "sketch" in params else None
        if target is None:
            obj = document.addObject("Sketcher::SketchObject", name)
            if operation in {"sketch.line", "sketch.circle", "sketch.rectangle"}:
                origin = params.get("origin_mm", params.get("center_mm",
                                                          params.get("start_mm", [0.0, 0.0, 0.0])))
                _set_sketch_placement(obj, "XY", origin)
            target = obj
        if not _sketch_object(target):
            raise MechanicalCadProgramError(
                f"{operation} target is not a Sketcher::SketchObject")
        _add_sketch_geometry(target, operation, params)
        _annotate_sketch(target, command)
        objects[command_id] = target
        return target

    obj = document.addObject("PartDesign::FeaturePython", name)
    obj.Label = f"{command_id} · {operation}"
    _add_property(obj, "App::PropertyString", "CommandId", "DesignStudio", command_id)
    _add_property(obj, "App::PropertyString", "Operation", "DesignStudio", operation)
    _add_property(obj, "App::PropertyString", "ParametersJSON", "DesignStudio",
                  json.dumps(params, sort_keys=True, separators=(",", ":")))
    _add_property(obj, "App::PropertyStringList", "Provenance", "DesignStudio",
                  command["provenance"])
    _add_property(obj, "App::PropertyString", "BuildStatus", "DesignStudio", "pending recompute")

    link_map = {"profile": "Profile", "base": "Base", "tool": "Tool", "source": "Source"}
    for parameter, property_name in link_map.items():
        if parameter in params:
            _add_property(obj, "App::PropertyLink", property_name, "References",
                          objects[params[parameter]])
    obj.Proxy = MechanicalFeatureProxy(operation, params)
    objects[command_id] = obj
    return obj


def _execute_program_normalized(document, normalized: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized program; callers provide transaction handling."""
    App, _ = _app_part()
    controller_name = _safe_name(normalized["program_id"])
    if document.getObject(controller_name) is not None:
        raise MechanicalCadProgramError(
            f"document already contains program {normalized['program_id']!r}; create a new revision")
    controller = document.addObject("App::Part", controller_name)
    controller.Label = f"Mechanical CAD Program · {normalized['program_id']}"
    _add_property(controller, "App::PropertyString", "ProgramId", "DesignStudio",
                  normalized["program_id"])
    _add_property(controller, "App::PropertyString", "Schema", "DesignStudio", SCHEMA)
    _add_property(controller, "App::PropertyString", "Author", "DesignStudio",
                  normalized["author"])
    _add_property(controller, "App::PropertyString", "ProgramDigest", "DesignStudio",
                  _canonical_digest(normalized))
    _add_property(controller, "App::PropertyString", "BuildStatus", "DesignStudio",
                  "pending recompute")
    _add_property(controller, "App::PropertyStringList", "CommandIds", "DesignStudio",
                  [command["id"] for command in normalized["commands"]])
    _add_property(controller, "App::PropertyString", "ChecksJSON", "DesignStudio",
                  json.dumps(normalized["checks"], sort_keys=True, separators=(",", ":")))

    objects: dict[str, Any] = {}
    created = []
    for command in normalized["commands"]:
        obj = _create_object(document, command, objects)
        if obj not in created:
            created.append(obj)
            # Assembly children are already inside their App::Part; all other
            # generated nodes belong directly to the program feature tree.
            if not getattr(obj, "InList", []):
                controller.addObject(obj)
            elif all(getattr(parent, "TypeId", "") != "App::Part"
                     for parent in getattr(obj, "InList", [])):
                controller.addObject(obj)
    document.recompute()
    if any(command["op"] == "assembly.mate" for command in normalized["commands"]):
        # A mate proxy may update an App::Link placement while the first graph
        # pass is running.  Settle the dependency graph before checks consume
        # the transformed topology.
        document.recompute()
    failures = [obj for obj in created
                 if not str(getattr(obj, "BuildStatus", "")).startswith("valid")]
    if failures:
        controller.BuildStatus = "invalid: " + "; ".join(
            f"{obj.CommandId}: {obj.BuildStatus}" for obj in failures)
        raise MechanicalCadProgramError(controller.BuildStatus)

    check_results = []
    for check in normalized["checks"]:
        target = objects.get(check["target"])
        if target is None:
            raise MechanicalCadProgramError(f"check target {check['target']!r} was not created")
        shape = getattr(target, "Shape", None)
        if check["kind"] == "rebuild":
            value = list(getattr(target, "State", []))
            passed = not any("error" in str(state).lower() for state in value)
        elif check["kind"] == "sketch_constraints":
            count = int(getattr(target, "ConstraintCount", 0))
            value = {"count": count,
                     "status": str(getattr(target, "ConstraintStatus", "unknown")),
                     "fully_constrained": bool(getattr(target, "FullyConstrained", False))}
            passed = count > 0 and not value["status"].startswith("invalid")
        elif check["kind"] == "tolerance_stack":
            passed = bool(getattr(target, "Passed", False))
            raw = str(getattr(target, "ResultJSON", "{}"))
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = {"raw": raw}
        elif check["kind"] == "solid_count":
            value = len(getattr(shape, "Solids", [])) if shape is not None else 0
            passed = value > 0
        elif shape is None or shape.isNull():
            passed = False
            value = None
        elif check["kind"] == "valid_shape":
            passed, value = bool(shape.isValid()), bool(shape.isValid())
        elif check["kind"] == "volume":
            passed, value = float(shape.Volume) > 0.0, float(shape.Volume)
        elif check["kind"] == "bbox":
            box = shape.BoundBox
            value = [float(box.XLength), float(box.YLength), float(box.ZLength)]
            passed = all(item > 0.0 for item in value)
        else:
            passed, value = True, "recomputed"
        check_results.append({"kind": check["kind"], "target": check["target"],
                              "passed": passed, "value": value})
    failed_checks = [item for item in check_results if not item["passed"]]
    controller.BuildStatus = "valid" if not failed_checks else "invalid checks"
    _add_property(controller, "App::PropertyString", "ChecksResultJSON", "DesignStudio",
                  json.dumps(check_results, sort_keys=True, separators=(",", ":")))
    document.recompute()
    if failed_checks:
        raise MechanicalCadProgramError(
            "mechanical CAD checks failed: " + ", ".join(item["target"] for item in failed_checks))
    verification = []
    verified_objects = set()
    for command_id, obj in objects.items():
        if obj not in created or obj.Name in verified_objects:
            continue
        verified_objects.add(obj.Name)
        shape = getattr(obj, "Shape", None)
        box = getattr(shape, "BoundBox", None) if shape is not None and not shape.isNull() else None
        verification.append({
            "command_id": command_id,
            "object": obj.Name,
            "type_id": obj.TypeId,
            "state": list(getattr(obj, "State", [])),
            "shape_type": getattr(shape, "ShapeType", None),
            "valid": bool(shape is not None and not shape.isNull() and shape.isValid()),
            "solid_count": len(getattr(shape, "Solids", [])) if shape is not None else 0,
            "volume": float(getattr(shape, "Volume", 0.0)) if shape is not None else 0.0,
            "bbox": ([float(box.XLength), float(box.YLength), float(box.ZLength)]
                     if box is not None else None),
            "constraint_status": getattr(obj, "ConstraintStatus", None),
            "build_status": str(getattr(obj, "BuildStatus", "")),
            "result": (json.loads(str(getattr(obj, "ResultJSON")))
                       if "ResultJSON" in getattr(obj, "PropertiesList", [])
                       else None),
        })
    _add_property(controller, "App::PropertyString", "VerificationJSON", "Verification",
                  json.dumps(verification, sort_keys=True, separators=(",", ":")))
    _add_property(controller, "App::PropertyStringList", "FeatureTreeObjects", "Verification",
                  [obj.Name for obj in created])
    return {"program_id": normalized["program_id"],
            "program_digest": controller.ProgramDigest,
            "controller": controller.Name,
            "objects": [obj.Name for obj in created],
            "checks": check_results,
            "verification": verification,
            "solid_valid": all(item["passed"] for item in check_results
                                if item["kind"] == "valid_shape")}


def execute_program(document, program: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically build a mechanical CAD program."""
    if isinstance(program, dict) and program.get("schema") == SCHEMA_V2:
        from .vector_native_cad import execute_vector_program
        return execute_vector_program(document, program)
    normalized = validate_program(program)
    existing_names = set()
    for obj in getattr(document, "Objects", []):
        try:
            existing_names.add(obj.Name)
        except ReferenceError:
            continue
    try:
        return _execute_program_normalized(document, normalized)
    except Exception:
        # A failed rebuild or verification must not leave a half-applied model
        # in the authoritative document.  The accepted JSON remains available
        # to the caller for correction and a new program revision.
        new_names = []
        for obj in list(getattr(document, "Objects", [])):
            try:
                name = obj.Name
            except ReferenceError:
                continue
            if name not in existing_names:
                new_names.append(name)
        for name in reversed(new_names):
            if document.getObject(name) is not None:
                document.removeObject(name)
        document.recompute()
        raise
