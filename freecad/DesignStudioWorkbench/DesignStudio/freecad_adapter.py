"""FreeCAD-specific extraction for the neutral mechanical-contract snapshot."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


ROLE_PROPERTY = "DesignStudioRole"
ROLE_VALUES = ["none", "fixed_item", "connector", "mounting_hole",
               "height_zone", "cooling_zone", "service_clearance"]


def _canonical_bytes(value):
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _vec(value):
    return [float(value.x), float(value.y), float(value.z)]


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _unit(value):
    length = math.sqrt(_dot(value, value))
    if length <= 1e-12:
        raise ValueError("cannot construct a board frame from a zero-length vector")
    return [component / length for component in value]


def ensure_controller(document):
    controller = document.getObject("DesignStudioProject")
    if controller is None:
        controller = document.addObject("App::FeaturePython", "DesignStudioProject")
        controller.Label = "DesignStudio Project"
        controller.addProperty("App::PropertyFile", "ProjectPath", "DesignStudio")
        controller.addProperty("App::PropertyLinkSub", "BoardRegion", "DesignStudio")
        controller.addProperty("App::PropertyLength", "BoardThickness", "DesignStudio")
        controller.BoardThickness = 1.6
        controller.addProperty("App::PropertyString", "ContractDigest", "DesignStudio")
        controller.addProperty("App::PropertyString", "SyncStatus", "DesignStudio")
    return controller


def mark_object(obj, role: str) -> None:
    if role not in ROLE_VALUES:
        raise ValueError(f"unsupported DesignStudio role {role!r}")
    if ROLE_PROPERTY not in obj.PropertiesList:
        obj.addProperty("App::PropertyEnumeration", ROLE_PROPERTY, "DesignStudio")
        setattr(obj, ROLE_PROPERTY, ROLE_VALUES)
    setattr(obj, ROLE_PROPERTY, role)
    if role in ("fixed_item", "connector"):
        for prop, kind in (("RefDes", "App::PropertyString"),
                           ("MPN", "App::PropertyString"),
                           ("StepModel", "App::PropertyFile")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "DesignStudio")
    if role == "mounting_hole" and "HoleDiameter" not in obj.PropertiesList:
        obj.addProperty("App::PropertyLength", "HoleDiameter", "DesignStudio")
        obj.HoleDiameter = max(1.0, min(obj.Shape.BoundBox.XLength,
                                       obj.Shape.BoundBox.YLength))
    if role == "height_zone" and "MaxComponentHeight" not in obj.PropertiesList:
        obj.addProperty("App::PropertyLength", "MaxComponentHeight", "DesignStudio")
        obj.MaxComponentHeight = 3.0
    if role == "cooling_zone" and "KeepClear" not in obj.PropertiesList:
        obj.addProperty("App::PropertyBool", "KeepClear", "DesignStudio")
        obj.KeepClear = True


def set_board_region(controller, obj, subelement: str) -> None:
    controller.BoardRegion = (obj, [subelement])


def _selected_face(controller):
    value = controller.BoardRegion
    if not value or value[0] is None or not value[1]:
        raise ValueError("select a planar face and run Set Board Region first")
    face = value[0].getSubObject(value[1][0])
    if face is None or not getattr(face, "Wires", None):
        raise ValueError("BoardRegion does not resolve to a face")
    return value[0], value[1][0], face


def _frame(face):
    origin = _vec(face.Wires[0].OrderedVertexes[0].Point)
    normal = _unit(_vec(face.normalAt(0, 0)))
    x_axis = None
    for edge in face.Wires[0].Edges:
        p0, p1 = _vec(edge.Vertexes[0].Point), _vec(edge.Vertexes[-1].Point)
        delta = _sub(p1, p0)
        planar = _sub(delta, [normal[i] * _dot(delta, normal) for i in range(3)])
        if math.sqrt(_dot(planar, planar)) > 1e-9:
            x_axis = _unit(planar)
            break
    if x_axis is None:
        raise ValueError("board face has no usable edge")
    y_axis = _unit(_cross(normal, x_axis))
    return origin, x_axis, y_axis, normal


def _wire_polygon(wire, origin, x_axis, y_axis):
    points = []
    for vertex in wire.OrderedVertexes:
        delta = _sub(_vec(vertex.Point), origin)
        point = [round(_dot(delta, x_axis), 6), round(_dot(delta, y_axis), 6)]
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _area(points):
    return abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                   - points[(i + 1) % len(points)][0] * points[i][1]
                   for i in range(len(points))) / 2.0)


def _face_polygon(obj, origin, x_axis, y_axis):
    faces = getattr(obj.Shape, "Faces", [])
    if not faces:
        raise ValueError(f"{obj.Label} needs a planar face")
    return _wire_polygon(faces[0].Wires[0], origin, x_axis, y_axis)


def _project(point, origin, x_axis, y_axis):
    delta = _sub(point, origin)
    return [round(_dot(delta, x_axis), 6), round(_dot(delta, y_axis), 6)]


def _matrix_multiply(a, b):
    return [sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
            for row in range(4) for col in range(4)]


def component_world_transform(frame: dict, x_mm: float, y_mm: float,
                              rot_deg: float, model_to_footprint: list[float],
                              *, side: int = 0,
                              board_thickness_mm: float = 0.0) -> list[float]:
    """Component-local → FreeCAD-world row-major transform.

    Datasheet GLBs use Z=0 at the PCB surface.  Top-side parts are lifted to
    the board top; bottom-side parts are rotated under the substrate while
    preserving their footprint X/Y registration and rotation.
    """
    origin = frame["origin_mm"]
    x_axis, y_axis, z_axis = frame["x_axis"], frame["y_axis"], frame["z_axis"]
    board_to_world = [
        x_axis[0], y_axis[0], z_axis[0], origin[0],
        x_axis[1], y_axis[1], z_axis[1], origin[1],
        x_axis[2], y_axis[2], z_axis[2], origin[2],
        0, 0, 0, 1,
    ]
    angle = math.radians(rot_deg)
    c, s = math.cos(angle), math.sin(angle)
    if int(side) == 1:
        footprint_to_board = [c, s, 0, x_mm, s, -c, 0, y_mm,
                              0, 0, -1, 0, 0, 0, 0, 1]
    else:
        footprint_to_board = [c, -s, 0, x_mm, s, c, 0, y_mm,
                              0, 0, 1, board_thickness_mm, 0, 0, 0, 1]
    return _matrix_multiply(_matrix_multiply(board_to_world, footprint_to_board),
                            model_to_footprint)


def snapshot_from_document(document, controller) -> dict:
    board_obj, subelement, face = _selected_face(controller)
    origin, x_axis, y_axis, z_axis = _frame(face)
    wire_polygons = [_wire_polygon(wire, origin, x_axis, y_axis) for wire in face.Wires]
    outer_index = max(range(len(wire_polygons)), key=lambda index: _area(wire_polygons[index]))
    outline = wire_polygons[outer_index]
    cutouts = [polygon for index, polygon in enumerate(wire_polygons) if index != outer_index]

    min_x = min(point[0] for point in outline)
    min_y = min(point[1] for point in outline)
    shift_world = [x_axis[i] * min_x + y_axis[i] * min_y for i in range(3)]
    frame_origin = [origin[i] + shift_world[i] for i in range(3)]

    def shifted(points):
        return [[round(point[0] - min_x, 6), round(point[1] - min_y, 6)] for point in points]

    outline = shifted(outline)
    cutouts = [shifted(polygon) for polygon in cutouts]
    thickness = float(controller.BoardThickness.Value)
    board = {"outline_pts": outline, "cutouts": cutouts,
             "thickness_mm": thickness, "z_range_mm": [0.0, thickness],
             "mounting_holes": [], "height_zones": [], "cooling_zones": [],
             "service_clearances": []}
    fixed_items = []
    connectors = []

    for obj in document.Objects:
        role = getattr(obj, ROLE_PROPERTY, "none") if ROLE_PROPERTY in obj.PropertiesList else "none"
        if role == "none" or obj is controller:
            continue
        center_world = _vec(obj.Shape.BoundBox.Center)
        center = _project(center_world, frame_origin, x_axis, y_axis)
        if role == "mounting_hole":
            board["mounting_holes"].append({"id": obj.Name, "name": obj.Label,
                                             "center_mm": center,
                                             "diameter_mm": float(obj.HoleDiameter.Value)})
        elif role in ("height_zone", "cooling_zone", "service_clearance"):
            zone = {"id": obj.Name, "name": obj.Label,
                    "outline_pts": shifted(_face_polygon(obj, origin, x_axis, y_axis))}
            if role == "height_zone":
                zone["max_height_mm"] = float(obj.MaxComponentHeight.Value)
                board["height_zones"].append(zone)
            elif role == "cooling_zone":
                zone["keep_clear"] = bool(obj.KeepClear)
                board["cooling_zones"].append(zone)
            else:
                board["service_clearances"].append(zone)
        elif role in ("fixed_item", "connector"):
            bbox = obj.Shape.BoundBox
            item = {"id": obj.Name, "name": obj.Label,
                    "ref": getattr(obj, "RefDes", ""), "mpn": getattr(obj, "MPN", ""),
                    "x_mm": center[0], "y_mm": center[1], "rot_deg": 0.0,
                    "z_range_mm": [round(float(bbox.ZMin), 6), round(float(bbox.ZMax), 6)],
                    "step_model": str(getattr(obj, "StepModel", ""))}
            if role == "connector":
                item["edge_anchor"] = "none"
                connectors.append(item)
            else:
                fixed_items.append(item)

    source_path = str(getattr(document, "FileName", ""))
    source_hash = ""
    if source_path and Path(source_path).is_file():
        source_hash = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
    return {
        "source": {"kind": "freecad-document", "path": source_path,
                   "document_id": document.Name, "sha256": source_hash,
                   "board_object": board_obj.Name, "board_subelement": subelement},
        "frame_to_world": {"origin_mm": frame_origin, "x_axis": x_axis,
                           "y_axis": y_axis, "z_axis": z_axis},
        "board": board, "fixed_items": fixed_items,
        "connector_locations": connectors,
    }


def _project_frame(document, contract: dict) -> dict:
    frame = contract.get("frame_to_world") or {}
    required = ("origin_mm", "x_axis", "y_axis", "z_axis")
    if all(isinstance(frame.get(key), list) and len(frame[key]) == 3 for key in required):
        return frame
    # Compatibility for workspaces created before full frame persistence.  The
    # enclosure's legal volume is an origin hint, not a PCB size restriction.
    legal = document.getObject("LegalPCBVolume")
    if legal is not None and getattr(legal, "Shape", None) is not None:
        box = legal.Shape.BoundBox
        origin = [float(box.XMin), float(box.YMin), float(box.ZMin)]
    else:
        z_range = ((contract.get("board") or {}).get("z_range_mm") or [0.0, 1.6])
        origin = [0.0, 0.0, float(z_range[0])]
    return {"origin_mm": origin, "x_axis": [1.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0], "z_axis": [0.0, 0.0, 1.0]}


def _board_world_transform(frame: dict) -> list[float]:
    origin = frame["origin_mm"]
    x_axis, y_axis, z_axis = frame["x_axis"], frame["y_axis"], frame["z_axis"]
    return [x_axis[0], y_axis[0], z_axis[0], origin[0],
            x_axis[1], y_axis[1], z_axis[1], origin[1],
            x_axis[2], y_axis[2], z_axis[2], origin[2],
            0, 0, 0, 1]


def _sync_pcb_substrate(document, project: dict, contract: dict, frame: dict):
    import FreeCAD as App
    import Part

    outline = project.get("board_outline_pts") or []
    if len(outline) < 3:
        width = float(project.get("board_width_mm", 0.0))
        height = float(project.get("board_height_mm", 0.0))
        if width <= 0 or height <= 0:
            outline = (contract.get("board") or {}).get("outline_pts") or []
        else:
            outline = [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]
    if len(outline) < 3:
        raise ValueError("completed PCB has no valid substrate outline")
    thickness = float((contract.get("board") or {}).get("thickness_mm", 1.6))
    points = [App.Vector(float(point[0]), float(point[1]), 0.0) for point in outline]
    points.append(points[0])
    face = Part.Face(Part.makePolygon(points))
    for cutout in project.get("board_cutouts", []):
        if len(cutout) < 3:
            continue
        cut = [App.Vector(float(point[0]), float(point[1]), 0.0) for point in cutout]
        cut.append(cut[0])
        try:
            face = face.cut(Part.Face(Part.makePolygon(cut)))
        except Exception:
            continue
    board_shape = face.extrude(App.Vector(0, 0, thickness))
    obj = document.getObject("DS_PCB_Substrate")
    if obj is None:
        obj = document.addObject("Part::Feature", "DS_PCB_Substrate")
    obj.Label = "Completed PCB substrate"
    obj.Shape = board_shape
    obj.Placement = App.Placement(App.Matrix(*_board_world_transform(frame)))
    if getattr(obj, "ViewObject", None) is not None:
        obj.ViewObject.ShapeColor = (0.05, 0.34, 0.12)
        obj.ViewObject.LineColor = (0.75, 0.85, 0.70)
        obj.ViewObject.Visibility = True
    return obj, thickness


def sync_bound_components(document, controller) -> dict:
    """Build the completed PCB assembly from exact STEP or generated GLB assets."""
    project_path = Path(str(controller.ProjectPath)).expanduser()
    if not project_path.is_file():
        raise ValueError("linked DesignStudio project does not exist")
    project = json.loads(project_path.read_text(encoding="utf-8"))
    contract = project.get("mechanical_contract") or {}
    if contract.get("status") != "locked":
        raise ValueError("synchronize and lock the FreeCAD mechanical contract first")
    frame = _project_frame(document, contract)

    import FreeCAD as App
    import Mesh
    import Part

    board_object, board_thickness = _sync_pcb_substrate(
        document, project, contract, frame)

    group = document.getObject("DesignStudioComponents")
    if group is None:
        group = document.addObject("App::Part", "DesignStudioComponents")
        group.Label = "Completed PCB component assets"
    imported = 0
    seen = set()
    assets = []
    for footprint in project.get("footprints", []):
        reference = footprint.get("bound_component") or {}
        generated = footprint.get("asset_3d") or {}
        exact = reference.get("schema") == "design-studio.bound-component-ref/1"
        model = (reference.get("model_3d") or {}) if exact else generated
        expected_schema = "design-studio.component-3d-asset/1"
        if not exact and generated.get("schema") != expected_schema:
            assets.append({"ref": footprint.get("ref", ""), "status": "missing",
                           "generator": "", "message": "no STEP binding or generated GLB"})
            continue
        model_path = Path(model.get("asset_uri", "")).expanduser()
        if not model_path.is_file():
            raise ValueError(f"{footprint.get('ref')}: component 3D asset is missing")
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != model.get("sha256"):
            raise ValueError(f"{footprint.get('ref')}: component 3D asset digest mismatch")
        transform = model.get("model_to_footprint")
        if not isinstance(transform, list) or len(transform) != 16:
            raise ValueError(f"{footprint.get('ref')}: invalid model-to-footprint transform")
        ref = str(footprint.get("ref") or "").strip()
        if not ref:
            raise ValueError("3D footprint has no reference designator")
        name = "DS_" + "".join(char if char.isalnum() else "_" for char in ref)
        obj = document.getObject(name)
        wanted_type = "Part::Feature" if exact else "Mesh::Feature"
        if obj is not None and obj.TypeId != wanted_type:
            document.removeObject(name)
            document.recompute()
            obj = None
        if obj is None:
            obj = document.addObject(wanted_type, name)
            group.addObject(obj)
            obj.addProperty("App::PropertyString", "RefDes", "DesignStudio")
            obj.addProperty("App::PropertyString", "MPN", "DesignStudio")
        for prop, kind in (("AssetStatus", "App::PropertyString"),
                           ("AssetGenerator", "App::PropertyString"),
                           ("AssetDigest", "App::PropertyString"),
                           ("SourceAsset", "App::PropertyFile")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "DesignStudio")
        obj.Label = f"{ref} — {footprint.get('mpn', footprint.get('lib', 'component'))}"
        obj.RefDes = ref
        obj.MPN = str(footprint.get("mpn", ""))
        obj.AssetStatus = "exact_step" if exact else str(generated.get("status", "ready"))
        obj.AssetGenerator = "supplier-step-binding" if exact else str(generated.get("generator", ""))
        obj.AssetDigest = str(model.get("sha256", ""))
        obj.SourceAsset = str(model_path.resolve())
        if exact:
            obj.Shape = Part.read(str(model_path))
        else:
            from .glb_mesh import read_glb
            glb_primitives = read_glb(model_path)
            facets = []
            colors = []
            for primitive in glb_primitives:
                vertices = primitive["vertices_mm"]
                for triangle in primitive["triangles"]:
                    facets.append(tuple(App.Vector(*vertices[index]) for index in triangle))
                colors.append(primitive.get("color", [0.12, 0.14, 0.16, 1.0]))
            obj.Mesh = Mesh.Mesh(facets)
            if getattr(obj, "ViewObject", None) is not None and colors:
                average = tuple(sum(float(color[channel]) for color in colors) / len(colors)
                                for channel in range(3))
                obj.ViewObject.ShapeColor = average
        world = component_world_transform(
            frame, float(footprint.get("x_mm", 0)), float(footprint.get("y_mm", 0)),
            float(footprint.get("rot_deg", 0)), [float(value) for value in transform],
            side=int(footprint.get("side", 0)), board_thickness_mm=board_thickness)
        obj.Placement = App.Placement(App.Matrix(*world))
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.Visibility = True
        seen.add(name)
        imported += 1
        assets.append({"ref": ref, "status": obj.AssetStatus,
                       "generator": obj.AssetGenerator, "asset": str(model_path.resolve())})
    for child in group.Group:
        if child.Name.startswith("DS_") and child.Name not in seen:
            if getattr(child, "ViewObject", None) is not None:
                child.ViewObject.Visibility = False
    document.recompute()
    report = component_collision_report(document, project, project_path.read_bytes())
    report_path = project_path.with_suffix(".mechanical-collision.json")
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    return {"imported": imported, "board": board_object.Name, "assets": assets,
        "ready": sum(item["status"] in ("ready", "exact_step") for item in assets),
        "pending": sum(item["status"] in ("proxy_ready", "cloud_pending", "cloud_failed")
                       for item in assets),
        "missing": sum(item["status"] == "missing" for item in assets),
        "hidden_stale": sum(
        1 for child in group.Group if child.Name.startswith("DS_") and child.Name not in seen),
        "collision_status": report["status"], "collisions": len(report["collisions"]),
        "collision_report": str(report_path)}


def component_collision_report(document, project: dict, project_bytes: bytes) -> dict:
    """Intersect synchronized component solids and marked fixed solids in FreeCAD."""
    group = document.getObject("DesignStudioComponents")
    components = [obj for obj in (getattr(group, "Group", []) if group else [])
                  if obj.Name.startswith("DS_")
                  and getattr(getattr(obj, "ViewObject", None), "Visibility", True)]
    fixed = [obj for obj in document.Objects
             if getattr(obj, ROLE_PROPERTY, "none") in ("fixed_item", "connector")]
    collisions = []
    incomplete = [{"a": str(getattr(obj, "RefDes", "") or obj.Name),
                   "b": "mechanical_collision_check", "kind": "generated_mesh",
                   "reason": "datasheet GLB is a visualization mesh, not a release-qualified solid"}
                  for obj in components if getattr(obj, "TypeId", "") == "Mesh::Feature"]
    solid_components = [obj for obj in components
                        if getattr(obj, "TypeId", "") != "Mesh::Feature"]

    def refdes(obj):
        return str(getattr(obj, "RefDes", "") or obj.Name)

    def check(a, b, kind):
        if kind == "fixed_item" and refdes(a) == refdes(b):
            return
        try:
            common = a.Shape.common(b.Shape)
            volume = float(getattr(common, "Volume", 0.0))
            if volume > 1e-9:
                collisions.append({"a": refdes(a), "b": refdes(b), "kind": kind,
                                   "intersection_volume_mm3": round(volume, 9)})
        except Exception as exc:
            incomplete.append({"a": refdes(a), "b": refdes(b), "kind": kind,
                               "reason": str(exc)})

    for index, component in enumerate(solid_components):
        for other in solid_components[index + 1:]:
            check(component, other, "component_component")
        for obstacle in fixed:
            check(component, obstacle, "fixed_item")

    status = "fail" if collisions else "incomplete" if incomplete else "pass"
    report = {
        "schema": "design-studio.mechanical-collision/1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "project": {
            "document_id": project.get("document_id", ""),
            "revision": int(project.get("revision", 0)),
            "file_sha256": hashlib.sha256(project_bytes).hexdigest(),
        },
        "contract_digest": (project.get("mechanical_contract") or {}).get(
            "contract_digest", ""),
        "components_checked": len(components),
        "fixed_items_checked": len(fixed),
        "collisions": collisions,
        "incomplete_checks": incomplete,
        "method": "FreeCAD Part.Shape.common solid intersection",
    }
    report["report_digest"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report
