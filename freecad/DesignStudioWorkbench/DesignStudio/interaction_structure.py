"""Deterministic cavities, anchors, and parametric rib graphs.

The cloud optimizer is allowed to suggest the same neutral rib-graph shape, but
only this trusted host module turns a validated graph into FreeCAD B-Rep
features.  It intentionally has no network or model dependency.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


SCHEMA = "design-studio.support-generation/1"
RESULT_SCHEMA = "design-studio.support-result/1"
MAX_ITEMS = 16_384
MAX_COORDINATE_MM = 1_000_000.0


class InteractionStructureError(ValueError):
    pass


def _finite(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise InteractionStructureError("coordinate and load values must be finite numbers")
    result = float(value)
    if abs(result) > MAX_COORDINATE_MM:
        raise InteractionStructureError("coordinate exceeds the ±1,000,000 mm limit")
    return result


def _point(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise InteractionStructureError(f"{path} must contain three coordinates")
    return tuple(_finite(coordinate) for coordinate in value)


def _bounds(value: Any, path: str) -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise InteractionStructureError(f"{path} must contain exactly min and max")
    minimum = _point(value["min"], f"{path}.min")
    maximum = _point(value["max"], f"{path}.max")
    if any(maximum[index] <= minimum[index] for index in range(3)):
        raise InteractionStructureError(f"{path} must have positive X/Y/Z extent")
    return {"min": list(minimum), "max": list(maximum)}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_placement(placement: Any, index: int,
                        document: dict[str, Any]) -> str:
    path = f"placements[{index}]"
    required = {
        "schema", "placement_id", "component_id", "component_sha256",
        "document", "transform", "coordinate_system", "snap", "locks",
        "clearance_mm",
    }
    optional = {"surface_anchor"}
    if not isinstance(placement, dict) or not required.issubset(placement) \
            or set(placement) - required - optional:
        raise InteractionStructureError(f"{path} fields are invalid")
    placement_schema = placement["schema"]
    if placement_schema not in {
            "design-studio.component-placement/1",
            "design-studio.component-placement/2"}:
        raise InteractionStructureError(f"{path} has an unsupported placement schema")
    if placement["document"] != document:
        raise InteractionStructureError(f"{path} has a stale document binding")
    placement_id = placement["placement_id"]
    if not isinstance(placement_id, str) or not placement_id:
        raise InteractionStructureError(f"{path}.placement_id is required")
    component_digest = placement["component_sha256"]
    if not isinstance(component_digest, str) or len(component_digest) != 64 or any(
            char not in "0123456789abcdef" for char in component_digest):
        raise InteractionStructureError(f"{path}.component_sha256 is invalid")
    transform = placement["transform"]
    if not isinstance(transform, dict) or set(transform) != {
            "translation_mm", "rotation_xyzw"}:
        raise InteractionStructureError(f"{path}.transform is invalid")
    _point(transform["translation_mm"], f"{path}.transform.translation_mm")
    quaternion = transform["rotation_xyzw"]
    if not isinstance(quaternion, list) or len(quaternion) != 4:
        raise InteractionStructureError(f"{path}.transform.rotation_xyzw is invalid")
    normalized_quaternion = [_finite(value) for value in quaternion]
    if sum(value * value for value in normalized_quaternion) < 1.0e-12:
        raise InteractionStructureError(f"{path}.transform.rotation_xyzw is zero")
    if placement["coordinate_system"] not in {
            "world", "enclosure_local", "pcb_local", "surface_local"}:
        raise InteractionStructureError(f"{path}.coordinate_system is invalid")
    expected_snap = {"grid", "surface", "axis", "symmetry", "clearance"}
    snap = placement["snap"]
    if not isinstance(snap, dict) or set(snap) != expected_snap or any(
            type(snap[field]) is not bool for field in expected_snap):
        raise InteractionStructureError(f"{path}.snap is invalid")
    expected_locks = {"position", "orientation", "surface_anchor"}
    locks = placement["locks"]
    if not isinstance(locks, dict) or set(locks) != expected_locks or any(
            type(locks[field]) is not bool for field in expected_locks):
        raise InteractionStructureError(f"{path}.locks is invalid")
    if not locks["position"] or not locks["orientation"]:
        raise InteractionStructureError(
            f"{path} must lock position and orientation before support generation")
    surface_anchor = placement.get("surface_anchor")
    if locks["surface_anchor"]:
        if placement_schema != "design-studio.component-placement/2":
            raise InteractionStructureError(
                f"{path} must use component-placement/2 for a locked surface anchor")
        _validate_surface_anchor(surface_anchor, path, document)
    elif surface_anchor is not None:
        raise InteractionStructureError(
            f"{path}.surface_anchor must be omitted unless its lock is enabled")
    clearance = _finite(placement["clearance_mm"])
    if not 0 <= clearance <= 1000:
        raise InteractionStructureError(f"{path}.clearance_mm is invalid")
    return placement_id


def _unit_vector(value: Any, path: str) -> tuple[float, float, float]:
    vector = _point(value, path)
    magnitude = math.sqrt(sum(coordinate * coordinate for coordinate in vector))
    if abs(magnitude - 1.0) > 1.0e-5:
        raise InteractionStructureError(f"{path} must be normalized")
    return vector


def _validate_surface_anchor(
    anchor: Any, placement_path: str, document: dict[str, Any]
) -> None:
    path = f"{placement_path}.surface_anchor"
    required = {
        "object_id", "patch_id", "patch_sha256", "baseline_sha256", "uv",
        "tangent_u_world", "tangent_v_world", "normal_world", "offset_mm",
    }
    if not isinstance(anchor, dict) or set(anchor) != required:
        raise InteractionStructureError(f"{path} fields are invalid")
    for key in ("object_id", "patch_id"):
        if not isinstance(anchor[key], str) or not anchor[key] or len(anchor[key]) > 128:
            raise InteractionStructureError(f"{path}.{key} is invalid")
    for key in ("patch_sha256", "baseline_sha256"):
        digest = anchor[key]
        if not isinstance(digest, str) or len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest):
            raise InteractionStructureError(f"{path}.{key} is invalid")
    if anchor["baseline_sha256"] != document["sha256"]:
        raise InteractionStructureError(f"{path} is bound to a stale baseline")
    uv = anchor["uv"]
    if not isinstance(uv, list) or len(uv) != 2:
        raise InteractionStructureError(f"{path}.uv must contain two coordinates")
    normalized_uv = [_finite(value) for value in uv]
    if any(value < 0.0 or value > 1.0 for value in normalized_uv):
        raise InteractionStructureError(f"{path}.uv must be normalized to [0, 1]")
    tangent_u = _unit_vector(anchor["tangent_u_world"], f"{path}.tangent_u_world")
    tangent_v = _unit_vector(anchor["tangent_v_world"], f"{path}.tangent_v_world")
    normal = _unit_vector(anchor["normal_world"], f"{path}.normal_world")
    pairs = ((tangent_u, tangent_v), (tangent_u, normal), (tangent_v, normal))
    if any(abs(sum(a * b for a, b in zip(left, right))) > 1.0e-4
           for left, right in pairs):
        raise InteractionStructureError(f"{path} tangent frame must be orthogonal")
    offset = _finite(anchor["offset_mm"])
    if abs(offset) > 1000.0:
        raise InteractionStructureError(f"{path}.offset_mm is invalid")


def validate_support_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise InteractionStructureError("support specification must be an object")
    required = {
        "schema", "document", "placements", "design_volume_mm", "anchors",
        "forbidden_bounds_mm", "load_cases", "manufacturing", "seed",
    }
    if set(spec) != required:
        raise InteractionStructureError(
            f"support specification fields differ; missing={sorted(required - set(spec))}, "
            f"unknown={sorted(set(spec) - required)}")
    if spec["schema"] != SCHEMA:
        raise InteractionStructureError(f"schema must be {SCHEMA!r}")
    document = spec["document"]
    if not isinstance(document, dict) or set(document) != {
            "document_id", "revision", "sha256"}:
        raise InteractionStructureError("document binding is invalid")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise InteractionStructureError("document_id is required")
    if type(document["revision"]) is not int or document["revision"] < 0:
        raise InteractionStructureError("document revision must be a non-negative integer")
    digest = document["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest):
        raise InteractionStructureError("document sha256 is invalid")

    design_volume = _bounds(spec["design_volume_mm"], "design_volume_mm")
    placements = spec["placements"]
    if not isinstance(placements, list) or not 1 <= len(placements) <= 4096:
        raise InteractionStructureError("placements must contain 1–4096 items")
    placement_ids = [
        _validate_placement(placement, index, document)
        for index, placement in enumerate(placements)
    ]
    if len(set(placement_ids)) != len(placement_ids):
        raise InteractionStructureError("placement IDs must be unique")

    anchors = spec["anchors"]
    if not isinstance(anchors, list) or len(anchors) > MAX_ITEMS:
        raise InteractionStructureError("anchors must be a bounded array")
    anchor_ids: set[str] = set()
    normalized_anchors = []
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict) or set(anchor) != {"id", "position_mm", "kind"}:
            raise InteractionStructureError(f"anchors[{index}] has invalid fields")
        if not isinstance(anchor["id"], str) or not anchor["id"] or anchor["id"] in anchor_ids:
            raise InteractionStructureError("anchor ids must be non-empty and unique")
        if anchor["kind"] not in {"mount", "load", "shell", "connector", "pcb"}:
            raise InteractionStructureError(f"anchors[{index}].kind is invalid")
        position = _point(anchor["position_mm"], f"anchors[{index}].position_mm")
        for axis in range(3):
            if not design_volume["min"][axis] <= position[axis] <= design_volume["max"][axis]:
                raise InteractionStructureError(f"anchor {anchor['id']!r} is outside design volume")
        anchor_ids.add(anchor["id"])
        normalized_anchors.append({**anchor, "position_mm": list(position)})

    forbidden = spec["forbidden_bounds_mm"]
    if not isinstance(forbidden, list) or len(forbidden) > MAX_ITEMS:
        raise InteractionStructureError("forbidden_bounds_mm must be a bounded array")
    normalized_forbidden = [
        _bounds(value, f"forbidden_bounds_mm[{index}]")
        for index, value in enumerate(forbidden)
    ]

    loads = spec["load_cases"]
    if not isinstance(loads, list) or len(loads) > 4096:
        raise InteractionStructureError("load_cases must be a bounded array")
    normalized_loads = []
    for index, load in enumerate(loads):
        if not isinstance(load, dict) or set(load) != {"anchor_id", "force_n"}:
            raise InteractionStructureError(f"load_cases[{index}] has invalid fields")
        if load["anchor_id"] not in anchor_ids:
            raise InteractionStructureError(
                f"load_cases[{index}] references an unknown anchor")
        normalized_loads.append(
            {"anchor_id": load["anchor_id"],
             "force_n": list(_point(load["force_n"], f"load_cases[{index}].force_n"))})

    manufacturing = spec["manufacturing"]
    if not isinstance(manufacturing, dict) or set(manufacturing) != {
            "process", "minimum_wall_mm", "minimum_rib_mm", "clearance_mm"}:
        raise InteractionStructureError("manufacturing rules are invalid")
    if manufacturing["process"] not in {"fdm", "sls", "cnc", "injection_molding"}:
        raise InteractionStructureError("manufacturing process is invalid")
    for key in ("minimum_wall_mm", "minimum_rib_mm"):
        if _finite(manufacturing[key]) <= 0:
            raise InteractionStructureError(f"manufacturing.{key} must be positive")
    if _finite(manufacturing["clearance_mm"]) < 0:
        raise InteractionStructureError("manufacturing.clearance_mm cannot be negative")
    if type(spec["seed"]) is not int or not 0 <= spec["seed"] <= 2_147_483_647:
        raise InteractionStructureError("seed is invalid")
    return {
        **spec,
        "design_volume_mm": design_volume,
        "anchors": normalized_anchors,
        "forbidden_bounds_mm": normalized_forbidden,
        "load_cases": normalized_loads,
    }


def _nearest_shell_point(point: list[float], bounds: dict[str, list[float]]) -> list[float]:
    candidates = []
    for axis in range(3):
        for side in ("min", "max"):
            candidate = list(point)
            candidate[axis] = bounds[side][axis]
            distance = abs(point[axis] - candidate[axis])
            candidates.append((distance, axis, side, candidate))
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def _point_in_bounds(point: list[float], bounds: dict[str, list[float]],
                     padding: float = 0.0) -> bool:
    return all(bounds["min"][axis] - padding <= point[axis]
               <= bounds["max"][axis] + padding for axis in range(3))


def _segment_blocked(start: list[float], end: list[float],
                     forbidden: list[dict[str, list[float]]], padding: float) -> bool:
    # Conservative deterministic sampling is sufficient for the neutral graph.
    # The host performs an exact B-Rep collision check after reconstruction.
    length = math.dist(start, end)
    samples = max(2, min(2048, int(math.ceil(length / max(0.25, padding))) + 1))
    for step in range(samples + 1):
        t = step / samples
        point = [start[axis] + (end[axis] - start[axis]) * t for axis in range(3)]
        if any(_point_in_bounds(point, obstacle, padding) for obstacle in forbidden):
            return True
    return False


def _route_to_shell(start: list[float], end: list[float],
                    forbidden: list[dict[str, list[float]]],
                    design_volume: dict[str, list[float]], clearance: float) -> list[list[float]]:
    if not _segment_blocked(start, end, forbidden, clearance):
        return [start, end]
    # Try one-axis doglegs in a stable order. Failed paths are omitted rather
    # than tunnelling through a protected component volume.
    for axis in range(3):
        for side in ("min", "max"):
            coordinate = design_volume[side][axis]
            waypoint = list(start)
            waypoint[axis] = coordinate
            if (not _segment_blocked(start, waypoint, forbidden, clearance)
                    and not _segment_blocked(waypoint, end, forbidden, clearance)):
                return [start, waypoint, end]
    return []


def generate_support_graph(spec: Any) -> dict[str, Any]:
    normalized = validate_support_spec(spec)
    manufacturing = normalized["manufacturing"]
    minimum_rib = float(manufacturing["minimum_rib_mm"])
    clearance = max(0.05, float(manufacturing["clearance_mm"]))
    loads_by_anchor: dict[str, float] = {}
    for load in normalized["load_cases"]:
        magnitude = math.sqrt(sum(value * value for value in load["force_n"]))
        loads_by_anchor[load["anchor_id"]] = (
            loads_by_anchor.get(load["anchor_id"], 0.0) + magnitude)

    ribs = []
    findings = []
    for anchor in sorted(normalized["anchors"], key=lambda item: item["id"]):
        if anchor["kind"] == "shell":
            continue
        start = anchor["position_mm"]
        end = _nearest_shell_point(start, normalized["design_volume_mm"])
        path = _route_to_shell(start, end, normalized["forbidden_bounds_mm"],
                               normalized["design_volume_mm"], clearance)
        if not path:
            findings.append({
                "severity": "error",
                "code": "NO_CLEAR_SUPPORT_PATH",
                "anchor_id": anchor["id"],
            })
            continue
        load = loads_by_anchor.get(anchor["id"], 0.0)
        thickness = max(minimum_rib, minimum_rib + math.sqrt(load) * 0.05)
        for segment_index in range(len(path) - 1):
            ribs.append({
                "id": f"rib:{anchor['id']}:{segment_index}",
                "anchor_id": anchor["id"],
                "start_mm": path[segment_index],
                "end_mm": path[segment_index + 1],
                "thickness_mm": round(thickness, 6),
                "height_mm": round(max(thickness * 1.5,
                                       float(manufacturing["minimum_wall_mm"]) * 1.5), 6),
                "generator": "deterministic-load-path-v1",
            })

    expanded_cavities = []
    for index, obstacle in enumerate(normalized["forbidden_bounds_mm"]):
        expanded_cavities.append({
            "id": f"cavity:{index}",
            "min": [value - clearance for value in obstacle["min"]],
            "max": [value + clearance for value in obstacle["max"]],
            "clearance_mm": clearance,
        })

    input_digest = _canonical_digest(normalized)
    result = {
        "schema": RESULT_SCHEMA,
        "document": normalized["document"],
        "input_sha256": input_digest,
        "generator": {
            "name": "designstudio-local-support",
            "version": 1,
            "seed": normalized["seed"],
            "authoritative_geometry": False,
        },
        "status": "pass" if not any(item["severity"] == "error"
                                    for item in findings) else "fail",
        "cavities": expanded_cavities,
        "ribs": ribs,
        "findings": findings,
        "required_host_checks": [
            "brep_valid",
            "exact_collision_free",
            "manufacturing_rules_pass",
            "structural_screening_pass",
            "assembly_order_pass",
        ],
    }
    result["result_sha256"] = _canonical_digest(result)
    return result


def validate_support_result(spec: Any, candidate: Any) -> dict[str, Any]:
    normalized = validate_support_spec(spec)
    if not isinstance(candidate, dict):
        raise InteractionStructureError("support result must be an object")
    required = {
        "schema", "document", "input_sha256", "generator", "status",
        "cavities", "ribs", "findings", "required_host_checks", "result_sha256",
    }
    if set(candidate) != required:
        raise InteractionStructureError("support result fields are invalid")
    if candidate["schema"] != RESULT_SCHEMA or candidate["status"] != "pass":
        raise InteractionStructureError("support result is not a passing support-result/1")
    if candidate["document"] != normalized["document"]:
        raise InteractionStructureError("support result has a stale document binding")
    input_digest = _canonical_digest(normalized)
    if candidate["input_sha256"] != input_digest:
        raise InteractionStructureError("support result input digest does not match specification")
    digest_material = dict(candidate)
    result_digest = digest_material.pop("result_sha256")
    if result_digest != _canonical_digest(digest_material):
        raise InteractionStructureError("support result digest is invalid")
    generator = candidate["generator"]
    if not isinstance(generator, dict) \
            or generator.get("authoritative_geometry") is not False \
            or not isinstance(generator.get("name"), str) \
            or not generator.get("name"):
        raise InteractionStructureError(
            "cloud generator must explicitly mark geometry non-authoritative")
    mandatory_checks = {
        "brep_valid", "exact_collision_free", "manufacturing_rules_pass",
        "structural_screening_pass", "assembly_order_pass",
    }
    checks = candidate["required_host_checks"]
    if not isinstance(checks, list) or not mandatory_checks.issubset(set(checks)):
        raise InteractionStructureError("support result omits mandatory host checks")

    minimum_rib = float(normalized["manufacturing"]["minimum_rib_mm"])
    anchor_ids = {anchor["id"] for anchor in normalized["anchors"]}
    rib_ids: set[str] = set()
    ribs = candidate["ribs"]
    if not isinstance(ribs, list) or len(ribs) > MAX_ITEMS:
        raise InteractionStructureError("support result ribs are not bounded")
    normalized_ribs = []
    for index, rib in enumerate(ribs):
        expected = {
            "id", "anchor_id", "start_mm", "end_mm", "thickness_mm",
            "height_mm", "generator",
        }
        if not isinstance(rib, dict) or set(rib) != expected:
            raise InteractionStructureError(f"support result ribs[{index}] fields are invalid")
        if not isinstance(rib["id"], str) or not rib["id"] or rib["id"] in rib_ids:
            raise InteractionStructureError("support result rib IDs must be unique")
        if rib["anchor_id"] not in anchor_ids:
            raise InteractionStructureError("support result rib references an unknown anchor")
        start = _point(rib["start_mm"], f"ribs[{index}].start_mm")
        end = _point(rib["end_mm"], f"ribs[{index}].end_mm")
        if math.dist(start, end) <= 1.0e-6:
            raise InteractionStructureError("support result contains a zero-length rib")
        for point in (start, end):
            for axis in range(3):
                if not normalized["design_volume_mm"]["min"][axis] - 1.0e-6 \
                        <= point[axis] \
                        <= normalized["design_volume_mm"]["max"][axis] + 1.0e-6:
                    raise InteractionStructureError("support result rib leaves the design volume")
        thickness = _finite(rib["thickness_mm"])
        height = _finite(rib["height_mm"])
        if thickness < minimum_rib or height <= 0 or thickness > 1000 or height > 1000:
            raise InteractionStructureError("support result rib dimensions are invalid")
        rib_ids.add(rib["id"])
        normalized_ribs.append({
            **rib, "start_mm": list(start), "end_mm": list(end),
            "thickness_mm": thickness, "height_mm": height,
        })

    # Protected cavities are host-derived from the immutable keep-outs. A cloud
    # service cannot shrink or move them even if its candidate says otherwise.
    clearance = float(normalized["manufacturing"]["clearance_mm"])
    cavities = [{
        "id": f"cavity:{index}",
        "min": [value - clearance for value in obstacle["min"]],
        "max": [value + clearance for value in obstacle["max"]],
        "clearance_mm": clearance,
    } for index, obstacle in enumerate(normalized["forbidden_bounds_mm"])]
    if candidate["cavities"] != cavities:
        raise InteractionStructureError(
            "support result attempted to alter host-derived protected cavities")
    return {**candidate, "ribs": normalized_ribs}


def _rib_shape(Part, App, start: list[float], end: list[float],
               thickness: float, height: float):
    direction = App.Vector(end[0] - start[0], end[1] - start[1], end[2] - start[2])
    length = direction.Length
    if length <= 1.0e-6:
        raise InteractionStructureError("rib segment has zero length")
    shape = Part.makeBox(length, thickness, height,
                         App.Vector(0, -thickness / 2.0, -height / 2.0))
    shape.Placement = App.Placement(
        App.Vector(*start), App.Rotation(App.Vector(1, 0, 0), direction))
    return shape


def create_support_structure(document, spec: Any,
                             candidate_result: Any | None = None) -> dict[str, Any]:
    """Create host-authoritative FreeCAD cavity envelopes and rib B-Reps."""
    import FreeCAD as App
    import Part

    result = (generate_support_graph(spec) if candidate_result is None
              else validate_support_result(spec, candidate_result))
    if result["status"] != "pass":
        raise InteractionStructureError("support graph contains blocking findings")
    group = document.getObject("InteractionStructure")
    if group is None:
        group = document.addObject("App::DocumentObjectGroup", "InteractionStructure")
        group.Label = "Interaction Cavities and Supports"
    created = []
    for cavity in result["cavities"]:
        name = cavity["id"].replace(":", "_")
        obj = document.getObject(name) or document.addObject("Part::Feature", name)
        obj.Label = cavity["id"]
        size = [cavity["max"][axis] - cavity["min"][axis] for axis in range(3)]
        obj.Shape = Part.makeBox(*size, App.Vector(*cavity["min"]))
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.Transparency = 80
            obj.ViewObject.ShapeColor = (0.90, 0.35, 0.20)
        group.addObject(obj)
        created.append(obj.Name)
    for rib in result["ribs"]:
        name = rib["id"].replace(":", "_")
        obj = document.getObject(name) or document.addObject("Part::Feature", name)
        obj.Label = rib["id"]
        obj.Shape = _rib_shape(Part, App, rib["start_mm"], rib["end_mm"],
                               rib["thickness_mm"], rib["height_mm"])
        if not obj.Shape.isValid():
            raise InteractionStructureError(f"{rib['id']} produced an invalid B-Rep")
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.ShapeColor = (0.25, 0.60, 0.90)
        group.addObject(obj)
        created.append(obj.Name)
    if "InputDigest" not in group.PropertiesList:
        group.addProperty("App::PropertyString", "InputDigest", "DesignStudio")
        group.addProperty("App::PropertyString", "ResultDigest", "DesignStudio")
        group.addProperty("App::PropertyString", "Generator", "DesignStudio")
    group.InputDigest = result["input_sha256"]
    group.ResultDigest = result["result_sha256"]
    group.Generator = result["generator"]["name"]
    document.recompute()
    return {**result, "freecad_objects": created}
