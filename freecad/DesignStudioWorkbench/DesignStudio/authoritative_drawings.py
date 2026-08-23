"""Authoritative engineering drawings generated from FreeCAD B-Reps.

This is the package-3 drawing path.  The source object is never measured from
SVG/DXF; OpenCASCADE's HLR projection and section operations provide the
geometry, while the generated files are deterministic evidence for people and
downstream interchange.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import uuid

from .physical_design import (
    PhysicalDesignError,
    _edge_points,
    _find_semantic,
    _relative,
    canonical_digest,
    file_digest,
    shape_digest,
)


SCHEMA = "design-studio.authoritative-drawing-package/1"
VIEWS = ("front", "rear", "left", "right", "top", "bottom")
SHEETS = (
    ("01-general-arrangement", "General arrangement"),
    ("02-selected-region-details", "Selected-region details"),
    ("03-sections-continuity", "Sections and continuity"),
    ("04-material-manufacturing", "Material, manufacturing and tolerances"),
    ("05-interfaces-clearances", "Interfaces and protected clearances"),
)
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _strict(value, allowed, required, label):
    if not isinstance(value, dict):
        raise PhysicalDesignError(f"{label} must be an object")
    unknown, missing = set(value) - set(allowed), set(required) - set(value)
    if unknown or missing:
        raise PhysicalDesignError(
            f"{label} keys invalid; unknown={sorted(unknown)}, missing={sorted(missing)}")


def _id(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PhysicalDesignError(f"{label} is not a valid identifier")
    return value


def _digest(value, label):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PhysicalDesignError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _finite(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PhysicalDesignError(f"{label} must be finite")
    value = float(value)
    if minimum is not None and value < minimum:
        raise PhysicalDesignError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise PhysicalDesignError(f"{label} must be <= {maximum}")
    return value


def _point(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise PhysicalDesignError(f"{label} must contain three coordinates")
    return [_finite(item, f"{label}[{index}]", -1_000_000, 1_000_000)
            for index, item in enumerate(value)]


def _normal(value, label):
    vector = _point(value, label)
    length = math.sqrt(sum(item * item for item in vector))
    if length <= 1.0e-12:
        raise PhysicalDesignError(f"{label} must not be zero")
    return [item / length for item in vector]


def _workspace_json(root, requested):
    path = Path(requested).expanduser().resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".json" or not path.is_file():
        raise PhysicalDesignError("drawing package request must be an existing workspace JSON file")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PhysicalDesignError(f"drawing package request is invalid JSON: {exc}") from exc


def validate_authoritative_drawing_package(value):
    package = deepcopy(value)
    keys = {"schema", "package_id", "revision", "document", "source", "views",
            "sections", "annotations", "output", "provenance"}
    _strict(package, keys, keys, "authoritative drawing package")
    if package["schema"] != SCHEMA:
        raise PhysicalDesignError(f"schema must be {SCHEMA}")
    _id(package["package_id"], "package_id")
    if not isinstance(package["revision"], int) or isinstance(package["revision"], bool) or package["revision"] < 1:
        raise PhysicalDesignError("revision must be a positive integer")
    document = package["document"]
    _strict(document, {"document_id", "path", "file_sha256"},
            {"document_id", "path", "file_sha256"}, "document")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise PhysicalDesignError("document.document_id is empty")
    _relative(document["path"], "document.path")
    _digest(document["file_sha256"], "document.file_sha256")
    source = package["source"]
    _strict(source, {"semantic_id", "object_name", "shape_sha256", "role", "source_of_truth"},
            {"semantic_id", "object_name", "shape_sha256", "role", "source_of_truth"}, "source")
    _id(source["semantic_id"], "source.semantic_id")
    if not isinstance(source["object_name"], str) or not source["object_name"]:
        raise PhysicalDesignError("source.object_name is empty")
    _digest(source["shape_sha256"], "source.shape_sha256")
    if source["role"] not in {"enclosure", "mechanical", "local_redesign_preview", "assembly", "other"}:
        raise PhysicalDesignError("source.role is invalid")
    if source["source_of_truth"] != "freecad_brep":
        raise PhysicalDesignError("source must be a FreeCAD B-Rep")
    if package["views"] != list(VIEWS):
        raise PhysicalDesignError("views must contain the canonical six orthographic views in order")
    section_ids = set()
    for index, section in enumerate(package["sections"]):
        _strict(section, {"section_id", "plane_origin_mm", "plane_normal", "label"},
                {"section_id", "plane_origin_mm", "plane_normal", "label"}, f"sections[{index}]")
        _id(section["section_id"], f"sections[{index}].section_id")
        if section["section_id"] in section_ids:
            raise PhysicalDesignError("section IDs must be unique")
        section_ids.add(section["section_id"])
        _point(section["plane_origin_mm"], f"sections[{index}].plane_origin_mm")
        section["plane_normal"] = _normal(section["plane_normal"], f"sections[{index}].plane_normal")
        if not isinstance(section["label"], str) or not section["label"].strip():
            raise PhysicalDesignError("section label is empty")
    annotations = package["annotations"]
    akeys = {"selected_faces", "selected_edges", "datums", "dimensions", "tolerances", "gdt", "continuity"}
    _strict(annotations, akeys, akeys, "annotations")
    for name, pattern in (("selected_faces", r"^Face[1-9][0-9]*$"),
                          ("selected_edges", r"^Edge[1-9][0-9]*$")):
        values = annotations[name]
        if not isinstance(values, list) or len(values) != len(set(values)) or any(
                not isinstance(item, str) or not re.fullmatch(pattern, item) for item in values):
            raise PhysicalDesignError(f"annotations.{name} is invalid")
    for index, datum in enumerate(annotations["datums"]):
        _strict(datum, {"datum_id", "point_mm", "label"}, {"datum_id", "point_mm", "label"}, f"datums[{index}]")
        _id(datum["datum_id"], f"datums[{index}].datum_id")
        _point(datum["point_mm"], f"datums[{index}].point_mm")
    for index, dimension in enumerate(annotations["dimensions"]):
        _strict(dimension, {"dimension_id", "kind", "value_mm", "label"},
                {"dimension_id", "kind", "value_mm", "label"}, f"dimensions[{index}]")
        _id(dimension["dimension_id"], f"dimensions[{index}].dimension_id")
        if dimension["kind"] not in {"overall", "local", "radius", "diameter", "angle"}:
            raise PhysicalDesignError("unsupported dimension kind")
        _finite(dimension["value_mm"], f"dimensions[{index}].value_mm", 0)
    for index, tolerance in enumerate(annotations["tolerances"]):
        _strict(tolerance, {"tolerance_id", "nominal_mm", "plus_mm", "minus_mm"},
                {"tolerance_id", "nominal_mm", "plus_mm", "minus_mm"}, f"tolerances[{index}]")
        _id(tolerance["tolerance_id"], f"tolerances[{index}].tolerance_id")
        _finite(tolerance["nominal_mm"], f"tolerances[{index}].nominal_mm")
        _finite(tolerance["plus_mm"], f"tolerances[{index}].plus_mm", 0)
        _finite(tolerance["minus_mm"], f"tolerances[{index}].minus_mm", 0)
    for index, frame in enumerate(annotations["gdt"]):
        _strict(frame, {"frame_id", "symbol", "tolerance_mm", "datum_refs"},
                {"frame_id", "symbol", "tolerance_mm", "datum_refs"}, f"gdt[{index}]")
        _id(frame["frame_id"], f"gdt[{index}].frame_id")
        if frame["symbol"] not in {"flatness", "parallelism", "perpendicularity", "position", "profile"}:
            raise PhysicalDesignError("unsupported GD&T symbol")
        _finite(frame["tolerance_mm"], f"gdt[{index}].tolerance_mm", 0)
        if not isinstance(frame["datum_refs"], list):
            raise PhysicalDesignError("GD&T datum_refs must be an array")
    for index, continuity in enumerate(annotations["continuity"]):
        _strict(continuity, {"continuity_id", "boundary_id", "required", "max_normal_angle_deg"},
                {"continuity_id", "boundary_id", "required", "max_normal_angle_deg"}, f"continuity[{index}]")
        _id(continuity["continuity_id"], f"continuity[{index}].continuity_id")
        if continuity["required"] not in {"G0", "G1", "G2"}:
            raise PhysicalDesignError("continuity requirement is invalid")
        _finite(continuity["max_normal_angle_deg"], f"continuity[{index}].max_normal_angle_deg", 0, 180)
    output = package["output"]
    _strict(output, {"directory", "formats", "sheet_capacity", "additional_sheets_policy"},
            {"directory", "formats", "sheet_capacity", "additional_sheets_policy"}, "output")
    _relative(output["directory"], "output.directory")
    if output["formats"] != ["svg", "dxf"] or output["additional_sheets_policy"] != "deterministic_complexity_split":
        raise PhysicalDesignError("output formats or sheet policy is unsupported")
    if not isinstance(output["sheet_capacity"], int) or output["sheet_capacity"] < 1:
        raise PhysicalDesignError("sheet_capacity must be positive")
    provenance = package["provenance"]
    _strict(provenance, {"created_by", "created_utc", "geometry_authority", "raster_geometry_authority"},
            {"created_by", "created_utc", "geometry_authority", "raster_geometry_authority"}, "provenance")
    if not isinstance(provenance["created_by"], str) or not provenance["created_by"]:
        raise PhysicalDesignError("provenance.created_by is empty")
    if provenance["geometry_authority"] != "freecad_brep" or provenance["raster_geometry_authority"] is not False:
        raise PhysicalDesignError("drawing provenance is not authoritative")
    return package


_VIEW_BASIS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "rear": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
}


def _dot(a, b):
    return sum(a[index] * b[index] for index in range(3))


def _vector(point):
    return [float(point.x), float(point.y), float(point.z)]


def _hlr_projection(shape, view):
    """Return visible/hidden projected OCC edges for one orthographic view."""
    import FreeCAD as App
    import Part
    direction, x_axis, y_axis = _VIEW_BASIS[view]
    box = shape.BoundBox
    span = max(float(box.XLength), float(box.YLength), float(box.ZLength), 1.0)
    origin = App.Vector(-direction[0] * span * 100.0,
                       -direction[1] * span * 100.0,
                       -direction[2] * span * 100.0)
    algo = Part.HLRBRep.Algo()
    algo.add(shape)
    try:
        algo.setProjector(origin, App.Vector(*direction), App.Vector(*y_axis))
        algo.update()
        algo.hide()
        result = Part.HLRBRep.HLRToShape(algo)
        visible_shape = result.vCompound(shape)
        hidden_shape = result.hCompound()
    except Exception as exc:
        raise PhysicalDesignError(f"OCCT HLR failed for {view}: {exc}") from exc

    # HLR returns edges in its own projection plane: the first coordinate is
    # the projected ``up`` axis and the second is the signed projected
    # horizontal axis.  Convert those plane coordinates into the canonical
    # drawing (horizontal, vertical) axes before writing evidence.
    horizontal_sign = {"front": -1.0, "rear": 1.0, "left": 1.0,
                       "right": -1.0, "top": -1.0, "bottom": 1.0}[view]

    def curves(projected, layer):
        data = []
        for index, edge in enumerate(projected.Edges, 1):
            points = []
            try:
                samples = list(edge.discretize(Deflection=0.05))
            except Exception:
                samples = [vertex.Point for vertex in edge.Vertexes]
            for point in samples:
                values = _vector(point)
                points.append([round(horizontal_sign * values[1], 9), round(values[0], 9)])
            if len(points) >= 2:
                data.append({"id": f"{layer}-edge-{index}", "layer": layer, "points": points})
        return data

    visible, hidden = curves(visible_shape, "visible"), curves(hidden_shape, "hidden")
    if not visible and not hidden and getattr(shape, "ShapeType", "") == "Compound":
        # OCCT can occasionally return an empty HLR result for a valid
        # compound containing coincident/tangent solids even though each
        # constituent projects correctly.  Preserve B-Rep authority by
        # projecting the compound children independently in the same datum,
        # then let the caller validate the combined bounds against the source.
        for child_index, child in enumerate(shape.childShapes(), 1):
            if child.isNull():
                continue
            try:
                child_projection = _hlr_projection(child, view)
            except PhysicalDesignError:
                continue
            for layer, destination in (("visible", visible), ("hidden", hidden)):
                for curve in child_projection[layer]:
                    destination.append({**curve,
                                        "id": f"child-{child_index}-{curve['id']}"})
    if not visible and not hidden:
        raise PhysicalDesignError(f"OCCT HLR produced no edges for {view}")
    # HLR's local origin is view-dependent.  Translate only (never scale or
    # reshape) into the source B-Rep's canonical projected datum so the
    # exported geometry can be compared directly after FCStd reload.
    source_bounds = _source_projection_bounds(shape, view)
    projected_points = [point for layer in (visible, hidden)
                        for curve in layer for point in curve["points"]]
    projected_bounds = [min(point[0] for point in projected_points),
                        min(point[1] for point in projected_points),
                        max(point[0] for point in projected_points),
                        max(point[1] for point in projected_points)]
    dx, dy = source_bounds[0] - projected_bounds[0], source_bounds[1] - projected_bounds[1]
    for curve in visible + hidden:
        curve["points"] = [[round(point[0] + dx, 9), round(point[1] + dy, 9)]
                            for point in curve["points"]]
    return {"view": view, "direction": list(direction), "x_axis": list(x_axis),
            "y_axis": list(y_axis), "visible": visible, "hidden": hidden,
            "engine": ("occt-hlrbrep-compound-child-fallback"
                       if any(item["id"].startswith("child-") for item in visible + hidden)
                       else "occt-hlrbrep")}


def _source_projection_bounds(shape, view):
    _, x_axis, y_axis = _VIEW_BASIS[view]
    points = []
    for edge in shape.Edges:
        points.extend(_vector(point) for point in _edge_points(edge))
    values = [(_dot(point, x_axis), _dot(point, y_axis)) for point in points]
    return [min(item[0] for item in values), min(item[1] for item in values),
            max(item[0] for item in values), max(item[1] for item in values)]


def _curve_points(projection):
    return [point for layer in ("visible", "hidden")
            for curve in projection[layer] for point in curve["points"]]


def _projection_check(shape, projection):
    values = _curve_points(projection)
    if not values:
        raise PhysicalDesignError("projection has no finite geometry")
    xmin, ymin = min(point[0] for point in values), min(point[1] for point in values)
    xmax, ymax = max(point[0] for point in values), max(point[1] for point in values)
    source = _source_projection_bounds(shape, projection["view"])
    projected = [xmin, ymin, xmax, ymax]
    deviation = max(abs((projected[index + 2] - projected[index]) -
                        (source[index + 2] - source[index])) for index in (0, 1))
    if deviation > 0.05 + 1.0e-9:
        raise PhysicalDesignError(
            f"{projection['view']} HLR geometry differs from source by {deviation:.6f} mm")
    return {"source_bounds_mm": source, "projected_bounds_mm": projected,
            "max_deviation_mm": deviation, "passed": True}


def _section_geometry(shape, section):
    import FreeCAD as App
    import Part
    center = App.Vector(*section["plane_origin_mm"])
    normal = App.Vector(*section["plane_normal"])
    span = max(shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength, 1.0) * 4.0
    # ``makePlane`` takes its point as the corner of the finite plane, not its
    # centre.  Keep the requested section origin as that authoritative point;
    # the generous span covers the source envelope for the supported views.
    plane = Part.makePlane(span, span, center, normal)
    try:
        result = shape.section(plane)
    except Exception as exc:
        raise PhysicalDesignError(f"section {section['section_id']} failed: {exc}") from exc
    if result.isNull() or not result.Edges:
        raise PhysicalDesignError(f"section {section['section_id']} does not intersect source B-Rep")
    curves = []
    for index, edge in enumerate(result.Edges, 1):
        points = []
        try:
            samples = list(edge.discretize(Deflection=0.05))
        except Exception:
            samples = [vertex.Point for vertex in edge.Vertexes]
        points = [[round(float(point.x), 9), round(float(point.y), 9)] for point in samples]
        if len(points) >= 2:
            curves.append({"id": f"{section['section_id']}-edge-{index}", "layer": "section", "points": points})
    return curves


def _bounds(curves):
    points = [point for curve in curves for point in curve["points"]]
    if not points:
        return [0.0, 0.0, 1.0, 1.0]
    return [min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points)]


def _svg(curves, title, metadata, annotations, width=420.0, height=297.0):
    all_curves = list(curves)
    ext = _bounds(all_curves)
    sx = max(ext[2] - ext[0], 1.0e-9)
    sy = max(ext[3] - ext[1], 1.0e-9)
    scale = min((width - 70.0) / sx, (height - 75.0) / sy)
    ox, oy = 35.0 - ext[0] * scale, height - 45.0 + ext[1] * scale
    groups = []
    for layer, stroke, dash in (("visible", "#111111", ""), ("hidden", "#666666", "4,2"),
                                 ("section", "#b11a1a", ""), ("selected-boundary", "#0b5cad", "")):
        paths = []
        for curve in all_curves:
            if curve.get("layer") != layer:
                continue
            points = " ".join(f"{ox + point[0] * scale:.4f},{oy - point[1] * scale:.4f}" for point in curve["points"])
            paths.append(f'<polyline id="{escape(curve["id"])}" points="{points}"/>')
        if paths:
            groups.append(f'<g id="{layer}" fill="none" stroke="{stroke}" stroke-width="0.35"' +
                          (f' stroke-dasharray="{dash}"' if dash else "") + ">" + "".join(paths) + "</g>")
    dimension_lines = []
    for dimension in annotations.get("dimensions", []):
        dimension_lines.append(f'<text x="14" y="{height - 28 - len(dimension_lines) * 5:.3f}" font-size="4">{escape(dimension["label"])}: {dimension["value_mm"]:.3f} mm</text>')
    datum_lines = [f'<text x="{width - 100:.3f}" y="{18 + index * 5:.3f}" font-size="4">DATUM {escape(item["label"])} ({item["point_mm"][0]:.3f},{item["point_mm"][1]:.3f},{item["point_mm"][2]:.3f})</text>' for index, item in enumerate(annotations.get("datums", []))]
    tolerance_lines = [f'<text x="14" y="{height - 48 - index * 5:.3f}" font-size="4">TOL {escape(item["tolerance_id"])}: {item["nominal_mm"]:.3f} +{item["plus_mm"]:.3f}/-{item["minus_mm"]:.3f} mm</text>' for index, item in enumerate(annotations.get("tolerances", []))]
    gdt_lines = [f'<text x="{width - 100:.3f}" y="{height - 48 - index * 5:.3f}" font-size="4">GD&amp;T {escape(item["symbol"])} ⌖ {item["tolerance_mm"]:.3f} mm</text>' for index, item in enumerate(annotations.get("gdt", []))]
    continuity_lines = [f'<text x="14" y="{height - 68 - index * 5:.3f}" font-size="4">CONTINUITY {escape(item["boundary_id"])}: {escape(item["required"])} ≤ {item["max_normal_angle_deg"]:.3f}°</text>' for index, item in enumerate(annotations.get("continuity", []))]
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}mm" height="{height:g}mm" viewBox="0 0 {width:g} {height:g}">',
        f'<metadata>{escape(json.dumps(metadata, sort_keys=True, separators=(",", ":")))}</metadata>',
        f'<rect x="8" y="8" width="{width - 16:g}" height="{height - 16:g}" fill="white" stroke="#111" stroke-width="0.35"/>',
        f'<text x="14" y="18" font-size="6" font-family="DejaVu Sans">{escape(title)}</text>',
        *groups, *dimension_lines, *datum_lines, *tolerance_lines, *gdt_lines, *continuity_lines,
        f'<text x="14" y="{height - 14:g}" font-size="3.5">SOURCE B-REP SHA-256: {metadata["source_shape_sha256"]}</text>',
        f'<text x="{width - 14:g}" y="{height - 14:g}" text-anchor="end" font-size="3.5">UNITS: mm · {metadata["authority"]}</text>',
        '</svg>', ''])


def _dxf(curves, title, metadata, annotations):
    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES", "999", title,
             "999", f"AUTHORITY {metadata['authority']}", "999", f"SOURCE_BREP_SHA256 {metadata['source_shape_sha256']}",
             "999", f"PACKAGE_SHA256 {metadata['package_sha256']}"]
    layer_map = {"visible": "VISIBLE", "hidden": "HIDDEN", "section": "SECTION", "selected-boundary": "SELECTED"}
    for curve in curves:
        points = curve["points"]
        lines += ["0", "LWPOLYLINE", "8", layer_map.get(curve.get("layer"), "GEOMETRY"), "90", str(len(points)), "70", "0", "999", curve["id"]]
        for x, y in points:
            lines += ["10", f"{x:.9f}", "20", f"{y:.9f}"]
    for item in annotations.get("dimensions", []):
        lines += ["0", "TEXT", "8", "DIMENSIONS", "10", "0", "20", "0", "40", "3", "1", f"{item['label']} {item['value_mm']:.6f} mm"]
    for item in annotations.get("tolerances", []):
        lines += ["0", "TEXT", "8", "TOLERANCES", "10", "0", "20", "0", "40", "3", "1", f"{item['tolerance_id']} {item['nominal_mm']:.6f} +{item['plus_mm']:.6f}/-{item['minus_mm']:.6f} mm"]
    for item in annotations.get("gdt", []):
        lines += ["0", "TEXT", "8", "GD&T", "10", "0", "20", "0", "40", "3", "1", f"{item['symbol']} {item['tolerance_mm']:.6f} mm"]
    for item in annotations.get("continuity", []):
        lines += ["0", "TEXT", "8", "CONTINUITY", "10", "0", "20", "0", "40", "3", "1", f"{item['boundary_id']} {item['required']} <= {item['max_normal_angle_deg']:.6f} deg"]
    lines += ["0", "ENDSEC", "0", "EOF", ""]
    return "\n".join(lines)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"path": str(path), "sha256": file_digest(path), "bytes": path.stat().st_size}


def generate_authoritative_drawing_package(document, workspace_root, request_path):
    root = Path(workspace_root).expanduser().resolve()
    request_file, raw = _workspace_json(root, request_path)
    package = validate_authoritative_drawing_package(raw)
    document_path = (root / _relative(package["document"]["path"], "document.path")).resolve()
    if not document_path.is_file() or file_digest(document_path) != package["document"]["file_sha256"]:
        raise PhysicalDesignError("drawing document digest is stale")
    source = _find_semantic(document, package["source"]["semantic_id"])
    if source.Name != package["source"]["object_name"] or shape_digest(source.Shape) != package["source"]["shape_sha256"]:
        raise PhysicalDesignError("drawing source B-Rep digest or object identity is stale")
    for name in package["annotations"]["selected_faces"]:
        index = int(name[4:])
        if index > len(source.Shape.Faces):
            raise PhysicalDesignError(f"selected face {name} does not exist")
    for name in package["annotations"]["selected_edges"]:
        index = int(name[4:])
        if index > len(source.Shape.Edges):
            raise PhysicalDesignError(f"selected edge {name} does not exist")
    output = (root / _relative(package["output"]["directory"], "output.directory")).resolve()
    if not output.is_relative_to(root):
        raise PhysicalDesignError("drawing output escapes workspace")
    stage = output.parent / (f".{output.name}.staging-{uuid.uuid4().hex}")
    stage.mkdir(parents=True, exist_ok=False)
    try:
        source_digest = package["source"]["shape_sha256"]
        projections, checks = {}, {}
        for view in VIEWS:
            projection = _hlr_projection(source.Shape, view)
            projections[view] = projection
            checks[view] = _projection_check(source.Shape, projection)
        sections = []
        for section in package["sections"]:
            sections.extend(_section_geometry(source.Shape, section))
        selected_curves = []
        for edge_name in package["annotations"]["selected_edges"]:
            edge = source.Shape.Edges[int(edge_name[4:]) - 1]
            points = [[round(float(point.x), 9), round(float(point.y), 9)] for point in _edge_points(edge)]
            selected_curves.append({"id": f"selected-{edge_name}", "layer": "selected-boundary", "points": points})
        annotation = package["annotations"]
        # The package digest is computed from source and geometry records only;
        # each file's metadata therefore carries a stable identity before it is
        # written and the index can be hashed after artifact paths/digests exist.
        geometry_material = {"source_shape_sha256": source_digest, "projections": projections,
                             "sections": sections, "selected": selected_curves, "checks": checks}
        geometry_digest = canonical_digest(geometry_material)
        base_metadata = {"schema": "design-studio.authoritative-brep-view/1",
                         "source_shape_sha256": source_digest, "source_semantic_id": package["source"]["semantic_id"],
                         "geometry_digest": geometry_digest, "authority": "OCCT HLR/TechDraw B-Rep",
                         "package_id": package["package_id"],
                         "package_sha256": canonical_digest(package),
                         "selected_faces": package["annotations"]["selected_faces"],
                         "selected_edges": package["annotations"]["selected_edges"],
                         "continuity_ids": [item["continuity_id"] for item in package["annotations"]["continuity"]]}
        artifacts = []
        for view in VIEWS:
            curves = projections[view]["visible"] + projections[view]["hidden"]
            metadata = {**base_metadata, "view": view, "layers": ["visible", "hidden"]}
            artifacts.append({"view": view, "format": "svg", **_write(stage / f"{view}.svg", _svg(curves, f"{view.upper()} ORTHOGRAPHIC", metadata, annotation))})
            artifacts.append({"view": view, "format": "dxf", **_write(stage / f"{view}.dxf", _dxf(curves, f"{view.upper()} ORTHOGRAPHIC", metadata, annotation))})
        for section in package["sections"]:
            section_curves = [curve for curve in sections if curve["id"].startswith(section["section_id"] + "-")]
            metadata = {**base_metadata, "section_id": section["section_id"], "layers": ["section"]}
            artifacts.append({"section": section["section_id"], "format": "svg", **_write(stage / f"section-{section['section_id']}.svg", _svg(section_curves, section["label"], metadata, annotation))})
            artifacts.append({"section": section["section_id"], "format": "dxf", **_write(stage / f"section-{section['section_id']}.dxf", _dxf(section_curves, section["label"], metadata, annotation))})
        for stem, title in SHEETS:
            sheet_curves = []
            if stem.startswith("01"):
                sheet_curves = projections["front"]["visible"] + projections["top"]["visible"] + projections["right"]["visible"]
            elif stem.startswith("02"):
                sheet_curves = selected_curves or projections["front"]["visible"]
            elif stem.startswith("03"):
                sheet_curves = sections
            else:
                sheet_curves = projections["front"]["visible"]
            metadata = {**base_metadata, "sheet": stem, "layers": sorted({curve.get("layer") for curve in sheet_curves})}
            artifacts.append({"sheet": stem, "format": "svg", **_write(stage / f"{stem}.svg", _svg(sheet_curves, title, metadata, annotation))})
            artifacts.append({"sheet": stem, "format": "dxf", **_write(stage / f"{stem}.dxf", _dxf(sheet_curves, title, metadata, annotation))})
        complexity = len(source.Shape.Edges) + 2 * len(source.Shape.Faces) + 10 * len(package["sections"])
        extra_count = max(0, math.ceil(complexity / package["output"]["sheet_capacity"]) - 1)
        for index in range(extra_count):
            stem = f"06-detail-{index + 1:02d}"
            metadata = {**base_metadata, "sheet": stem, "layers": ["visible", "hidden"], "complexity": complexity}
            detail_projection = projections[VIEWS[index % len(VIEWS)]]
            curves = detail_projection["visible"] + detail_projection["hidden"]
            artifacts.append({"sheet": stem, "format": "svg", **_write(stage / f"{stem}.svg", _svg(curves, f"DETAIL {index + 1}", metadata, annotation))})
            artifacts.append({"sheet": stem, "format": "dxf", **_write(stage / f"{stem}.dxf", _dxf(curves, f"DETAIL {index + 1}", metadata, annotation))})
        # Artifact paths are package-relative, never staging paths containing
        # a random transaction directory.  Digests remain computed from the
        # staged bytes before the atomic rename.
        for artifact in artifacts:
            artifact["path"] = str(Path(artifact["path"]).relative_to(stage))
        package_index = {"schema": "design-studio.authoritative-drawing-index/1", "package_id": package["package_id"],
                         "revision": package["revision"], "source_semantic_id": package["source"]["semantic_id"],
                         "source_shape_sha256": source_digest, "geometry_digest": geometry_digest,
                         "projection_engine": "occt-hlrbrep", "view_checks": checks,
                         "artifact_count": len(artifacts), "artifacts": artifacts,
                         "created_utc": datetime.now(timezone.utc).isoformat()}
        package_index["package_digest"] = canonical_digest(package_index)
        _write(stage / "drawing-package-index.json", json.dumps(package_index, indent=2, sort_keys=True) + "\n")
        if output.exists():
            # Only replace the package directory, never a workspace-wide path.
            shutil.rmtree(output)
        stage.rename(output)
        receipt_dir = root / "contracts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = receipt_dir / f"authoritative-drawing-package-{package['package_id']}-r{package['revision']}.json"
        receipt = {"schema": SCHEMA, "request_path": str(request_file.relative_to(root)),
                   "request_sha256": canonical_digest(package), "index": package_index,
                   "output_directory": str(output.relative_to(root)), "source_shape_sha256": source_digest}
        receipt["receipt_sha256"] = canonical_digest(receipt)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"package": receipt, "receipt_path": str(receipt_path), "index_path": str(output / "drawing-package-index.json")}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def make_authoritative_request(document, workspace_root, source, output_directory,
                               sections=(), selected_faces=(), selected_edges=(), package_id=None):
    root = Path(workspace_root).expanduser().resolve()
    if not document.FileName:
        raise PhysicalDesignError("save the document before creating drawings")
    document_path = Path(document.FileName).resolve()
    if not document_path.is_relative_to(root):
        raise PhysicalDesignError("document must remain inside workspace")
    package_id = package_id or f"drawing-{uuid.uuid4().hex[:12]}"
    source_id = str(getattr(source, "DesignStudioSemanticId", "")).strip()
    if not source_id:
        raise PhysicalDesignError("source object needs a semantic ID")
    dimensions = [{"dimension_id": "overall-x", "kind": "overall", "value_mm": float(source.Shape.BoundBox.XLength), "label": "Overall X"},
                  {"dimension_id": "overall-y", "kind": "overall", "value_mm": float(source.Shape.BoundBox.YLength), "label": "Overall Y"},
                  {"dimension_id": "overall-z", "kind": "overall", "value_mm": float(source.Shape.BoundBox.ZLength), "label": "Overall Z"}]
    request = {"schema": SCHEMA, "package_id": package_id, "revision": 1,
               "document": {"document_id": document.Name, "path": str(document_path.relative_to(root)), "file_sha256": file_digest(document_path)},
               "source": {"semantic_id": source_id, "object_name": source.Name, "shape_sha256": shape_digest(source.Shape),
                          "role": str(getattr(source, "DesignStudioRole", "other")) if str(getattr(source, "DesignStudioRole", "other")) in {"enclosure", "mechanical", "local_redesign_preview", "assembly", "other"} else "other", "source_of_truth": "freecad_brep"},
               "views": list(VIEWS), "sections": list(sections),
               "annotations": {"selected_faces": list(selected_faces), "selected_edges": list(selected_edges), "datums": [], "dimensions": dimensions, "tolerances": [], "gdt": [], "continuity": []},
               "output": {"directory": output_directory, "formats": ["svg", "dxf"], "sheet_capacity": 100, "additional_sheets_policy": "deterministic_complexity_split"},
               "provenance": {"created_by": "DesignStudio authoritative drawing tool", "created_utc": datetime.now(timezone.utc).isoformat(), "geometry_authority": "freecad_brep", "raster_geometry_authority": False}}
    return validate_authoritative_drawing_package(request)
