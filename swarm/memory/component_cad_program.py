"""Typed component CAD programs, deterministic execution, and publication.

The model-authored component/2 record is normalized into a small auditable
command language.  Footprint commands are checked in Python; solid commands are
executed by the Open CASCADE ``designstudio-component-cad`` tool.  Preview
artifacts are isolated from the authoritative component libraries until an
explicit publication call.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

from swarm.runtime_paths import component_library_dir, data_root


PROGRAM_SCHEMA = "design-studio.component-cad-program/1"
PREVIEW_SCHEMA = "design-studio.component-preview/1"
PROGRAM_SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "schemas" / "component-cad-program-v1.schema.json"


class ComponentCadError(RuntimeError):
    def __init__(self, issues: str | list[str]):
        self.issues = issues if isinstance(issues, list) else [issues]
        super().__init__("; ".join(self.issues))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value) or "component"


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComponentCadError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ComponentCadError(f"{name} must be finite")
    return result


def _inspection_pages(inspection: dict[str, Any], roles: set[str] | None = None) -> list[int]:
    return sorted({int(region["page"]) for region in inspection.get("regions") or []
                   if roles is None or region.get("role") in roles})


def _provenance(datasheet_sha: str, pages: list[int], label: str) -> list[str]:
    return [f"datasheet:{datasheet_sha}:page:{page}:{label}" for page in pages]


def _courtyard(footprint: dict[str, Any]) -> list[list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for index, pad in enumerate(footprint.get("pads") or []):
        x = _finite(pad.get("x_mm"), f"pad {index + 1} x")
        y = _finite(pad.get("y_mm"), f"pad {index + 1} y")
        width = _finite(pad.get("width_mm"), f"pad {index + 1} width")
        height = _finite(pad.get("height_mm"), f"pad {index + 1} height")
        xs.extend((x - width / 2, x + width / 2))
        ys.extend((y - height / 2, y + height / 2))
    body = footprint.get("body") or {}
    width = body.get("width_mm")
    length = body.get("length_mm")
    if isinstance(width, (int, float)) and width > 0:
        xs.extend((-float(width) / 2, float(width) / 2))
    if isinstance(length, (int, float)) and length > 0:
        ys.extend((-float(length) / 2, float(length) / 2))
    if not xs or not ys:
        raise ComponentCadError("footprint has no measurable pads or body")
    margin = footprint.get("courtyard_margin_mm")
    margin = 0.5 if margin is None else _finite(margin, "courtyard margin")
    if margin < 0:
        raise ComponentCadError("courtyard margin cannot be negative")
    x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    return [[round(x0, 6), round(y0, 6)], [round(x1, 6), round(y0, 6)],
            [round(x1, 6), round(y1, 6)], [round(x0, 6), round(y1, 6)]]


def build_component_cad_program(component: dict[str, Any], inspection: dict[str, Any]) -> dict[str, Any]:
    identity = component.get("component") or {}
    mpn = str(identity.get("mpn") or "").strip()
    datasheet_sha = str((component.get("evidence") or {}).get("sha256") or "")
    if len(datasheet_sha) != 64:
        raise ComponentCadError("datasheet SHA-256 evidence is missing")
    pages = _inspection_pages(inspection)
    land_pattern_pages = _inspection_pages(inspection, {"land_pattern"})
    footprint_pages = _inspection_pages(inspection, {"land_pattern", "orientation"})
    model_pages = _inspection_pages(inspection, {"package", "orientation"})
    pin_pages = _inspection_pages(inspection, {"pinout", "pin_table", "ordering", "overview"})
    issues: list[str] = []
    if not mpn:
        issues.append("component MPN is missing")
    if not pages:
        issues.append("Sol inspection selected no evidence regions")
    if not land_pattern_pages:
        issues.append("datasheet contains no selected manufacturer land-pattern evidence")
    if not model_pages:
        issues.append("datasheet contains no selected package drawing")
    if not pin_pages:
        issues.append("datasheet contains no selected pinout evidence")

    symbol_commands: list[dict[str, Any]] = []
    pins = (component.get("symbol") or {}).get("pins") or []
    for index, pin in enumerate(pins):
        number = str(pin.get("number") or "").strip()
        if not number:
            issues.append(f"symbol pin {index + 1} has no number")
            continue
        symbol_commands.append({"op": "symbol.pin.create", "id": f"pin-{number}",
                                "params": {"number": number, "name": str(pin.get("name") or ""),
                                           "electrical_type": str(pin.get("electrical_type") or "passive")},
                                "provenance": _provenance(datasheet_sha, pin_pages, "pinout")})
    if not symbol_commands:
        issues.append("schematic symbol has no grounded pins")

    footprint = component.get("footprint") or {}
    mount = footprint.get("mount")
    if mount not in ("smd", "through_hole"):
        issues.append("footprint mount must be smd or through_hole")
    if footprint.get("derived_from_outline"):
        issues.append("land pattern is derived from a package outline rather than a manufacturer pattern")
    trusted_footprint = footprint.get("generated") in ("ipc7351", "vector-trace", "ipc-passive") \
        or bool(footprint.get("dimensions"))
    if not trusted_footprint:
        issues.append("land pattern has no verified dimension or vector source")
    extraction = component.get("extraction") or {}
    if (extraction.get("grounding") or {}).get("status") != "passed":
        issues.append("recommended land-pattern visual grounding did not pass")
    if (extraction.get("verification") or {}).get("geometry_confidence") != "verified":
        issues.append("footprint geometry confidence is not verified")
    if not component.get("electrical"):
        issues.append("datasheet electrical model is missing")
    fp_provenance = _provenance(datasheet_sha, footprint_pages, "land-pattern")
    footprint_commands: list[dict[str, Any]] = [{
        "op": "footprint.begin", "id": "footprint",
        "params": {"name": str(footprint.get("name") or mpn), "mount": mount},
        "provenance": fp_provenance,
    }]
    seen_numbers: set[str] = set()
    rectangles: list[tuple[float, float, float, float, str]] = []
    for index, pad in enumerate(footprint.get("pads") or []):
        try:
            number = str(pad.get("number") or "").strip()
            x = _finite(pad.get("x_mm"), f"pad {index + 1} x_mm")
            y = _finite(pad.get("y_mm"), f"pad {index + 1} y_mm")
            width = _finite(pad.get("width_mm"), f"pad {index + 1} width_mm")
            height = _finite(pad.get("height_mm"), f"pad {index + 1} height_mm")
            if not number or number in seen_numbers:
                issues.append(f"pad {index + 1} has a missing or duplicate number")
                continue
            seen_numbers.add(number)
            if width <= 0 or height <= 0:
                issues.append(f"pad {number} dimensions must be positive")
            drill = pad.get("drill_mm")
            if mount == "through_hole":
                if drill is None or _finite(drill, f"pad {number} drill_mm") <= 0:
                    issues.append(f"THT pad {number} requires a positive drill")
                elif (min(width, height) - float(drill)) / 2 < 0.125 - 1e-9:
                    issues.append(f"THT pad {number} annular ring is below 0.125 mm")
            elif drill not in (None, 0, 0.0):
                issues.append(f"SMT pad {number} cannot carry a drill")
            params = {"number": number, "x_mm": x, "y_mm": y,
                      "width_mm": width, "height_mm": height,
                      "shape": pad.get("shape", "rect"),
                      "mechanical": bool(pad.get("mechanical", False))}
            if drill is not None:
                params["drill_mm"] = float(drill)
            footprint_commands.append({"op": "footprint.pad.create", "id": f"pad-{index + 1}",
                                       "params": params, "provenance": fp_provenance})
            rectangles.append((x - width / 2, y - height / 2,
                               x + width / 2, y + height / 2, number))
        except ComponentCadError as exc:
            issues.extend(exc.issues)
    if len(footprint_commands) == 1:
        issues.append("footprint has no concrete pads")
    for left in range(len(rectangles)):
        for right in range(left + 1, len(rectangles)):
            a, b = rectangles[left], rectangles[right]
            if min(a[2], b[2]) - max(a[0], b[0]) > 1e-6 \
                    and min(a[3], b[3]) - max(a[1], b[1]) > 1e-6:
                issues.append(f"pads {a[4]} and {b[4]} overlap")
    try:
        courtyard = _courtyard(footprint)
        footprint_commands.append({"op": "footprint.courtyard.set", "id": "courtyard",
                                   "params": {"points_mm": courtyard},
                                   "provenance": fp_provenance})
    except ComponentCadError as exc:
        issues.extend(exc.issues)

    pin_numbers = {str(pin.get("number") or "").strip() for pin in pins}
    electrical_pads = {str(pad.get("number") or "").strip()
                       for pad in footprint.get("pads") or [] if not pad.get("mechanical", False)}
    for number in sorted((pin_numbers - electrical_pads) | (electrical_pads - pin_numbers)):
        issues.append(f"symbol/pad mapping is incomplete for {number}")

    construction = (component.get("package_3d") or {}).get("construction") or {}
    if construction.get("author") != "gpt-5.6-luna-xhigh":
        issues.append("3D construction was not authored by GPT-5.6 Luna XHigh")
    if construction.get("method") != "parametric" or construction.get("complexity") != "simple":
        issues.append("complex package geometry is preview-only until exact CAD dimensions are supplied")
    if construction.get("assumptions"):
        issues.append("3D construction contains unverified dimensional assumptions")
    model_provenance = _provenance(datasheet_sha, model_pages, "package")
    model_commands: list[dict[str, Any]] = []
    for index, primitive in enumerate(construction.get("primitives") or []):
        primitive_id = str(primitive.get("id") or f"primitive-{index + 1}")
        if any(char.isspace() for char in primitive_id):
            issues.append(f"3D primitive {primitive_id!r} contains whitespace")
            continue
        try:
            center = [_finite(value, f"{primitive_id} center") for value in primitive.get("center_mm", [])]
            size = [_finite(value, f"{primitive_id} size") for value in primitive.get("size_mm", [])]
            rotation = [_finite(value, f"{primitive_id} rotation")
                        for value in primitive.get("rotation_deg_xyz", [])]
            if len(center) != 3 or len(size) != 3 or len(rotation) != 3 or min(size, default=0) <= 0:
                issues.append(f"3D primitive {primitive_id} has invalid vectors")
                continue
            shape = primitive.get("shape")
            if shape not in ("box", "cylinder", "dome"):
                issues.append(f"3D primitive {primitive_id} uses unsupported shape {shape}")
                continue
            provenance = primitive.get("provenance") or []
            if not provenance:
                issues.append(f"3D primitive {primitive_id} has no callout provenance")
                continue
            model_commands.append({"op": "model.primitive.create", "id": primitive_id,
                                   "params": {"shape": shape, "center_mm": center,
                                              "size_mm": size, "rotation_deg_xyz": rotation,
                                              "role": primitive.get("role", "detail")},
                                   "provenance": provenance})
        except ComponentCadError as exc:
            issues.extend(exc.issues)
    if not model_commands:
        issues.append("package construction contains no executable B-Rep primitives")
    model_commands.append({"op": "model.step.export", "id": "step-export",
                           "params": {"schema": "AP242", "coordinate_frame": "footprint"},
                           "provenance": model_provenance})
    if issues:
        raise ComponentCadError(sorted(set(issues)))

    program = {
        "schema": PROGRAM_SCHEMA, "units": "mm", "author": "gpt-5.6-luna-xhigh",
        "component": {"mpn": mpn, "package_variant": str(inspection.get("package_variant"))},
        "source": {"datasheet_sha256": datasheet_sha,
                   "inspection_digest": _digest(inspection), "pages": pages},
        "symbol_commands": symbol_commands, "footprint_commands": footprint_commands,
        "model_commands": model_commands,
        "validation": {"require_valid_brep": True, "require_step_roundtrip": True,
                       "require_pin_pad_map": True, "max_dimension_error_mm": 0.05},
    }
    validate_component_cad_program(program)
    return program


def validate_component_cad_program(program: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
        schema = json.loads(PROGRAM_SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(program),
                        key=lambda item: list(item.absolute_path))
    except (ImportError, ModuleNotFoundError):
        required = {"schema", "units", "author", "component", "source",
                    "symbol_commands", "footprint_commands", "model_commands", "validation"}
        errors = []
        if not isinstance(program, dict) or set(program) != required:
            errors.append("component CAD program fields do not match version 1")
        if program.get("schema") != PROGRAM_SCHEMA or program.get("units") != "mm" \
                or program.get("author") != "gpt-5.6-luna-xhigh":
            errors.append("component CAD program identity is invalid")
        for group in ("symbol_commands", "footprint_commands", "model_commands"):
            commands = program.get(group)
            if not isinstance(commands, list) or not commands:
                errors.append(f"{group} must contain commands")
                continue
            for command in commands:
                if not isinstance(command, dict) or set(command) != {"op", "id", "params", "provenance"} \
                        or not isinstance(command.get("params"), dict) \
                        or not isinstance(command.get("provenance"), list) \
                        or not command.get("provenance"):
                    errors.append(f"{group} contains an invalid typed command")
                    break
    if errors:
        raise ComponentCadError([error.message if hasattr(error, "message") else str(error)
                                 for error in errors[:16]])


def _find_compiler(executable: str | None = None) -> str:
    candidate = executable or os.environ.get("DESIGNSTUDIO_COMPONENT_CAD", "").strip()
    if candidate:
        path = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
        if path and os.access(path, os.X_OK):
            return str(Path(path).resolve())
        raise ComponentCadError(f"component CAD executor is not executable: {candidate}")
    found = shutil.which("designstudio-component-cad")
    if found:
        return found
    root = Path(__file__).parents[2]
    for path in (root / "build" / "core" / "designstudio-component-cad",
                 root / "build" / "designstudio-component-cad",
                 root / "build-release" / "core" / "designstudio-component-cad"):
        if os.access(path, os.X_OK):
            return str(path)
    raise ComponentCadError("Open CASCADE component CAD executor is not installed or built")


def compile_step(program: dict[str, Any], destination: str | Path, *,
                 executable: str | None = None, timeout: int = 120) -> dict[str, Any]:
    validate_component_cad_program(program)
    solids = [command for command in program["model_commands"]
              if command["op"] == "model.primitive.create"]
    with tempfile.TemporaryDirectory(prefix="designstudio-component-cad-") as directory:
        command_file = Path(directory) / "commands.txt"
        lines = ["# DesignStudio typed component CAD stream v1"]
        for command in solids:
            params = command["params"]
            values = [params["shape"], command["id"], *params["center_mm"],
                      *params["size_mm"], *params["rotation_deg_xyz"]]
            lines.append(" ".join(str(value) for value in values))
        command_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run([_find_compiler(executable), str(command_file), str(Path(destination).resolve())],
                                capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise ComponentCadError((result.stderr or result.stdout or "component CAD execution failed").strip())
    try:
        report_line = next(line for line in reversed(result.stdout.splitlines())
                           if line.lstrip().startswith("{"))
        report = json.loads(report_line)
    except (json.JSONDecodeError, StopIteration) as exc:
        raise ComponentCadError("component CAD executor returned an invalid report") from exc
    path = Path(destination)
    step_bytes = path.read_bytes() if path.is_file() else b""
    if not step_bytes.startswith(b"ISO-10303-21"):
        raise ComponentCadError("component CAD executor did not produce a STEP exchange file")
    if b"AP242_MANAGED_MODEL_BASED_3D_ENGINEERING" not in step_bytes \
            or report.get("step_schema") != "AP242":
        path.unlink(missing_ok=True)
        raise ComponentCadError("component CAD executor did not produce the required AP242 STEP schema")
    report.update({"sha256": hashlib.sha256(step_bytes).hexdigest(),
                   "byte_size": path.stat().st_size, "roundtrip_valid": True})
    return report


def _png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    rows = b"".join(b"\x00" + bytes(pixels[y * width * 4:(y + 1) * width * 4]) for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def render_footprint_preview(component: dict[str, Any], destination: str | Path,
                             size: int = 720) -> Path:
    footprint = component.get("footprint") or {}
    court = _courtyard(footprint)
    x0, y0 = court[0]; x1, y1 = court[2]
    span = max(x1 - x0, y1 - y0, 0.001) * 1.15
    scale = (size - 40) / span
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pixels = bytearray([245, 247, 250, 255] * size * size)

    def point(x: float, y: float) -> tuple[int, int]:
        return int(size / 2 + (x - cx) * scale), int(size / 2 - (y - cy) * scale)

    def fill_rect(ax: float, ay: float, bx: float, by: float, color: tuple[int, int, int, int]) -> None:
        px0, py1 = point(ax, ay); px1, py0 = point(bx, by)
        for py in range(max(0, min(py0, py1)), min(size, max(py0, py1) + 1)):
            for px in range(max(0, min(px0, px1)), min(size, max(px0, px1) + 1)):
                offset = (py * size + px) * 4; pixels[offset:offset + 4] = bytes(color)

    def outline(points: list[list[float]], color: tuple[int, int, int, int], thickness: int = 2) -> None:
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            ax, ay = point(*start); bx, by = point(*end)
            steps = max(abs(bx - ax), abs(by - ay), 1)
            for step in range(steps + 1):
                px = round(ax + (bx - ax) * step / steps); py = round(ay + (by - ay) * step / steps)
                for oy in range(-thickness, thickness + 1):
                    for ox in range(-thickness, thickness + 1):
                        if 0 <= px + ox < size and 0 <= py + oy < size:
                            offset = ((py + oy) * size + px + ox) * 4
                            pixels[offset:offset + 4] = bytes(color)

    outline(court, (200, 45, 55, 255), 1)
    body = footprint.get("body") or {}
    if body.get("width_mm") and body.get("length_mm"):
        width, length = float(body["width_mm"]), float(body["length_mm"])
        outline([[-width / 2, -length / 2], [width / 2, -length / 2],
                 [width / 2, length / 2], [-width / 2, length / 2]], (55, 75, 95, 255), 1)
    for pad in footprint.get("pads") or []:
        x, y = float(pad["x_mm"]), float(pad["y_mm"])
        width, height = float(pad["width_mm"]), float(pad["height_mm"])
        fill_rect(x - width / 2, y - height / 2, x + width / 2, y + height / 2,
                  (205, 156, 45, 255) if not pad.get("mechanical") else (130, 135, 145, 255))
    path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
    _png(path, size, size, pixels)
    return path


def export_kicad_v6_footprint(component: dict[str, Any], destination: str | Path,
                              *, step_reference: str = "package.step") -> Path:
    """Write a deterministic KiCad 6 footprint bound to the aligned STEP model."""
    footprint = component.get("footprint") or {}
    name = _safe(str(footprint.get("name") or
                     (component.get("component") or {}).get("mpn") or "component"))
    mount = str(footprint.get("mount") or "smd").lower()
    through_hole = mount in ("through", "tht", "through-hole", "through_hole", "thru")
    courtyard = _courtyard(footprint)

    def number(value: Any) -> str:
        return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"

    lines = [f'(footprint "{name}"', "  (version 20211014)",
             '  (generator "DesignStudio")',
             f'  (attr {"through_hole" if through_hole else "smd"})',
             '  (fp_text reference "REF**" (at 0 -3) (layer "F.SilkS")',
             '    (effects (font (size 1 1) (thickness 0.15))))',
             f'  (fp_text value "{name}" (at 0 3) (layer "F.Fab")',
             '    (effects (font (size 1 1) (thickness 0.15))))']
    for index, start in enumerate(courtyard):
        end = courtyard[(index + 1) % len(courtyard)]
        lines.append("  (fp_line (start {} {}) (end {} {}) (stroke (width 0.05) "
                     "(type default)) (layer \"F.CrtYd\"))".format(
                         number(start[0]), number(start[1]), number(end[0]), number(end[1])))
    body = footprint.get("body") or {}
    default_outline: list[list[float]] = []
    if body.get("width_mm") and body.get("length_mm"):
        half_x, half_y = float(body["width_mm"]) / 2, float(body["length_mm"]) / 2
        default_outline = [[-half_x, -half_y], [half_x, -half_y],
                           [half_x, half_y], [-half_x, half_y]]

    def add_outline(points: list[list[float]], layer: str, width: float) -> None:
        if len(points) < 3:
            return
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            lines.append("  (fp_line (start {} {}) (end {} {}) (stroke (width {}) "
                         "(type default)) (layer \"{}\"))".format(
                             number(start[0]), number(start[1]), number(end[0]),
                             number(end[1]), number(width), layer))

    outlines = footprint.get("outlines") or {}
    silk = outlines.get("silkscreen") or {}
    fabrication = outlines.get("fabrication") or {}
    assembly = outlines.get("assembly") or {}
    add_outline(silk.get("points_mm") or default_outline, "F.SilkS",
                float(silk.get("line_width_mm", 0.15)))
    add_outline(fabrication.get("points_mm") or default_outline, "F.Fab",
                float(fabrication.get("line_width_mm", 0.10)))
    # KiCad has no portable assembly-outline layer; Dwgs.User is deterministic,
    # editable, and remains distinct from fabrication and silkscreen output.
    add_outline(assembly.get("points_mm") or default_outline, "Dwgs.User",
                float(assembly.get("line_width_mm", 0.10)))
    for pad in footprint.get("pads") or []:
        pad_number = str(pad.get("number", pad.get("name", ""))).replace('"', "")
        shape = str(pad.get("shape") or "rect").lower()
        kicad_shape = "circle" if shape in ("circle", "round") else \
            "roundrect" if shape in ("rounded_rect", "roundrect") else "oval" if shape == "oval" else "rect"
        at = f'{number(pad["x_mm"])} {number(pad["y_mm"])}'
        size = f'{number(pad["width_mm"])} {number(pad["height_mm"])}'
        if through_hole or pad.get("drill_mm") is not None:
            drill = number(pad.get("drill_mm") or min(float(pad["width_mm"]),
                                                       float(pad["height_mm"])) * 0.5)
            lines.append(f'  (pad "{pad_number}" thru_hole {kicad_shape} (at {at}) '
                         f'(size {size}) (drill {drill}) (layers "*.Cu" "*.Mask"))')
        else:
            suffix = " (roundrect_rratio 0.25)" if kicad_shape == "roundrect" else ""
            mask = footprint.get("solder_mask_expansion_mm")
            paste = footprint.get("paste_reduction_mm")
            if mask is not None:
                suffix += f" (solder_mask_margin {number(mask)})"
            if paste is not None:
                suffix += f" (solder_paste_margin -{number(paste)})"
            lines.append(f'  (pad "{pad_number}" smd {kicad_shape} (at {at}) '
                         f'(size {size}) (layers "F.Cu" "F.Paste" "F.Mask"){suffix})')
    reference = step_reference.replace('"', "")
    lines.extend([f'  (model "{reference}"', "    (offset (xyz 0 0 0))",
                  "    (scale (xyz 1 1 1))", "    (rotate (xyz 0 0 0))", "  )", ")"])
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _render_model_wireframes(meshes: list[dict[str, Any]], output: Path,
                             resolution: int = 512) -> list[dict[str, Any]]:
    """Dependency-free orthographic preview fallback used when Pillow is absent."""
    mappings = {
        "front": ((0, 1), (2, 1)), "rear": ((0, -1), (2, 1)),
        "left": ((1, -1), (2, 1)), "right": ((1, 1), (2, 1)),
        "top": ((0, 1), (1, 1)), "bottom": ((0, -1), (1, 1)),
    }
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for view, (right, up) in mappings.items():
        vertices = [vertex for mesh in meshes for vertex in mesh["vertices"]]
        projected = [(vertex[right[0]] * right[1], vertex[up[0]] * up[1])
                     for vertex in vertices]
        min_x, max_x = min(p[0] for p in projected), max(p[0] for p in projected)
        min_y, max_y = min(p[1] for p in projected), max(p[1] for p in projected)
        span = max(max_x - min_x, max_y - min_y, 0.001) * 1.18
        centre_x, centre_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        scale = (resolution - 4) / span
        pixels = bytearray([246, 248, 250, 255] * resolution * resolution)

        def project(vertex):
            return (round((vertex[right[0]] * right[1] - centre_x) * scale + resolution / 2),
                    round(resolution / 2 - (vertex[up[0]] * up[1] - centre_y) * scale))

        def line(a, b, color):
            x0, y0 = a; x1, y1 = b
            dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
            dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
            error = dx + dy
            while True:
                if 0 <= x0 < resolution and 0 <= y0 < resolution:
                    offset = (y0 * resolution + x0) * 4
                    pixels[offset:offset + 4] = bytes(color)
                if x0 == x1 and y0 == y1:
                    break
                twice = 2 * error
                if twice >= dy: error += dy; x0 += sx
                if twice <= dx: error += dx; y0 += sy

        for mesh in meshes:
            color = tuple(max(0, min(255, round(float(value) * 255)))
                          for value in mesh.get("color", [0.1, 0.15, 0.2, 1])[:3]) + (255,)
            for face in mesh["faces"]:
                points = [project(mesh["vertices"][index]) for index in face]
                line(points[0], points[1], color); line(points[1], points[2], color)
                line(points[2], points[0], color)
        path = output / f"{view}.png"
        _png(path, resolution, resolution, pixels)
        records.append({"view": view, "image_uri": str(path.resolve()),
                        "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return records


def prepare_component_preview(component: dict[str, Any], inspection: dict[str, Any],
                              *, preview_root: str | Path | None = None,
                              compiler: str | None = None,
                              datasheet_bytes: bytes | None = None) -> dict[str, Any]:
    program = build_component_cad_program(component, inspection)
    root = Path(preview_root or (data_root() / "component-previews"))
    directory = root / _safe(program["component"]["mpn"]) / _digest(program)[:16]
    directory.mkdir(parents=True, exist_ok=True)
    program_path = directory / "component.cad-program.json"
    program_path.write_text(json.dumps(program, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    step_path = directory / "package.step"
    step_report = compile_step(program, step_path, executable=compiler)

    from swarm.memory.component_3d_assets import meshes_from_component, render_six_views, write_glb
    meshes = meshes_from_component(component)
    glb_path = directory / "package-preview.glb"
    write_glb(meshes, glb_path, generator="designstudio-component-cad-preview")
    try:
        views = render_six_views(meshes, directory / "views", resolution=512)
    except Exception as exc:
        if "Pillow is required" not in str(exc):
            raise
        views = _render_model_wireframes(meshes, directory / "views", resolution=512)
    footprint_preview = render_footprint_preview(component, directory / "footprint-preview.png")
    footprint_path = export_kicad_v6_footprint(
        component, directory / f"{_safe(program['component']['mpn'])}.kicad_mod",
        step_reference="package.step")
    datasheet_path = None
    if datasheet_bytes is not None:
        if not datasheet_bytes.startswith(b"%PDF"):
            raise ComponentCadError("preview datasheet bytes are not a PDF")
        datasheet_path = directory / "datasheet.pdf"
        datasheet_path.write_bytes(datasheet_bytes)

    preview_component = copy.deepcopy(component)
    preview_component["inspection"] = copy.deepcopy(inspection)
    preview_component["cad_program"] = program
    preview_component.setdefault("package_3d", {})["step_asset"] = {
        "format": "step", "asset_uri": str(step_path.resolve()), "sha256": step_report["sha256"],
        "solid_count": int(step_report["solid_count"]), "roundtrip_valid": True,
        "bounds_mm": step_report.get("bounds_mm"),
    }
    verification = preview_component.setdefault("extraction", {}).setdefault("verification", {})
    verification.update({"state": "review_required", "placement_allowed": False,
                         "blockers": [], "warnings": verification.get("warnings") or []})
    component_path = directory / "component.preview.json"

    manifest_path = directory / "preview-manifest.json"
    preview_component["preview"] = {"schema": "design-studio.component-preview-ref/1",
                                    "state": "ready", "manifest_uri": str(manifest_path.resolve())}
    component_path.write_text(json.dumps(preview_component, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")

    from swarm.memory.component_binding import bind_component
    binding = bind_component(preview_component, step_path, model_mpn=program["component"]["mpn"],
                             alignment_status="verified", source_uri=str(component_path.resolve()))
    binding_path = directory / "component.bound-preview.json"
    binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    from swarm.memory.component_engineering import evaluate_component_engineering
    engineering_checks = evaluate_component_engineering(preview_component)
    manifest = {
        "schema": PREVIEW_SCHEMA, "state": "ready", "component_mpn": program["component"]["mpn"],
        "component_path": str(component_path.resolve()), "cad_program_path": str(program_path.resolve()),
        "component_sha256": hashlib.sha256(component_path.read_bytes()).hexdigest(),
        "cad_program_sha256": hashlib.sha256(program_path.read_bytes()).hexdigest(),
        "step_path": str(step_path.resolve()), "step_sha256": step_report["sha256"],
        "kicad_footprint_path": str(footprint_path.resolve()),
        "kicad_footprint_sha256": hashlib.sha256(footprint_path.read_bytes()).hexdigest(),
        "glb_path": str(glb_path.resolve()), "footprint_preview": str(footprint_preview.resolve()),
        "model_previews": {view["view"]: view["image_uri"] for view in views},
        "binding_preview_path": str(binding_path.resolve()),
        "binding_preview_sha256": hashlib.sha256(binding_path.read_bytes()).hexdigest(),
        "assumptions": copy.deepcopy(preview_component.get("assumptions") or []),
        "engineering_checks": engineering_checks,
        "validation": {"footprint": "passed", "pin_pad_map": "passed", "brep": "passed",
                       "step_roundtrip": "passed", "solid_count": step_report["solid_count"],
                       "bounds_mm": step_report.get("bounds_mm")},
    }
    if datasheet_path is not None:
        manifest["datasheet_path"] = str(datasheet_path.resolve())
        manifest["datasheet_sha256"] = hashlib.sha256(datasheet_path.read_bytes()).hexdigest()
    manifest["preview_digest"] = _digest(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"component": preview_component, "manifest": manifest,
            "manifest_path": str(manifest_path.resolve())}


def prepare_blocked_component_preview(component: dict[str, Any], inspection: dict[str, Any],
                                      issues: list[str], *,
                                      preview_root: str | Path | None = None) -> dict[str, Any]:
    """Persist a non-publishable partial preview with actionable missing data."""
    mpn = str((component.get("component") or {}).get("mpn") or "component")
    root = Path(preview_root or (data_root() / "component-previews"))
    directory = root / _safe(mpn) / ("blocked-" + _digest({"inspection": inspection,
                                                            "issues": issues})[:16])
    directory.mkdir(parents=True, exist_ok=True)
    partial = copy.deepcopy(component)
    partial["inspection"] = copy.deepcopy(inspection)
    verification = partial.setdefault("extraction", {}).setdefault("verification", {})
    verification.update({"state": "review_required", "placement_allowed": False,
                         "blockers": sorted(set(issues)),
                         "warnings": verification.get("warnings") or []})
    footprint_preview = ""
    try:
        footprint_preview = str(render_footprint_preview(
            partial, directory / "footprint-preview.png").resolve())
    except Exception:
        pass
    component_path = directory / "component.blocked-preview.json"
    manifest = {
        "schema": PREVIEW_SCHEMA, "state": "blocked", "component_mpn": mpn,
        "component_path": str(component_path.resolve()), "footprint_preview": footprint_preview,
        "model_previews": {}, "blockers": sorted(set(issues)),
        "assumptions": copy.deepcopy(partial.get("assumptions") or []),
        "validation": {"footprint": "incomplete", "pin_pad_map": "incomplete",
                       "brep": "not_run", "step_roundtrip": "not_run", "solid_count": 0},
    }
    manifest["preview_digest"] = _digest(manifest)
    manifest_path = directory / "preview-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial["preview"] = {"schema": "design-studio.component-preview-ref/1",
                          "state": "blocked", "manifest_uri": str(manifest_path.resolve())}
    component_path.write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"component": partial, "manifest": manifest,
            "manifest_path": str(manifest_path.resolve())}


def publish_component_preview(manifest_path: str | Path, *, component_root: str | Path | None = None,
                              binding_root: str | Path | None = None, reviewer: str = "user",
                              library_db: str | Path | None = None) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != PREVIEW_SCHEMA or manifest.get("state") != "ready":
        raise ComponentCadError("component preview is not publication-ready")
    expected = manifest.pop("preview_digest", "")
    if expected != _digest(manifest):
        raise ComponentCadError("component preview manifest digest mismatch")
    manifest["preview_digest"] = expected
    component_path = Path(manifest["component_path"])
    program_path = Path(manifest["cad_program_path"])
    binding_preview_path = Path(manifest["binding_preview_path"])
    step_path = Path(manifest["step_path"])
    footprint_path = Path(manifest["kicad_footprint_path"])
    expected_files = ((component_path, "component_sha256", "component JSON"),
                      (program_path, "cad_program_sha256", "CAD program"),
                      (binding_preview_path, "binding_preview_sha256", "binding preview"),
                      (step_path, "step_sha256", "STEP"),
                      (footprint_path, "kicad_footprint_sha256", "KiCad footprint"))
    if manifest.get("datasheet_path"):
        expected_files += ((Path(manifest["datasheet_path"]), "datasheet_sha256", "datasheet"),)
    for artifact, digest_key, label in expected_files:
        if not artifact.is_file() \
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest.get(digest_key):
            raise ComponentCadError(f"preview {label} digest mismatch")
    component = json.loads(component_path.read_text(encoding="utf-8"))
    program = json.loads(program_path.read_text(encoding="utf-8"))
    validate_component_cad_program(program)
    mpn = str((component.get("component") or {}).get("mpn") or "")
    if manifest.get("datasheet_path") and \
            (component.get("evidence") or {}).get("sha256") != manifest.get("datasheet_sha256"):
        raise ComponentCadError("preview datasheet digest does not match component evidence")
    preview = component.get("preview") or {}
    if component.get("schema") != "design-studio.component/2" \
            or mpn != manifest.get("component_mpn") or program["component"]["mpn"] != mpn \
            or preview.get("state") != "ready" \
            or Path(preview.get("manifest_uri", "")).resolve() != path:
        raise ComponentCadError("preview component identity or approval reference mismatch")
    verification = component.setdefault("extraction", {}).setdefault("verification", {})
    verification.update({"state": "approved", "placement_allowed": True,
                         "reviewer": reviewer, "blockers": []})
    component["preview"]["state"] = "published"

    from swarm.memory.component_binding import BoundComponentStore, bind_component
    binding = bind_component(component, step_path, model_mpn=mpn, alignment_status="verified",
                             source_uri=str(component_path.resolve()))
    binding_store = BoundComponentStore(binding_root)
    binding_path = binding_store.save(binding)
    library = Path(component_root or component_library_dir())
    library.mkdir(parents=True, exist_ok=True)
    destination = library / f"{_safe(mpn)}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=library, delete=False,
                                     prefix=f".{_safe(mpn)}-", suffix=".tmp") as temporary:
        json.dump(component, temporary, indent=2, sort_keys=True); temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, destination)

    # The SQLite record is authoritative; JSON/STEP files above remain legacy
    # projections for existing CAD consumers. Explicit test roots get an
    # isolated sibling database unless the caller supplies one.
    from swarm.memory.component_library import ComponentLibrary
    if library_db is None and component_root is not None:
        library_db = Path(component_root).resolve().parent / "component-library.sqlite3"
    stored_binding = json.loads(binding_path.read_text(encoding="utf-8"))
    component_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with ComponentLibrary(library_db) as database, database.connection:
        authoritative_db_path = str(database.path)
        key = database.upsert_component(component, binding=stored_binding, status="approved",
                                        evidence_state=str((component.get("evidence") or {}).get(
                                            "mpn_match") or "incomplete"))
        step_sha = database.put_asset_file("step", step_path, format="step")
        footprint_sha = database.put_asset_file("kicad_v6_footprint", footprint_path,
                                               format="kicad_mod")
        database.link_asset(mpn, "model_3d", step_sha, exact_mpn=True,
                            provenance={"generator": program["author"]})
        database.link_asset(mpn, "footprint", footprint_sha, exact_mpn=True,
                            provenance={"format": "KiCad v6", "cad_program": _digest(program)})
        datasheet_sha = None
        if manifest.get("datasheet_path"):
            datasheet = Path(manifest["datasheet_path"])
            datasheet_sha = database.put_asset_file("datasheet_pdf", datasheet, format="pdf")
            exact = (component.get("evidence") or {}).get("mpn_match") == "exact"
            database.link_asset(mpn, "datasheet", datasheet_sha, exact_mpn=exact,
                                provenance=component.get("evidence") or {})
        circuits = (component.get("electrical") or {}).get("application_circuits") or []
        database.replace_reference_circuits(mpn, circuits, datasheet_sha)
        from swarm.memory.component_engineering import evaluate_component_engineering
        for category, check in evaluate_component_engineering(component).items():
            database.record_check(f"component:{key}", category, check["status"], check,
                                  critical=check.get("critical", True),
                                  input_digest=component_digest)
        database.record_approval(f"component:{key}", "engineering_review", component_digest,
                                 reviewer, rationale="Explicit component preview publication")
    manifest["state"] = "published"
    manifest["published_component_path"] = str(destination.resolve())
    manifest["published_binding_path"] = str(binding_path.resolve())
    manifest["preview_digest"] = _digest({key: value for key, value in manifest.items()
                                           if key != "preview_digest"})
    temporary_manifest = path.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, path)
    return {"component_path": str(destination.resolve()), "binding_path": str(binding_path.resolve()),
            "library_db": authoritative_db_path,
            "kicad_footprint_path": str(footprint_path.resolve()),
            "step_path": str((binding_store.root / stored_binding["model_3d"]["asset_uri"]).resolve())}
