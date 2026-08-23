"""Manufacturer-bound, deterministic PCB production outputs.

This module is intentionally separate from ``swarm.agents.fab_export_agent``.
The latter is a preview renderer.  This exporter requires exact project,
verification, manufacturer-document, stackup, DFM, and component-model
bindings before it writes any production artifact.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swarm.memory.component_binding import BindingError, validate_binding


GENERATOR = "designstudio-production-cam"
GENERATOR_VERSION = "1.0.0"
PROFILE_SCHEMA = "design-studio.fabrication-profile/1"
PACKAGE_SCHEMA = "design-studio.production-package/1"


class ProductionExportError(RuntimeError):
    """A release-critical input or generated artifact is invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProductionExportError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductionExportError(f"{path} does not contain a JSON object")
    return value


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def _board_document(document: dict) -> tuple[dict, bool]:
    if document.get("format") == "design-studio.project/3":
        board = document.get("board")
        if not isinstance(board, dict):
            raise ProductionExportError("project/3 document has no embedded board")
        return board, True
    if int(document.get("version", 0)) in (1, 2):
        return document, False
    raise ProductionExportError("production export requires design-studio.project/2 or project/3")


def _required(mapping: dict, keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ProductionExportError(f"{label} is missing: {', '.join(missing)}")


def validate_fabrication_profile(profile: dict, profile_path: Path) -> dict:
    _required(profile, ("schema", "id", "fabricator", "assembler", "source",
                        "approval", "pcb_rules", "stackup", "impedance", "outputs"),
              "fabrication profile")
    if profile.get("schema") != PROFILE_SCHEMA:
        raise ProductionExportError("unsupported fabrication profile schema")
    for field in ("fabricator", "assembler"):
        value = str(profile.get(field, "")).strip()
        if not value or value.lower() in {"unassigned", "unknown", "tbd", "none"}:
            raise ProductionExportError(f"fabrication profile has no contracted {field}")
    source = profile["source"]
    _required(source, ("document_path", "document_sha256", "uri", "revision",
                       "retrieved_utc"), "profile source")
    source_path = _resolve(profile_path.parent, str(source["document_path"]))
    if not source_path.is_file():
        raise ProductionExportError("manufacturer stackup/DFM source document is missing")
    if _sha256(source_path) != source.get("document_sha256"):
        raise ProductionExportError("manufacturer stackup/DFM source document digest changed")
    approval = profile["approval"]
    if approval.get("status") != "approved" or not str(approval.get("reviewer", "")).strip() \
            or not approval.get("approved_utc"):
        raise ProductionExportError("fabrication profile has not been approved by a named reviewer")
    rules = profile["pcb_rules"]
    _required(rules, ("schema_version", "id", "name", "source", "source_revision",
                      "fabricator", "assembler", "limits_mm", "checks", "severity"),
              "profile PCB rules")
    if int(rules.get("schema_version", 0)) != 1:
        raise ProductionExportError("unsupported PCB rules version")
    if rules.get("fabricator") != profile["fabricator"] \
            or rules.get("assembler") != profile["assembler"]:
        raise ProductionExportError("PCB rules manufacturer names do not match the profile")
    limits = rules["limits_mm"]
    _required(limits, ("default_clearance", "min_trace_width", "min_mechanical_drill",
                       "min_annular_ring", "min_drill_to_drill", "min_microvia_drill",
                       "min_microvia_wall", "min_copper_to_edge", "min_copper_to_hole",
                       "min_courtyard_clearance", "min_mask_sliver", "min_silk_width"),
              "manufacturer DFM limits")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in limits.values()):
        raise ProductionExportError("manufacturer DFM limits must be positive millimetres")
    stackup = profile["stackup"]
    _required(stackup, ("finished_thickness_mm", "thickness_tolerance_mm", "surface_finish",
                        "solder_mask_color", "silkscreen_color", "layers"), "stackup")
    layers = stackup["layers"]
    if not isinstance(layers, list) or len(layers) < 3:
        raise ProductionExportError("manufacturer stackup has too few layers")
    if [layer.get("order") for layer in layers] != list(range(len(layers))):
        raise ProductionExportError("manufacturer stackup order is not contiguous")
    copper = [layer for layer in layers if layer.get("kind") == "copper"]
    dielectric = [layer for layer in layers if layer.get("kind") == "dielectric"]
    if len(copper) < 2 or len(dielectric) != len(copper) - 1:
        raise ProductionExportError("stackup must alternate copper and dielectric layers")
    if any(layers[index].get("kind") == layers[index + 1].get("kind")
           for index in range(len(layers) - 1)):
        raise ProductionExportError("stackup copper/dielectric layers do not alternate")
    total = sum(float(layer.get("thickness_mm", 0)) for layer in layers)
    target = float(stackup["finished_thickness_mm"])
    tolerance = float(stackup["thickness_tolerance_mm"])
    if target <= 0 or tolerance <= 0 or abs(total - target) > tolerance + 1e-9:
        raise ProductionExportError(
            f"stackup material thickness {total:.4f} mm is outside finished-board tolerance")
    outputs = profile["outputs"]
    _required(outputs, ("gerber_format", "coordinate_precision", "origin",
                        "separate_pth_npth", "solder_mask_expansion_mm",
                        "paste_reduction_mm", "silkscreen", "pick_place"),
              "manufacturer output contract")
    if outputs.get("gerber_format") != "Gerber X2" or outputs.get("origin") != "absolute" \
            or outputs.get("separate_pth_npth") is not True:
        raise ProductionExportError("production output contract must use X2, absolute origin, and split drills")
    if outputs.get("coordinate_precision") not in (5, 6):
        raise ProductionExportError("Gerber precision must be five or six decimal places")
    if outputs.get("silkscreen") not in ("none", "project-primitives"):
        raise ProductionExportError("unsupported silkscreen policy")
    return {"profile_sha256": _sha256(profile_path), "source_path": source_path,
            "source_sha256": source["document_sha256"], "copper_layers": len(copper)}


def apply_fabrication_profile(project_path: str | Path, profile_path: str | Path,
                              output_path: str | Path) -> dict:
    """Write an explicit new project revision with the contracted profile applied."""
    project_path, profile_path, output_path = map(Path, (project_path, profile_path, output_path))
    document, profile = _json(project_path), _json(profile_path)
    board, wrapped = _board_document(document)
    profile_info = validate_fabrication_profile(profile, profile_path)
    if int(board.get("copper_layers", 0)) != profile_info["copper_layers"]:
        raise ProductionExportError("project copper-layer count does not match manufacturer stackup")
    old_revision = int(board.get("revision", 0))
    board["pcb_rules"] = profile["pcb_rules"]
    board["stackup"] = profile["stackup"]
    board["fabrication_profile"] = {
        "schema": PROFILE_SCHEMA,
        "id": profile["id"],
        "profile_sha256": profile_info["profile_sha256"],
        "source_document_sha256": profile_info["source_sha256"],
        "source_revision": profile["source"]["revision"],
        "fabricator": profile["fabricator"],
        "assembler": profile["assembler"],
    }
    dielectric = next(layer for layer in profile["stackup"]["layers"]
                      if layer["kind"] == "dielectric")
    copper = next(layer for layer in profile["stackup"]["layers"]
                  if layer["kind"] == "copper")
    board["copper_t_mm"] = float(copper["thickness_mm"])
    board["dielectric_h_mm"] = float(dielectric["thickness_mm"])
    if "dielectric_er" in dielectric:
        board["dielectric_er"] = float(dielectric["dielectric_er"])
    if "loss_tangent" in dielectric:
        board["loss_tangent"] = float(dielectric["loss_tangent"])
    board["revision"] = old_revision + 1
    readiness = board.setdefault("release_readiness", {})
    blockers = list(readiness.get("blocking_gates", []))
    readiness["blocking_gates"] = [item for item in blockers
                                    if item != "named fabricator/assembler profile"]
    readiness["manufacturing_release"] = "blocked" if readiness["blocking_gates"] else "pending-verification"
    if wrapped:
        document.setdefault("rationale", []).append({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "intent": f"Apply contracted fabrication profile {profile['id']}",
            "alternatives": [], "gate": "manual",
            "source": f"human:{profile['approval']['reviewer']}",
            "drove": {"mechanical": [], "electronic": [
                "manufacturer DFM rules", "physical stackup", "CAM output conventions"]},
        })
    if output_path.resolve() == project_path.resolve():
        backup = project_path.with_suffix(project_path.suffix + f".revision-{old_revision}.bak")
        if backup.exists():
            raise ProductionExportError(f"explicit migration backup already exists: {backup}")
        shutil.copy2(project_path, backup)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    return {"project": str(output_path.resolve()), "sha256": _sha256(output_path),
            "revision": board["revision"], "profile_sha256": profile_info["profile_sha256"]}


def _validate_project_binding(project_path: Path, document: dict, board: dict,
                              profile: dict, profile_info: dict,
                              verification_path: Path, verification: dict) -> None:
    binding = board.get("fabrication_profile") or {}
    expected = {
        "schema": PROFILE_SCHEMA, "id": profile["id"],
        "profile_sha256": profile_info["profile_sha256"],
        "source_document_sha256": profile_info["source_sha256"],
        "source_revision": profile["source"]["revision"],
        "fabricator": profile["fabricator"], "assembler": profile["assembler"],
    }
    if binding != expected:
        raise ProductionExportError("contracted fabrication profile is not applied to the project revision")
    if _canonical(board.get("pcb_rules")) != _canonical(profile["pcb_rules"]):
        raise ProductionExportError("project DFM rules differ from the contracted profile")
    if _canonical(board.get("stackup")) != _canonical(profile["stackup"]):
        raise ProductionExportError("project stackup differs from the contracted profile")
    if int(board.get("copper_layers", 0)) != profile_info["copper_layers"]:
        raise ProductionExportError("project copper-layer count differs from the stackup")
    if verification.get("schema") != "design-studio.verification/1" \
            or verification.get("overall_status") != "pass":
        raise ProductionExportError("a passing unified verification report is required")
    vb = verification.get("project") or {}
    exact_project_hash = _sha256(project_path)
    exact = vb.get("file_sha256") == exact_project_hash
    if document.get("format") == "design-studio.project/3":
        # New verifiers bind project/3 directly.  Legacy reports are rejected;
        # they cannot prove the wrapper and newly applied profile were checked.
        exact = exact and vb.get("document_id") == board.get("document_id")
    if not exact or int(vb.get("revision", -1)) != int(board.get("revision", 0)):
        raise ProductionExportError("verification is stale for the exact authoritative project bytes")
    for name, category in (verification.get("categories") or {}).items():
        if category.get("required") and category.get("status") != "pass":
            raise ProductionExportError(f"required verification category {name} is not pass")
    constraint = verification.get("constraint_profile") or {}
    for key in ("id", "source", "source_revision", "fabricator", "assembler"):
        if constraint.get(key) != profile["pcb_rules"].get(key):
            raise ProductionExportError(f"verification did not use contracted profile field {key}")
    project_dir = project_path.resolve().parent
    for footprint in board.get("footprints", []):
        ref = str(footprint.get("ref", "?"))
        compact = footprint.get("bound_component") or {}
        record_uri = str(compact.get("record_uri", "")).strip()
        if not record_uri:
            raise ProductionExportError(f"{ref}: bound-component record_uri is missing")
        record_path = _resolve(project_dir, record_uri)
        try:
            record = _json(record_path)
            validate_binding(record, require_complete=True, verify_asset=True,
                             library_root=record_path.parent)
        except (ProductionExportError, BindingError) as exc:
            raise ProductionExportError(
                f"{ref}: full bound-component record is invalid: {exc}") from exc
        if record.get("binding_id") != compact.get("binding_id") \
                or record.get("binding_digest") != compact.get("binding_digest"):
            raise ProductionExportError(
                f"{ref}: bound-component record identity or digest differs from the project")
        if _normalize_mpn((record.get("component") or {}).get("mpn")) \
                != _normalize_mpn(footprint.get("mpn")):
            raise ProductionExportError(
                f"{ref}: bound-component record MPN differs from the footprint")
        compact_model = compact.get("model_3d") or {}
        record_model = record.get("model_3d") or {}
        if record_model.get("sha256") != compact_model.get("sha256") \
                or record_model.get("model_to_footprint") \
                != compact_model.get("model_to_footprint"):
            raise ProductionExportError(
                f"{ref}: STEP digest or transform differs from its reviewed component record")
    if _sha256(verification_path) == "0" * 64:  # keeps static analyzers aware of exact evidence use
        raise AssertionError("unreachable digest")


def _layer_names(count: int) -> list[str]:
    if count < 2:
        raise ProductionExportError("board needs at least two copper layers")
    return ["F_Cu", *(f"In{index}_Cu" for index in range(1, count - 1)), "B_Cu"]


def _coord(value: float, precision: int) -> str:
    scaled = int(round(value * (10 ** precision)))
    return ("-" if scaled < 0 else "") + str(abs(scaled))


def _x2_header(project_id: str, revision: int, file_function: str,
               precision: int, creation_utc: str) -> list[str]:
    safe_id = project_id.replace(",", "_").replace("*", "_").replace("%", "_")
    return [
        f"G04 {GENERATOR} {GENERATOR_VERSION}*",
        f"%TF.GenerationSoftware,DesignStudio,ProductionCAM,{GENERATOR_VERSION}*%",
        f"%TF.CreationDate,{creation_utc}*%",
        f"%TF.ProjectId,{safe_id},{safe_id},rev-{revision}*%",
        f"%TF.FileFunction,{file_function}*%",
        "%TF.FilePolarity,Positive*%",
        f"%FSLAX4{precision}Y4{precision}*%",
        "%MOMM*%", "%LPD*%",
    ]


def _attribute(value: str) -> str:
    return str(value).replace("%", "_").replace("*", "_").replace(",", "_")[:120]


def _transform_point(fp: dict, x: float, y: float) -> tuple[float, float]:
    angle = math.radians(float(fp.get("rot_deg", 0)))
    c, s = math.cos(angle), math.sin(angle)
    if int(fp.get("side", 0)) == 1:
        return (float(fp["x_mm"]) + c * x + s * y,
                float(fp["y_mm"]) + s * x - c * y)
    return (float(fp["x_mm"]) + c * x - s * y,
            float(fp["y_mm"]) + s * x + c * y)


def _ellipse_points(width: float, height: float, count: int = 64) -> list[tuple[float, float]]:
    return [(width * math.cos(2 * math.pi * index / count) / 2,
             height * math.sin(2 * math.pi * index / count) / 2)
            for index in range(count)]


def _rounded_rect_points(width: float, height: float, radius: float) -> list[tuple[float, float]]:
    radius = max(0.0, min(radius, width / 2, height / 2))
    if radius <= 1e-9:
        return [(-width / 2, -height / 2), (width / 2, -height / 2),
                (width / 2, height / 2), (-width / 2, height / 2)]
    result = []
    for cx, cy, start in ((width / 2 - radius, -height / 2 + radius, -90),
                          (width / 2 - radius, height / 2 - radius, 0),
                          (-width / 2 + radius, height / 2 - radius, 90),
                          (-width / 2 + radius, -height / 2 + radius, 180)):
        for step in range(9):
            angle = math.radians(start + step * 90 / 8)
            result.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return result


def _pad_polygon(fp: dict, pad: dict, expansion: float = 0.0,
                 reduction: float = 0.0) -> list[tuple[float, float]]:
    width = float(pad.get("w_mm", 0)) + 2 * expansion - 2 * reduction
    height = float(pad.get("h_mm", 0)) + 2 * expansion - 2 * reduction
    if width <= 0 or height <= 0:
        raise ProductionExportError(f"{fp.get('ref')}.{pad.get('name')}: non-positive plotted pad size")
    shape = pad.get("shape", "rect")
    if shape in ("circle", "oval"):
        local = _ellipse_points(width, height)
    elif shape == "roundrect":
        local = _rounded_rect_points(width, height,
                                     float(pad.get("corner_r_mm", 0)) + expansion - reduction)
    elif shape == "rect":
        local = _rounded_rect_points(width, height, 0)
    else:
        raise ProductionExportError(f"unsupported production pad shape: {shape}")
    pad_angle = math.radians(float(pad.get("rot_deg", pad.get("rotation_deg", 0))))
    c, s = math.cos(pad_angle), math.sin(pad_angle)
    px, py = float(pad.get("x_mm", 0)), float(pad.get("y_mm", 0))
    return [_transform_point(fp, px + c * x - s * y, py + s * x + c * y)
            for x, y in local]


def _region(lines: list[str], points: list[tuple[float, float]], precision: int) -> None:
    if len(points) < 3:
        raise ProductionExportError("Gerber region has fewer than three points")
    lines.append("G36*")
    lines.append(f"X{_coord(points[0][0], precision)}Y{_coord(points[0][1], precision)}D02*")
    for x, y in points[1:] + [points[0]]:
        lines.append(f"X{_coord(x, precision)}Y{_coord(y, precision)}D01*")
    lines.append("G37*")


def _write_gerber(path: Path, header: list[str], traces: list[dict],
                  pad_regions: list[tuple[dict, dict, list[tuple[float, float]]]],
                  via_flashes: list[dict], polygons: list[list[tuple[float, float]]],
                  precision: int) -> dict:
    widths = sorted({round(float(trace["w_mm"]), 6) for trace in traces}
                    | {round(float(via["dia_mm"]), 6) for via in via_flashes})
    aperture = {width: index + 11 for index, width in enumerate(widths)}
    lines = list(header)
    lines.append("%ADD10C,0.010000*%")
    for width, code in aperture.items():
        lines.append(f"%ADD{code}C,{width:.6f}*%")
    current = None
    for trace in sorted(traces, key=lambda item: (int(item.get("net", -1)),
                                                   item.get("uuid", ""))):
        width = round(float(trace["w_mm"]), 6)
        if current != aperture[width]:
            current = aperture[width]; lines.append(f"D{current}*")
        net_name = _attribute(trace.get("net_name", "N/C"))
        lines.append(f"%TO.N,{net_name}*%")
        lines.append(f"X{_coord(float(trace['ax_mm']), precision)}Y{_coord(float(trace['ay_mm']), precision)}D02*")
        lines.append(f"X{_coord(float(trace['bx_mm']), precision)}Y{_coord(float(trace['by_mm']), precision)}D01*")
        lines.append("%TD*%")
    for via in sorted(via_flashes, key=lambda item: item.get("uuid", "")):
        width = round(float(via["dia_mm"]), 6)
        if current != aperture[width]:
            current = aperture[width]; lines.append(f"D{current}*")
        lines.append(f"%TO.N,{_attribute(via.get('net_name', 'N/C'))}*%")
        lines.append(f"X{_coord(float(via['x_mm']), precision)}Y{_coord(float(via['y_mm']), precision)}D03*")
        lines.append("%TD*%")
    lines.append("D10*")
    for fp, pad, points in sorted(pad_regions,
                                   key=lambda item: (item[0].get("ref", ""),
                                                     str(item[1].get("name", "")))):
        lines.append(f"%TO.C,{_attribute(fp.get('ref', ''))}*%")
        lines.append(f"%TO.P,{_attribute(pad.get('name', ''))}*%")
        lines.append(f"%TO.N,{_attribute(pad.get('net_name', 'N/C'))}*%")
        _region(lines, points, precision)
        lines.append("%TD*%")
    for points in polygons:
        _region(lines, points, precision)
    lines.extend(["%TD*%", "M02*"])
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {"traces": len(traces), "pads": len(pad_regions),
            "vias": len(via_flashes), "regions": len(polygons)}


def _outline_gerber(path: Path, board: dict, header: list[str], precision: int) -> dict:
    outline = board.get("board_outline_pts") or []
    if len(outline) < 3:
        raise ProductionExportError("board outline is missing")
    lines = list(header) + ["%ADD10C,0.100000*%", "D10*"]
    paths = [outline, *(board.get("board_cutouts") or [])]
    for points in paths:
        lines.append(f"X{_coord(float(points[0][0]), precision)}Y{_coord(float(points[0][1]), precision)}D02*")
        for point in points[1:] + [points[0]]:
            lines.append(f"X{_coord(float(point[0]), precision)}Y{_coord(float(point[1]), precision)}D01*")
    lines.append("M02*")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {"contours": len(paths), "segments": sum(len(path_) for path_ in paths)}


def _net_names(board: dict) -> dict[int, str]:
    table = board.get("net_table") or {}
    if isinstance(table, list):
        return {int(item.get("id", index)): str(item.get("name", f"Net-{index}"))
                for index, item in enumerate(table)}
    return {int(key): str(value.get("name", f"Net-{key}")) for key, value in table.items()}


def _pads(board: dict, names: dict[int, str]) -> list[tuple[dict, dict, float, float]]:
    result = []
    for fp in board.get("footprints", []):
        for pad in fp.get("pads", []):
            copy = dict(pad)
            net = int(copy.get("net", -1))
            copy["net_name"] = names.get(net, "N/C") if net >= 0 else "N/C"
            x, y = _transform_point(fp, float(copy.get("x_mm", 0)), float(copy.get("y_mm", 0)))
            result.append((fp, copy, x, y))
    return result


def _plated(pad: dict) -> bool:
    if "plated" in pad:
        return bool(pad["plated"])
    return int(pad.get("net", -1)) >= 0


def _drill_hits(board: dict, pads: list[tuple[dict, dict, float, float]]) -> tuple[list[dict], list[dict]]:
    pth = [{"x": float(via["x_mm"]), "y": float(via["y_mm"]),
            "diameter": float(via["drill_mm"]), "kind": "via", "id": via.get("uuid", "")}
           for via in board.get("vias", [])]
    npth = []
    for fp, pad, x, y in pads:
        if not pad.get("th") or float(pad.get("drill_mm", 0)) <= 0:
            continue
        hit = {"x": x, "y": y, "diameter": float(pad["drill_mm"]),
               "kind": "component", "id": f"{fp.get('ref')}.{pad.get('name')}"}
        (pth if _plated(pad) else npth).append(hit)
    key = lambda hit: (round(hit["x"], 6), round(hit["y"], 6),
                       round(hit["diameter"], 6), hit["kind"], hit["id"])
    return sorted(pth, key=key), sorted(npth, key=key)


def _write_excellon(path: Path, hits: list[dict], plating: str) -> dict:
    groups: dict[float, list[dict]] = defaultdict(list)
    for hit in hits:
        groups[round(float(hit["diameter"]), 6)].append(hit)
    lines = ["M48", f";FILE_FORMAT=4:6", f";TYPE={plating}", "METRIC,LZ"]
    tools = {diameter: index + 1 for index, diameter in enumerate(sorted(groups))}
    for diameter, index in tools.items():
        lines.append(f"T{index:02d}C{diameter:.6f}")
    lines.extend(["%", "G90", "G05"])
    for diameter, index in tools.items():
        lines.append(f"T{index:02d}")
        for hit in groups[diameter]:
            lines.append(f"X{hit['x']:.6f}Y{hit['y']:.6f}")
    lines.extend(["T00", "M30"])
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {"plating": plating, "hits": len(hits), "tools": len(tools)}


def _write_bom(path: Path, board: dict) -> dict:
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for fp in board.get("footprints", []):
        key = (str(fp.get("manufacturer", "")), str(fp.get("mpn", "")),
               str(fp.get("lib", "")), str(fp.get("value", "")),
               "DNP" if fp.get("dnp") else "FIT")
        if not key[0] or not key[1]:
            raise ProductionExportError(f"{fp.get('ref')}: manufacturer/MPN missing from BOM")
        groups[key].append(str(fp.get("ref", "")))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Item", "MPN", "Manufacturer", "Qty", "References",
                         "Footprint", "Value", "Populate"])
        for index, (key, refs) in enumerate(sorted(groups.items()), 1):
            manufacturer, mpn, footprint, value, populate = key
            writer.writerow([index, mpn, manufacturer, len(refs), " ".join(sorted(refs)),
                             footprint, value, populate])
    return {"line_items": len(groups), "components": sum(map(len, groups.values()))}


def _is_through_hole(fp: dict) -> bool:
    pads = fp.get("pads") or []
    return bool(pads) and all(bool(pad.get("th")) for pad in pads)


def _write_pick_place(path: Path, board: dict, contract: dict) -> dict:
    include_th = bool(contract["include_through_hole"])
    corrections = contract.get("rotation_corrections_deg") or {}
    rows = []
    for fp in board.get("footprints", []):
        if fp.get("dnp") or (_is_through_hole(fp) and not include_th):
            continue
        rotation = float(fp.get("rot_deg", 0)) + float(corrections.get(fp.get("mpn", ""), 0))
        side = "Bottom" if int(fp.get("side", 0)) == 1 else "Top"
        if side == "Bottom" and contract["bottom_rotation"] == "viewed-from-bottom":
            rotation = -rotation
        rows.append([fp.get("ref", ""), fp.get("mpn", ""), float(fp.get("x_mm", 0)),
                     float(fp.get("y_mm", 0)), rotation % 360, side,
                     "THT" if _is_through_hole(fp) else "SMD"])
    rows.sort(key=lambda row: row[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Ref", "MPN", "X_mm", "Y_mm", "Rotation_deg", "Side", "Mount"])
        for row in rows:
            writer.writerow([row[0], row[1], f"{row[2]:.6f}", f"{row[3]:.6f}",
                             f"{row[4]:.3f}", row[5], row[6]])
    return {"placements": len(rows), "include_through_hole": include_th}


def _ipc_field(value: str, length: int) -> str:
    cleaned = "".join(char if 32 <= ord(char) < 127 else "_" for char in str(value))
    return cleaned[:length].ljust(length)


def _ipc_coord(mm: float) -> str:
    # IPC-D-356 CUST 0 coordinates are ten-thousandths of an inch.
    value = int(round(mm / 25.4 * 10000))
    return ("+" if value >= 0 else "-") + f"{abs(value):06d}"


def _ipc_size(mm: float) -> str:
    return f"{max(0, min(9999, int(round(mm / 25.4 * 10000)))):04d}"


def _ipc_record(net: str, ref: str, pin: str, x: float, y: float,
                width: float, height: float, access: str, hole: float) -> str:
    # Fixed-width IPC-D-356A access record.  D denotes a drilled terminal and A
    # a surface access point.  The Axx access code identifies both/top/bottom.
    kind = "D" if hole > 0 else "A"
    rotation = "R000"
    return ("317" + _ipc_field(net, 14) + "   " + _ipc_field(ref, 6) + "-"
            + _ipc_field(pin, 4) + kind + access
            + "X" + _ipc_coord(x) + "Y" + _ipc_coord(y)
            + "X" + _ipc_size(width) + "Y" + _ipc_size(height) + rotation).ljust(80)


def _write_ipc356(path: Path, board: dict,
                  pads: list[tuple[dict, dict, float, float]], names: dict[int, str]) -> dict:
    project_id = str(board.get("document_id", "designstudio-board"))
    lines = [f"C  IPC-D-356A bare-board netlist generated by {GENERATOR} {GENERATOR_VERSION}",
             f"P  JOB {_attribute(project_id)}", "P  CODE 00", "P  UNITS CUST 0",
             "P  DIM N", "P  VER IPC-D-356A", "P  IMAGE PRIMARY"]
    records = 0
    for fp, pad, x, y in sorted(pads, key=lambda item: (item[0].get("ref", ""),
                                                        str(item[1].get("name", "")))):
        net = names.get(int(pad.get("net", -1)), "N/C")
        if pad.get("th"):
            access = "A00"
        else:
            access = "A02" if int(fp.get("side", 0)) == 1 else "A01"
        lines.append(_ipc_record(net, str(fp.get("ref", "")), str(pad.get("name", "")),
                                 x, y, float(pad.get("w_mm", 0)),
                                 float(pad.get("h_mm", 0)), access,
                                 float(pad.get("drill_mm", 0))))
        records += 1
    for index, via in enumerate(sorted(board.get("vias", []), key=lambda item: item.get("uuid", "")), 1):
        net = names.get(int(via.get("net", -1)), "N/C")
        lines.append(_ipc_record(net, "VIA", str(index), float(via["x_mm"]),
                                 float(via["y_mm"]), float(via["dia_mm"]),
                                 float(via["dia_mm"]), "A00", float(via["drill_mm"])))
        records += 1
    lines.append("999")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {"test_records": records, "nets": len(names)}


def _multiply(a: list[float], b: list[float]) -> list[float]:
    return [sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
            for row in range(4) for col in range(4)]


def _component_transform(fp: dict, local: list[float], thickness: float) -> list[float]:
    angle = math.radians(float(fp.get("rot_deg", 0)))
    c, s = math.cos(angle), math.sin(angle)
    if int(fp.get("side", 0)) == 1:
        placed = [c, s, 0, float(fp["x_mm"]), s, -c, 0, float(fp["y_mm"]),
                  0, 0, -1, 0, 0, 0, 0, 1]
    else:
        placed = [c, -s, 0, float(fp["x_mm"]), s, c, 0, float(fp["y_mm"]),
                  0, 0, 1, thickness, 0, 0, 0, 1]
    return _multiply(placed, local)


def _q(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _normalize_mpn(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _write_board_step(job_path: Path, destination: Path, board: dict, profile: dict,
                      pads: list[tuple[dict, dict, float, float]], pth: list[dict],
                      npth: list[dict], project_dir: Path, board_step_binary: Path) -> dict:
    thickness = float(profile["stackup"]["finished_thickness_mm"])
    outline = board.get("board_outline_pts") or []
    lines = ["# DesignStudio board STEP job v1",
             "BOARD " + f"{thickness:.9f} {len(outline)} "
             + " ".join(f"{float(x):.9f} {float(y):.9f}" for x, y in outline)]
    for cutout in board.get("board_cutouts") or []:
        lines.append("CUTOUT " + str(len(cutout)) + " "
                     + " ".join(f"{float(x):.9f} {float(y):.9f}" for x, y in cutout))
    unique_holes = {}
    for hit in pth + npth:
        key = (round(hit["x"], 6), round(hit["y"], 6), round(hit["diameter"], 6))
        unique_holes[key] = hit
    for x, y, diameter in sorted(unique_holes):
        lines.append(f"HOLE {x:.9f} {y:.9f} {diameter:.9f}")
    component_count = 0
    external_components = []
    for fp in sorted(board.get("footprints", []), key=lambda item: item.get("ref", "")):
        binding = fp.get("bound_component") or {}
        model = binding.get("model_3d") or {}
        if binding.get("schema") != "design-studio.bound-component-ref/1" \
                or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("binding_digest", ""))) \
                or model.get("format") != "step":
            raise ProductionExportError(f"{fp.get('ref')}: approved STEP binding is missing")
        if not _normalize_mpn(fp.get("mpn")) \
                or _normalize_mpn(model.get("claimed_mpn")) != _normalize_mpn(fp.get("mpn")):
            raise ProductionExportError(f"{fp.get('ref')}: STEP binding MPN does not match the BOM")
        asset = _resolve(project_dir, str(model.get("asset_uri", "")))
        if not asset.is_file() or _sha256(asset) != model.get("sha256"):
            raise ProductionExportError(f"{fp.get('ref')}: approved STEP asset is missing or changed")
        local = model.get("model_to_footprint")
        if not isinstance(local, list) or len(local) != 16:
            raise ProductionExportError(f"{fp.get('ref')}: invalid model-to-footprint transform")
        # Flexible sensors, displays and other enclosure-mounted parts can have
        # an electrically bound PCB termination without being rigid members of
        # the PCB assembly.  Their approved CAD remains digest-checked above,
        # but belongs in the enclosure assembly rather than being falsely
        # fused to the board STEP at the footprint origin.
        if model.get("assembly_role") == "enclosure-mounted-external":
            external_components.append(str(fp.get("ref", "")))
            continue
        world = _component_transform(fp, [float(value) for value in local], thickness)
        values = [world[row * 4 + col] for row in range(3) for col in range(4)]
        lines.append("COMPONENT " + _q(str(fp.get("ref", ""))) + " " + _q(str(asset))
                     + " " + " ".join(f"{value:.12g}" for value in values))
        component_count += 1
    job_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run([str(board_step_binary), str(job_path), str(destination)],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=300)
    if result.returncode != 0:
        raise ProductionExportError(f"AP242 board STEP export failed: {result.stdout.strip()}")
    try:
        report_line = next(line for line in reversed(result.stdout.splitlines())
                           if line.lstrip().startswith("{"))
        report = json.loads(report_line)
    except Exception as exc:
        raise ProductionExportError("AP242 board STEP exporter returned invalid evidence") from exc
    if not report.get("ok") or report.get("step_schema") != "AP242" \
            or int(report.get("components", -1)) != component_count:
        raise ProductionExportError("AP242 board STEP evidence is incomplete")
    report["external_components_excluded"] = external_components
    return report


def _deterministic_zip(path: Path, files: list[Path], base: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(files, key=lambda value: value.name):
            info = zipfile.ZipInfo(str(item.relative_to(base)).replace(os.sep, "/"),
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, item.read_bytes())


def _artifact(path: Path, role: str, base: Path, metrics: dict | None = None) -> dict:
    result = {"role": role, "path": str(path.relative_to(base)), "sha256": _sha256(path),
              "bytes": path.stat().st_size, "generator": GENERATOR,
              "generator_version": GENERATOR_VERSION}
    if metrics is not None:
        result["metrics"] = metrics
    return result


def _validate_outputs(output: Path, gerbers: list[Path], archive: Path,
                      pth_path: Path, npth_path: Path, pth: list[dict], npth: list[dict],
                      ipc_path: Path, ipc_records: int, step_path: Path) -> None:
    for path in gerbers:
        if path.suffix == ".gbrjob":
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ProductionExportError("generated Gerber job file is invalid") from exc
            if not job.get("FilesAttributes") or not job.get("MaterialStackup"):
                raise ProductionExportError("generated Gerber job file is incomplete")
            continue
        text = path.read_text(encoding="ascii")
        if "%TF.FileFunction," not in text or "%FSLAX" not in text or not text.rstrip().endswith("M02*"):
            raise ProductionExportError(f"generated Gerber failed syntax checks: {path.name}")
    with zipfile.ZipFile(archive) as handle:
        if sorted(handle.namelist()) != sorted(str(path.relative_to(output)).replace(os.sep, "/")
                                               for path in gerbers):
            raise ProductionExportError("Gerber archive content differs from generated layers")
    for path, expected in ((pth_path, len(pth)), (npth_path, len(npth))):
        lines = path.read_text(encoding="ascii").splitlines()
        hits = sum(line.startswith("X") and "Y" in line for line in lines)
        if not lines or lines[0] != "M48" or lines[-1] != "M30" or hits != expected:
            raise ProductionExportError(f"generated drill file failed semantic checks: {path.name}")
    ipc_lines = ipc_path.read_text(encoding="ascii").splitlines()
    if ipc_lines[-1] != "999" or sum(line.startswith("317") for line in ipc_lines) != ipc_records:
        raise ProductionExportError("IPC-D-356A record count is invalid")
    if not step_path.read_bytes()[:128].upper().startswith(b"ISO-10303-21"):
        raise ProductionExportError("board STEP has no ISO-10303-21 signature")


def export_package(project_path: str | Path, profile_path: str | Path,
                   verification_path: str | Path, output_dir: str | Path,
                   board_step_binary: str | Path) -> dict:
    project_path, profile_path, verification_path, output = map(
        Path, (project_path, profile_path, verification_path, output_dir))
    board_step_binary = Path(board_step_binary)
    document, profile, verification = (_json(project_path), _json(profile_path),
                                        _json(verification_path))
    board, _ = _board_document(document)
    profile_info = validate_fabrication_profile(profile, profile_path)
    _validate_project_binding(project_path, document, board, profile, profile_info,
                              verification_path, verification)
    if not board_step_binary.is_file() or not os.access(board_step_binary, os.X_OK):
        raise ProductionExportError("approved AP242 board STEP exporter binary is unavailable")
    if output.exists() and any(output.iterdir()):
        raise ProductionExportError("production output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    cam = output / "cam"; assembly = output / "assembly"; mechanical = output / "mechanical"
    for directory in (cam, assembly, mechanical): directory.mkdir()
    project_id = str(board.get("document_id", project_path.stem))
    revision = int(board.get("revision", 0))
    precision = int(profile["outputs"]["coordinate_precision"])
    creation = str(profile["approval"]["approved_utc"])
    layer_names = _layer_names(int(board["copper_layers"]))
    names = _net_names(board)
    pads = _pads(board, names)
    traces_by_layer: dict[int, list[dict]] = defaultdict(list)
    for trace in board.get("traces", []):
        copied = dict(trace)
        copied["net_name"] = names.get(int(trace.get("net", -1)), "N/C")
        traces_by_layer[int(trace.get("layer", 0))].append(copied)
    vias_by_layer: dict[int, list[dict]] = defaultdict(list)
    for via in board.get("vias", []):
        copied = dict(via)
        copied["net_name"] = names.get(int(via.get("net", -1)), "N/C")
        for layer in range(int(via.get("from", 0)), int(via.get("to", 0)) + 1):
            vias_by_layer[layer].append(copied)
    gerbers: list[Path] = []; layer_metrics = {}
    for index, name in enumerate(layer_names):
        position = "Top" if index == 0 else "Bot" if index == len(layer_names) - 1 else "Inr"
        file_function = f"Copper,L{index + 1},{position}"
        regions = [(fp, pad, _pad_polygon(fp, pad)) for fp, pad, _, _ in pads
                   if pad.get("th") or (int(fp.get("side", 0)) == (1 if position == "Bot" else 0)
                                        and position in ("Top", "Bot"))]
        path = cam / f"{project_path.stem}-{name}.gbr"
        layer_metrics[name] = _write_gerber(
            path, _x2_header(project_id, revision, file_function, precision, creation),
            traces_by_layer[index], regions, vias_by_layer[index], [], precision)
        gerbers.append(path)
    mask_expansion = float(profile["outputs"]["solder_mask_expansion_mm"])
    paste_reduction = float(profile["outputs"]["paste_reduction_mm"])
    for side, label in ((0, "F"), (1, "B")):
        mask_regions = [(fp, pad, _pad_polygon(fp, pad, expansion=mask_expansion))
                        for fp, pad, _, _ in pads if pad.get("th") or int(fp.get("side", 0)) == side]
        mask_path = cam / f"{project_path.stem}-{label}_Mask.gbr"
        layer_metrics[f"{label}_Mask"] = _write_gerber(
            mask_path, _x2_header(project_id, revision,
                                  f"Soldermask,{'Top' if side == 0 else 'Bot'}",
                                  precision, creation), [], mask_regions, [], [], precision)
        gerbers.append(mask_path)
        paste_regions = [(fp, pad, _pad_polygon(fp, pad, reduction=paste_reduction))
                         for fp, pad, _, _ in pads
                         if not pad.get("th") and int(fp.get("side", 0)) == side]
        paste_path = cam / f"{project_path.stem}-{label}_Paste.gbr"
        layer_metrics[f"{label}_Paste"] = _write_gerber(
            paste_path, _x2_header(project_id, revision,
                                   f"Paste,{'Top' if side == 0 else 'Bot'}",
                                   precision, creation), [], paste_regions, [], [], precision)
        gerbers.append(paste_path)
        silk_path = cam / f"{project_path.stem}-{label}_Silkscreen.gbr"
        silk_policy = profile["outputs"]["silkscreen"]
        primitives = board.get("silkscreen_primitives") or []
        if silk_policy == "project-primitives" and not primitives:
            raise ProductionExportError("profile requires project silkscreen primitives, but none exist")
        silk_polygons = [[(float(point[0]), float(point[1])) for point in item["pts"]]
                         for item in primitives if int(item.get("side", 0)) == side]
        layer_metrics[f"{label}_Silkscreen"] = _write_gerber(
            silk_path, _x2_header(project_id, revision,
                                  f"Legend,{'Top' if side == 0 else 'Bot'}",
                                  precision, creation), [], [], [], silk_polygons, precision)
        gerbers.append(silk_path)
    edge_path = cam / f"{project_path.stem}-Edge_Cuts.gbr"
    layer_metrics["Edge_Cuts"] = _outline_gerber(
        edge_path, board, _x2_header(project_id, revision, "Profile,NP", precision, creation),
        precision)
    gerbers.append(edge_path)
    job = {
        "Header": {"GenerationSoftware": {"Vendor": "DesignStudio",
                    "Application": "ProductionCAM", "Version": GENERATOR_VERSION},
                   "CreationDate": creation, "ProjectId": project_id, "Revision": revision},
        "GeneralSpecs": {"ProjectId": project_id,
                         "Size": {"X": float(board.get("board_width_mm", 0)),
                                  "Y": float(board.get("board_height_mm", 0))},
                         "LayerNumber": len(layer_names),
                         "BoardThickness": float(profile["stackup"]["finished_thickness_mm"]),
                         "Finish": profile["stackup"]["surface_finish"]},
        "FilesAttributes": [{"Path": path.name,
                              "FileFunction": next(line.split(",", 1)[1].split("*", 1)[0]
                                                   for line in path.read_text().splitlines()
                                                   if line.startswith("%TF.FileFunction,"))}
                             for path in gerbers],
        "MaterialStackup": profile["stackup"]["layers"],
    }
    job_path = cam / f"{project_path.stem}.gbrjob"
    job_path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gerbers.append(job_path)
    archive = output / f"{project_path.stem}-gerbers.zip"
    _deterministic_zip(archive, gerbers, output)
    pth, npth = _drill_hits(board, pads)
    pth_path = cam / f"{project_path.stem}-PTH.drl"
    npth_path = cam / f"{project_path.stem}-NPTH.drl"
    pth_metrics = _write_excellon(pth_path, pth, "PLATED")
    npth_metrics = _write_excellon(npth_path, npth, "NON_PLATED")
    drill_report = cam / f"{project_path.stem}-drill-report.json"
    drill_report.write_text(json.dumps({"schema": "design-studio.drill-report/1",
                                        "pth": pth_metrics, "npth": npth_metrics},
                                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bom_path = assembly / f"{project_path.stem}-BOM.csv"
    bom_metrics = _write_bom(bom_path, board)
    pnp_path = assembly / f"{project_path.stem}-pick-and-place.csv"
    pnp_metrics = _write_pick_place(pnp_path, board, profile["outputs"]["pick_place"])
    ipc_path = cam / f"{project_path.stem}.ipc"
    ipc_metrics = _write_ipc356(ipc_path, board, pads, names)
    step_path = mechanical / f"{project_path.stem}-board.step"
    step_job = mechanical / ".board-step-job.txt"
    step_metrics = _write_board_step(step_job, step_path, board, profile, pads, pth, npth,
                                     project_path.resolve().parent, board_step_binary.resolve())
    step_job.unlink()
    _validate_outputs(output, gerbers, archive, pth_path, npth_path, pth, npth,
                      ipc_path, ipc_metrics["test_records"], step_path)
    artifacts = [
        _artifact(archive, "gerber_archive", output, {"files": len(gerbers),
                                                      "layers": layer_metrics}),
        _artifact(pth_path, "drill", output, pth_metrics),
        _artifact(npth_path, "npth_drill", output, npth_metrics),
        _artifact(drill_report, "drill_report", output),
        _artifact(bom_path, "bom", output, bom_metrics),
        _artifact(pnp_path, "pick_place", output, pnp_metrics),
        _artifact(ipc_path, "ipc_netlist", output, ipc_metrics),
        _artifact(step_path, "board_step", output, step_metrics),
    ]
    manifest = {
        "schema": PACKAGE_SCHEMA, "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "project": {"path": str(project_path.resolve()), "sha256": _sha256(project_path),
                    "document_id": board.get("document_id"), "revision": revision,
                    "format": document.get("format", "design-studio.project/2")},
        "verification": {"path": str(verification_path.resolve()),
                         "sha256": _sha256(verification_path)},
        "fabrication_profile": {"path": str(profile_path.resolve()),
                                "sha256": profile_info["profile_sha256"],
                                "source_document_sha256": profile_info["source_sha256"],
                                "fabricator": profile["fabricator"],
                                "assembler": profile["assembler"]},
        "artifacts": artifacts,
        "validation": {"status": "pass", "gerber_files": len(gerbers),
                       "pth_hits": len(pth), "npth_hits": len(npth),
                       "ipc_test_records": ipc_metrics["test_records"],
                       "step_schema": step_metrics["step_schema"]},
    }
    manifest_path = output / "production-package-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    checksums = output / "SHA256SUMS"
    checksum_paths = [archive, pth_path, npth_path, drill_report, bom_path, pnp_path,
                      ipc_path, step_path, manifest_path]
    checksums.write_text("".join(f"{_sha256(path)}  {path.relative_to(output)}\n"
                                 for path in sorted(checksum_paths,
                                                    key=lambda value: str(value.relative_to(output)))),
                         encoding="ascii")
    return manifest
