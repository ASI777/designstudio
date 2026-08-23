"""Manufacturable rib-network planning independent of the CAD host.

The network is a neutral, deterministic graph.  It is deliberately not a
mesh or a collection of axis-aligned bounding boxes: each member carries a
centerline, section dimensions, draft and root-fillet intent.  A geometry
backend is responsible for reconstructing and validating the final B-Rep.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .manufacturing_rules import (canonical_digest, check_feature,
                                  default_profile, recommended_rib,
                                  validate_profile)


SCHEMA = "design-studio.rib-network/1"


class RibNetworkError(ValueError):
    """Raised when a proposed network cannot be proven safe for reconstruction."""


def _point(value: Any, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RibNetworkError(f"{path} must contain three coordinates")
    result = []
    for index, coordinate in enumerate(value):
        if type(coordinate) not in (int, float) or not math.isfinite(float(coordinate)):
            raise RibNetworkError(f"{path}[{index}] must be finite")
        result.append(float(coordinate))
    return result


def _bounds(value: Any, path: str) -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise RibNetworkError(f"{path} must contain min and max")
    minimum, maximum = _point(value["min"], f"{path}.min"), _point(value["max"], f"{path}.max")
    if any(maximum[index] <= minimum[index] for index in range(3)):
        raise RibNetworkError(f"{path} has no positive extent")
    return {"min": minimum, "max": maximum}


def _inside(point: list[float], bounds: dict[str, list[float]], padding: float = 0.0) -> bool:
    return all(bounds["min"][axis] - padding <= point[axis] <=
               bounds["max"][axis] + padding for axis in range(3))


def _blocked(start: list[float], end: list[float], obstacles: list[dict[str, Any]],
             padding: float) -> bool:
    distance = math.dist(start, end)
    samples = max(2, min(4096, int(math.ceil(distance / max(0.25, padding))) + 1))
    for index in range(samples + 1):
        ratio = index / samples
        point = [start[axis] + (end[axis] - start[axis]) * ratio for axis in range(3)]
        if any(_inside(point, obstacle["bounds"], padding) for obstacle in obstacles):
            return True
    return False


def _nearest_shell(point: list[float], volume: dict[str, list[float]]) -> tuple[list[float], int, str]:
    candidates = []
    for axis in range(3):
        for side in ("min", "max"):
            target = list(point)
            target[axis] = volume[side][axis]
            candidates.append((abs(point[axis] - target[axis]), axis, side, target))
    _, axis, side, target = min(candidates, key=lambda item: (item[0], item[1], item[2]))
    return target, axis, side


def _route(start: list[float], end: list[float], obstacles: list[dict[str, Any]],
           volume: dict[str, list[float]], clearance: float) -> list[list[float]]:
    if not _blocked(start, end, obstacles, clearance):
        return [start, end]
    # Try two-segment doglegs first, then a three-segment path through a
    # deterministic shell corner.  Every proposed segment is sampled here;
    # the host still performs exact B-Rep collision checks later.
    for axis in range(3):
        for side in ("min", "max"):
            waypoint = list(start)
            waypoint[axis] = volume[side][axis]
            if not _blocked(start, waypoint, obstacles, clearance) and not _blocked(
                    waypoint, end, obstacles, clearance):
                return [start, waypoint, end]
    for axis_a in range(3):
        for side_a in ("min", "max"):
            for axis_b in range(axis_a + 1, 3):
                for side_b in ("min", "max"):
                    waypoint_a = list(start)
                    waypoint_a[axis_a] = volume[side_a][axis_a]
                    waypoint_b = list(waypoint_a)
                    waypoint_b[axis_b] = volume[side_b][axis_b]
                    if all(not _blocked(a, b, obstacles, clearance) for a, b in zip(
                            (start, waypoint_a, waypoint_b),
                            (waypoint_a, waypoint_b, end))):
                        return [start, waypoint_a, waypoint_b, end]
    return []


def _normalize_obstacles(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 16384:
        raise RibNetworkError("obstacles must be a bounded list")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"id", "bounds"}:
            raise RibNetworkError(f"obstacles[{index}] fields are invalid")
        if not isinstance(item["id"], str) or not item["id"]:
            raise RibNetworkError(f"obstacles[{index}].id is invalid")
        result.append({"id": item["id"], "bounds": _bounds(item["bounds"],
                                                               f"obstacles[{index}].bounds")})
    return result


def build_rib_network(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic, process-aware neutral rib graph."""
    if not isinstance(spec, dict):
        raise RibNetworkError("rib specification must be an object")
    required = {"schema", "design_volume_mm", "anchors", "obstacles",
                "load_cases", "manufacturing", "profile", "seed"}
    if set(spec) != required:
        raise RibNetworkError("rib specification fields differ from the contract")
    if spec["schema"] != SCHEMA:
        raise RibNetworkError(f"schema must be {SCHEMA}")
    volume = _bounds(spec["design_volume_mm"], "design_volume_mm")
    profile = validate_profile(spec["profile"] or default_profile())
    manufacturing = spec["manufacturing"]
    if not isinstance(manufacturing, dict) or set(manufacturing) != {
            "wall_mm", "clearance_mm", "pull_direction"}:
        raise RibNetworkError("manufacturing fields are invalid")
    wall = float(manufacturing["wall_mm"])
    clearance = float(manufacturing["clearance_mm"])
    if not math.isfinite(wall) or wall <= 0.0 or not math.isfinite(clearance) or clearance < 0.0:
        raise RibNetworkError("manufacturing dimensions are invalid")
    pull = _point(manufacturing["pull_direction"], "manufacturing.pull_direction")
    if math.dist(pull, [0.0, 0.0, 0.0]) <= 1.0e-9:
        raise RibNetworkError("pull_direction must not be zero")
    obstacles = _normalize_obstacles(spec["obstacles"])
    anchors = spec["anchors"]
    if not isinstance(anchors, list) or not anchors:
        raise RibNetworkError("anchors must be a non-empty list")
    load_by_anchor: dict[str, float] = {}
    for index, load in enumerate(spec["load_cases"]):
        if not isinstance(load, dict) or set(load) != {"anchor_id", "force_n"}:
            raise RibNetworkError(f"load_cases[{index}] fields are invalid")
        force = _point(load["force_n"], f"load_cases[{index}].force_n")
        load_by_anchor[load["anchor_id"]] = load_by_anchor.get(load["anchor_id"], 0.0) + math.sqrt(
            sum(component * component for component in force))

    normalized_anchors = []
    seen = set()
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict) or set(anchor) != {"id", "position_mm", "kind"}:
            raise RibNetworkError(f"anchors[{index}] fields are invalid")
        anchor_id = anchor["id"]
        if not isinstance(anchor_id, str) or not anchor_id or anchor_id in seen:
            raise RibNetworkError("anchor IDs must be unique and non-empty")
        position = _point(anchor["position_mm"], f"anchors[{index}].position_mm")
        if not _inside(position, volume):
            raise RibNetworkError(f"anchor {anchor_id!r} is outside design volume")
        if anchor["kind"] not in {"mount", "load", "shell", "connector", "pcb", "boss"}:
            raise RibNetworkError(f"anchors[{index}].kind is invalid")
        seen.add(anchor_id)
        normalized_anchors.append({"id": anchor_id, "position_mm": position,
                                   "kind": anchor["kind"]})

    ribs = []
    nodes = [{"id": anchor["id"], "kind": anchor["kind"],
              "position_mm": anchor["position_mm"]} for anchor in normalized_anchors]
    findings = []
    for anchor in normalized_anchors:
        if anchor["kind"] == "shell":
            continue
        shell_point, shell_axis, shell_side = _nearest_shell(anchor["position_mm"], volume)
        path = _route(anchor["position_mm"], shell_point, obstacles, volume, clearance)
        if not path:
            findings.append({"code": "NO_SUPPORT_PATH", "severity": "error",
                             "anchor_id": anchor["id"]})
            continue
        dimensions = recommended_rib(profile, wall)
        load = load_by_anchor.get(anchor["id"], 0.0)
        # A bounded load factor changes member size without pretending to be an
        # FEA result.  The exact solve remains a separate release gate.
        scale = min(1.75, 1.0 + math.sqrt(max(0.0, load)) / 20.0)
        thickness = min(profile["rib"]["maximum_mm"], dimensions["thickness_mm"] * scale)
        height = min(wall * 3.0, max(dimensions["height_mm"], thickness * scale * 2.0))
        for segment_index, (start, end) in enumerate(zip(path, path[1:])):
            if math.dist(start, end) <= 1.0e-6:
                continue
            rib_id = f"rib:{anchor['id']}:{segment_index}"
            rib = {
                "id": rib_id,
                "start_node": anchor["id"] if segment_index == 0 else f"{rib_id}:start",
                "end_node": f"{rib_id}:end" if segment_index < len(path) - 2 else
                            f"shell:{shell_axis}:{shell_side}",
                "anchor_id": anchor["id"],
                "path_mm": [start, end],
                "thickness_mm": round(thickness, 6),
                "height_mm": round(height, 6),
                "draft_deg": dimensions["draft_deg"],
                "root_fillet_mm": dimensions["root_fillet_mm"],
                "clearance_mm": clearance,
                "generator": "designstudio-rib-network-v1",
            }
            ribs.append(rib)
            findings.extend(check_feature({"id": rib_id, "kind": "rib",
                                           "thickness_mm": rib["thickness_mm"],
                                           "draft_deg": rib["draft_deg"],
                                           "root_fillet_mm": rib["root_fillet_mm"],
                                           "clearance_mm": clearance}, profile))
    result = {
        "schema": SCHEMA,
        "profile_id": profile["profile_id"],
        "profile_digest": canonical_digest(profile),
        "design_volume_mm": volume,
        "manufacturing": {"wall_mm": wall, "clearance_mm": clearance,
                           "pull_direction": pull},
        "nodes": nodes,
        "ribs": ribs,
        "findings": findings,
        "status": "pass" if not any(item["severity"] == "error" for item in findings) else "fail",
        "generator": {"name": "designstudio-rib-network", "version": 1,
                       "authoritative_geometry": False,
                       "seed": spec["seed"]},
    }
    result["result_sha256"] = canonical_digest(result)
    return result


def validate_rib_network(result: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        raise RibNetworkError("invalid rib-network result")
    profile = validate_profile(profile or default_profile())
    if result.get("profile_digest") != canonical_digest(profile):
        raise RibNetworkError("rib network profile digest is stale")
    material = deepcopy(result)
    digest = material.pop("result_sha256", None)
    if not isinstance(digest, str) or digest != canonical_digest(material):
        raise RibNetworkError("rib network digest is invalid")
    if result.get("status") != "pass":
        raise RibNetworkError("rib network contains blocking findings")
    for index, rib in enumerate(result.get("ribs", [])):
        if not isinstance(rib, dict) or set(rib) != {
                "id", "start_node", "end_node", "anchor_id", "path_mm",
                "thickness_mm", "height_mm", "draft_deg", "root_fillet_mm",
                "clearance_mm", "generator"}:
            raise RibNetworkError(f"ribs[{index}] fields are invalid")
        if len(rib["path_mm"]) != 2 or math.dist(*[_point(point, "rib.path_mm")
                                                    for point in rib["path_mm"]]) <= 1.0e-6:
            raise RibNetworkError(f"ribs[{index}] has an invalid path")
        for field in ("thickness_mm", "height_mm", "draft_deg", "root_fillet_mm", "clearance_mm"):
            if type(rib[field]) not in (int, float) or not math.isfinite(float(rib[field])):
                raise RibNetworkError(f"ribs[{index}].{field} is invalid")
        if rib["thickness_mm"] < profile["rib"]["minimum_mm"]:
            raise RibNetworkError(f"ribs[{index}] is thinner than the selected process minimum")
    return result


def to_mechanical_program(result: dict[str, Any],
                          profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a validated neutral graph into typed feature.rib commands."""
    # A custom manufacturing profile is not reconstructible from its digest;
    # callers that generated the graph must pass the same profile back through
    # this compilation boundary so a stale/default profile cannot be accepted.
    validate_rib_network(result, profile=profile)
    from .vector_native_cad import control_datum_digest
    commands = []
    for rib in result["ribs"]:
        commands.append({
            "id": rib["id"].replace(":", "_"),
            "op": "feature.rib",
            "params": {
                "start_mm": rib["path_mm"][0], "end_mm": rib["path_mm"][1],
                "thickness_mm": rib["thickness_mm"], "height_mm": rib["height_mm"],
                "draft_deg": rib["draft_deg"], "root_fillet_mm": rib["root_fillet_mm"],
            },
            "provenance": [f"{result['generator']['name']}:{rib['id']}"]
        })
    minimum = result["design_volume_mm"]["min"]
    maximum = result["design_volume_mm"]["max"]
    return {"schema": "design-studio.mechanical-cad-program/2",
            "program_id": "rib-network-reconstruction", "units": "mm",
            "author": "DesignStudio rib-network compiler",
            "envelope": {"min_mm": minimum, "max_mm": maximum},
            "control_datums": [{"id": "rib-network-origin", "point_mm": minimum,
                                "locked": True, "digest": control_datum_digest(minimum)}],
            "commands": commands,
            "checks": [{"kind": "valid_shape", "target": command["id"]}
                       for command in commands]}


__all__ = ["SCHEMA", "RibNetworkError", "build_rib_network",
           "validate_rib_network", "to_mechanical_program"]
