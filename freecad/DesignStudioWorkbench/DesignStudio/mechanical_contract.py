"""Pure-Python mechanical-contract derivation and `.dsproj` synchronization.

FreeCAD owns authoritative 3D geometry. DesignStudio consumes only this locked,
auditable 2D/2.5D boundary. Keeping the contract module FreeCAD-independent makes
the safety policy testable without a GUI or an Open CASCADE installation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "design-studio.mechanical-contract/1"


class ContractError(ValueError):
    pass


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{label} must be finite")
    return result


def _point2(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ContractError(f"{label} must be [x, y]")
    return [round(_finite(value[0], f"{label}.x"), 6),
            round(_finite(value[1], f"{label}.y"), 6)]


def _polygon(value: Any, label: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise ContractError(f"{label} needs at least three points")
    points = [_point2(point, f"{label}[{index}]") for index, point in enumerate(value)]
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or abs(_signed_area(points)) < 1e-9:
        raise ContractError(f"{label} is degenerate")
    return points


def _signed_area(points: list[list[float]]) -> float:
    return sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2.0


def _canonical_bytes(value: dict) -> bytes:
    # Qt's QJson writer may serialize 1.0 as 1. Normalize mathematically integral
    # floats so a harmless DesignStudio save cannot invalidate FreeCAD's lock.
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def contract_digest(contract: dict) -> str:
    payload = copy.deepcopy(contract)
    payload.pop("contract_digest", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def validate_contract(contract: dict, verify_digest: bool = True) -> None:
    if not isinstance(contract, dict):
        raise ContractError("contract must be an object")
    if contract.get("schema") != SCHEMA:
        raise ContractError(f"unsupported contract schema: {contract.get('schema')!r}")
    if contract.get("status") != "locked":
        raise ContractError("mechanical contract is not locked")
    if contract.get("units") != "mm":
        raise ContractError("mechanical contract units must be mm")
    if not isinstance(contract.get("contract_id"), str) or not contract["contract_id"]:
        raise ContractError("contract_id is required")
    revision = contract.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractError("revision must be a positive integer")
    board = contract.get("board")
    if not isinstance(board, dict):
        raise ContractError("board is required")
    _polygon(board.get("outline_pts"), "board.outline_pts")
    for index, cutout in enumerate(board.get("cutouts", [])):
        _polygon(cutout, f"board.cutouts[{index}]")
    thickness = _finite(board.get("thickness_mm"), "board.thickness_mm")
    if thickness <= 0:
        raise ContractError("board.thickness_mm must be positive")
    z_range = board.get("z_range_mm")
    if not isinstance(z_range, list) or len(z_range) != 2:
        raise ContractError("board.z_range_mm must be [min, max]")
    z0, z1 = (_finite(z_range[0], "board.z_range_mm[0]"),
              _finite(z_range[1], "board.z_range_mm[1]"))
    if z1 <= z0:
        raise ContractError("board.z_range_mm must have positive height")
    for collection in ("mounting_holes", "height_zones", "cooling_zones",
                       "service_clearances"):
        if not isinstance(board.get(collection, []), list):
            raise ContractError(f"board.{collection} must be an array")
    if not isinstance(contract.get("fixed_items", []), list):
        raise ContractError("fixed_items must be an array")
    if not isinstance(contract.get("connector_locations", []), list):
        raise ContractError("connector_locations must be an array")
    digest = contract.get("contract_digest", "")
    if verify_digest and digest != contract_digest(contract):
        raise ContractError("mechanical contract digest mismatch")


def derive_contract(snapshot: dict, revision: int = 1) -> dict:
    """Normalize a neutral FreeCAD snapshot into a locked contract.

    `snapshot` is produced by `freecad_adapter.snapshot_from_document()` and is
    intentionally small: polygons, transforms, envelopes, and asset references;
    never tessellation or B-Rep topology.
    """
    if not isinstance(snapshot, dict):
        raise ContractError("snapshot must be an object")
    source = copy.deepcopy(snapshot.get("source") or {})
    source_id = source.get("document_id") or source.get("path") or "unsaved-freecad-document"
    board_in = snapshot.get("board")
    if not isinstance(board_in, dict):
        raise ContractError("snapshot.board is required")

    outline = _polygon(board_in.get("outline_pts"), "snapshot.board.outline_pts")
    cutouts = [_polygon(item, f"snapshot.board.cutouts[{index}]")
               for index, item in enumerate(board_in.get("cutouts", []))]
    thickness = _finite(board_in.get("thickness_mm", 1.6), "snapshot.board.thickness_mm")
    if thickness <= 0:
        raise ContractError("snapshot board thickness must be positive")
    z_range = board_in.get("z_range_mm", [0.0, thickness])

    contract = {
        "schema": SCHEMA,
        "contract_id": "freecad:" + hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:24],
        "revision": revision,
        "status": "locked",
        "units": "mm",
        "source": source,
        "frame_to_world": copy.deepcopy(snapshot.get("frame_to_world") or {
            "origin_mm": [0.0, 0.0, 0.0],
            "x_axis": [1.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0],
            "z_axis": [0.0, 0.0, 1.0],
        }),
        "board": {
            "outline_pts": outline,
            "cutouts": cutouts,
            "thickness_mm": round(thickness, 6),
            "z_range_mm": [round(_finite(z_range[0], "z_range min"), 6),
                           round(_finite(z_range[1], "z_range max"), 6)],
            "mounting_holes": copy.deepcopy(board_in.get("mounting_holes", [])),
            "height_zones": copy.deepcopy(board_in.get("height_zones", [])),
            "cooling_zones": copy.deepcopy(board_in.get("cooling_zones", [])),
            "service_clearances": copy.deepcopy(board_in.get("service_clearances", [])),
        },
        "fixed_items": copy.deepcopy(snapshot.get("fixed_items", [])),
        "connector_locations": copy.deepcopy(snapshot.get("connector_locations", [])),
    }
    contract["contract_digest"] = contract_digest(contract)
    validate_contract(contract)
    return contract


def _circle(center: list[float], diameter: float, segments: int = 24) -> list[list[float]]:
    radius = diameter / 2.0
    return [[round(center[0] + radius * math.cos(2 * math.pi * i / segments), 6),
             round(center[1] + radius * math.sin(2 * math.pi * i / segments), 6)]
            for i in range(segments)]


def _bbox(points: list[list[float]]) -> tuple[float, float, float, float]:
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def apply_contract(project: dict, contract: dict, contract_path: str = "",
                   contract_file_sha256: str = "") -> dict:
    """Return a `.dsproj` with FreeCAD-owned fields applied fail-closed.

    Existing schematic, nets, routes, footprints, and manufacturing rules are
    retained. Only mechanical-authority fields and explicit fixed placements are
    changed.
    """
    validate_contract(contract)
    if not isinstance(project, dict):
        raise ContractError("project must be an object")
    result = copy.deepcopy(project)
    board = contract["board"]
    outline = copy.deepcopy(board["outline_pts"])
    cutouts = copy.deepcopy(board.get("cutouts", []))

    for hole in board.get("mounting_holes", []):
        center = _point2(hole.get("center_mm"), "mounting_hole.center_mm")
        diameter = _finite(hole.get("diameter_mm"), "mounting_hole.diameter_mm")
        if diameter <= 0:
            raise ContractError("mounting-hole diameter must be positive")
        cutouts.append(_circle(center, diameter))

    x0, y0, x1, y1 = _bbox(outline)
    result.update({
        "version": 2,
        "board_width_mm": round(x1 - x0, 6),
        "board_height_mm": round(y1 - y0, 6),
        "board_outline_pts": outline,
        "board_cutouts": cutouts,
        "mechanical_contract": copy.deepcopy(contract),
    })
    result.setdefault("footprints", [])
    result.setdefault("traces", [])
    result.setdefault("vias", [])
    result.setdefault("net_table", [])
    result.setdefault("net_classes", [])

    existing_areas = [area for area in result.get("rule_areas", [])
                      if area.get("source") != "freecad-mechanical-contract"]
    revision = str(contract["revision"])

    def add_area(item: dict, *, forbid_placement: bool, forbid_routing: bool,
                 forbid_vias: bool, max_height: float = 0.0) -> None:
        points = _polygon(item.get("outline_pts"), f"rule area {item.get('id', '?')}")
        existing_areas.append({
            "id": int(item.get("numeric_id", 10000 + len(existing_areas))),
            "name": str(item.get("name") or item.get("id") or "FreeCAD mechanical zone"),
            "pts": points,
            "layer": -1,
            "clearance_mm": max(0.0, float(item.get("clearance_mm", 0.0))),
            "min_trace_width_mm": 0.0,
            "forbid_routing": forbid_routing,
            "forbid_vias": forbid_vias,
            "forbid_placement": forbid_placement,
            "max_height_mm": max_height,
            "source": "freecad-mechanical-contract",
            "source_revision": revision,
        })

    for zone in board.get("height_zones", []):
        height = _finite(zone.get("max_height_mm"), "height_zone.max_height_mm")
        if height <= 0:
            raise ContractError("height-zone max height must be positive")
        add_area(zone, forbid_placement=False, forbid_routing=False,
                 forbid_vias=False, max_height=height)
    for zone in board.get("cooling_zones", []):
        add_area(zone, forbid_placement=bool(zone.get("keep_clear", True)),
                 forbid_routing=False, forbid_vias=False)
    for zone in board.get("service_clearances", []):
        add_area(zone, forbid_placement=True, forbid_routing=True, forbid_vias=True)
    for item in contract.get("fixed_items", []):
        if item.get("keepout_outline_pts"):
            area_item = dict(item)
            area_item["outline_pts"] = item["keepout_outline_pts"]
            add_area(area_item, forbid_placement=True, forbid_routing=False, forbid_vias=False)
    result["rule_areas"] = existing_areas

    fixed_by_ref = {str(item.get("ref")): item
                    for item in contract.get("fixed_items", []) if item.get("ref")}
    fixed_by_ref.update({str(item.get("ref")): item
                         for item in contract.get("connector_locations", []) if item.get("ref")})
    for footprint in result["footprints"]:
        item = fixed_by_ref.get(str(footprint.get("ref")))
        if not item:
            continue
        footprint["x_mm"] = _finite(item.get("x_mm"), f"{footprint.get('ref')}.x_mm")
        footprint["y_mm"] = _finite(item.get("y_mm"), f"{footprint.get('ref')}.y_mm")
        footprint["rot_deg"] = _finite(item.get("rot_deg", 0.0), "rot_deg")
        placement = dict(footprint.get("placement") or {})
        placement["locked"] = True
        if item.get("edge_anchor"):
            placement["edge_anchor"] = item["edge_anchor"]
        footprint["placement"] = placement

    verification = dict(result.get("verification_requirements") or {})
    verification["require_enclosure_evidence"] = True
    verification["enclosure_evidence_path"] = contract_path
    verification["enclosure_evidence_sha256"] = (
        contract_file_sha256 or contract["contract_digest"])
    result["verification_requirements"] = verification
    return result


def write_contract_and_project(project_path: str | Path, contract: dict) -> tuple[Path, Path]:
    """Atomically write a sidecar contract, then synchronize the project."""
    validate_contract(contract)
    project_path = Path(project_path).expanduser().resolve()
    contract_path = project_path.with_suffix(".mechanical-contract.json")
    if project_path.exists():
        project = json.loads(project_path.read_text(encoding="utf-8"))
    else:
        project = {"version": 2, "footprints": [], "traces": [], "vias": [],
                   "net_table": [], "net_classes": [], "rule_areas": []}
    relative_contract = contract_path.name
    contract_text = json.dumps(contract, indent=2, sort_keys=True) + "\n"
    file_sha256 = hashlib.sha256(contract_text.encode("utf-8")).hexdigest()
    synchronized = apply_contract(project, contract, relative_contract, file_sha256)
    contract_tmp = contract_path.with_suffix(contract_path.suffix + ".tmp")
    project_tmp = project_path.with_suffix(project_path.suffix + ".tmp")
    contract_tmp.write_text(contract_text, encoding="utf-8")
    project_tmp.write_text(json.dumps(synchronized, indent=2) + "\n", encoding="utf-8")
    contract_tmp.replace(contract_path)
    project_tmp.replace(project_path)
    return project_path, contract_path
