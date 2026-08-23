"""Revision-bound physical-design and local-redesign operations.

Images and sketches are evidence in this module, never manufacturing geometry.
Only a validated typed CAD program rebuilt by FreeCAD may become a candidate.
Candidate geometry is first kept in a visible sibling branch; commit revalidates
all digests, transfers the stable semantic identity, and retains the baseline.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import math
from pathlib import Path
import re
import uuid


SESSION_SCHEMA = "design-studio.physical-design-session/1"
REDESIGN_SCHEMA = "design-studio.local-redesign/1"
MANIFEST_SCHEMA = "design-studio.semantic-assembly/1"
PREVIEW_SCHEMA = "design-studio.local-redesign-preview/1"
COMMIT_SCHEMA = "design-studio.local-redesign-commit/1"
VIEWS = ("front", "rear", "left", "right", "top", "bottom")
FORMATS = ("svg", "dxf")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class PhysicalDesignError(ValueError):
    """A physical-design request failed a deterministic trust check."""


def _strict(value, allowed, required, label):
    if not isinstance(value, dict):
        raise PhysicalDesignError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise PhysicalDesignError(
            f"{label} keys invalid; unknown={sorted(unknown)}, missing={sorted(missing)}")


def _finite(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PhysicalDesignError(f"{label} must be a finite number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise PhysicalDesignError(f"{label} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise PhysicalDesignError(f"{label} must be <= {maximum}")
    return number


def _digest(value, label):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PhysicalDesignError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _relative(value, label):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise PhysicalDesignError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PhysicalDesignError(f"{label} must remain inside the workspace")
    return path


def _point3(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise PhysicalDesignError(f"{label} must contain three coordinates")
    return [_finite(item, f"{label}[{index}]", -1_000_000, 1_000_000)
            for index, item in enumerate(value)]


def _bounds(value, label):
    _strict(value, {"min_mm", "max_mm"}, {"min_mm", "max_mm"}, label)
    low = _point3(value["min_mm"], f"{label}.min_mm")
    high = _point3(value["max_mm"], f"{label}.max_mm")
    if any(high[index] < low[index] for index in range(3)):
        raise PhysicalDesignError(f"{label} has reversed coordinates")
    return {"min_mm": low, "max_mm": high}


def canonical_digest(value) -> str:
    try:
        material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PhysicalDesignError(f"value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(material).hexdigest()


def file_digest(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique(items, label):
    if len(items) != len(set(items)):
        raise PhysicalDesignError(f"{label} must not contain duplicates")


def validate_physical_design_session(value, workspace_root=None):
    session = deepcopy(value)
    allowed = {
        "schema", "session_id", "revision", "workflow_mode", "document", "intent",
        "input_assets", "occupied_volumes", "desire_capture", "material_constraints",
        "manufacturing_constraints", "geometry_authority", "provenance",
    }
    _strict(session, allowed, allowed, "physical design session")
    if session["schema"] != SESSION_SCHEMA:
        raise PhysicalDesignError(f"schema must be {SESSION_SCHEMA}")
    if not isinstance(session["session_id"], str) or not _IDENTIFIER.fullmatch(session["session_id"]):
        raise PhysicalDesignError("session_id is invalid")
    if not isinstance(session["revision"], int) or isinstance(session["revision"], bool) or session["revision"] < 1:
        raise PhysicalDesignError("revision must be a positive integer")
    if session["workflow_mode"] not in {"inside_out", "outside_in", "co_design"}:
        raise PhysicalDesignError("workflow_mode is invalid")
    if not isinstance(session["intent"], str) or not session["intent"].strip():
        raise PhysicalDesignError("intent must not be empty")

    document = session["document"]
    _strict(document, {"document_id", "path", "sha256"},
            {"document_id", "path", "sha256"}, "document")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise PhysicalDesignError("document_id must not be empty")
    document_path = _relative(document["path"], "document.path")
    _digest(document["sha256"], "document.sha256")

    assets = session["input_assets"]
    if not isinstance(assets, list) or len(assets) > 256:
        raise PhysicalDesignError("input_assets must contain at most 256 entries")
    asset_ids = []
    root = Path(workspace_root).resolve() if workspace_root is not None else None
    for index, asset in enumerate(assets):
        label = f"input_assets[{index}]"
        keys = {"asset_id", "path", "sha256", "media_type", "input_kind", "role",
                "geometry_authority", "measurement_status"}
        _strict(asset, keys, keys, label)
        if not isinstance(asset["asset_id"], str) or not asset["asset_id"]:
            raise PhysicalDesignError(f"{label}.asset_id must not be empty")
        asset_ids.append(asset["asset_id"])
        relative = _relative(asset["path"], f"{label}.path")
        claimed = _digest(asset["sha256"], f"{label}.sha256")
        if asset["media_type"] not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml", "application/pdf"}:
            raise PhysicalDesignError(f"{label}.media_type is unsupported")
        if asset["input_kind"] not in {"object_photo", "render", "hand_sketch", "vector_sketch", "engineering_drawing", "scan"}:
            raise PhysicalDesignError(f"{label}.input_kind is invalid")
        if asset["role"] not in {"inspiration", "silhouette", "layout", "measurement_reference", "material_reference"}:
            raise PhysicalDesignError(f"{label}.role is invalid")
        if asset["geometry_authority"] is not False:
            raise PhysicalDesignError(f"{label}: raster/vector reference cannot be geometry authority")
        if asset["measurement_status"] not in {"none", "uncalibrated", "user_calibrated", "drawing_dimensions_verified"}:
            raise PhysicalDesignError(f"{label}.measurement_status is invalid")
        if root is not None:
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise PhysicalDesignError(f"{label}.path does not identify a workspace asset")
            if file_digest(resolved) != claimed:
                raise PhysicalDesignError(f"{label} digest does not match the asset")
    _unique(asset_ids, "input asset IDs")

    volumes = session["occupied_volumes"]
    if not isinstance(volumes, list) or len(volumes) > 4096:
        raise PhysicalDesignError("occupied_volumes must contain at most 4096 entries")
    volume_ids = []
    for index, volume in enumerate(volumes):
        label = f"occupied_volumes[{index}]"
        keys = {"volume_id", "semantic_id", "kind", "min_mm", "max_mm",
                "clearance_mm", "locked", "source"}
        _strict(volume, keys, keys, label)
        volume_ids.append(volume["volume_id"])
        low = _point3(volume["min_mm"], f"{label}.min_mm")
        high = _point3(volume["max_mm"], f"{label}.max_mm")
        if any(high[i] <= low[i] for i in range(3)):
            raise PhysicalDesignError(f"{label} must have positive volume")
        _finite(volume["clearance_mm"], f"{label}.clearance_mm", 0, 10000)
        if volume["locked"] not in (True, False):
            raise PhysicalDesignError(f"{label}.locked must be boolean")
    _unique(volume_ids, "occupied volume IDs")

    if not isinstance(session["desire_capture"], list) or len(session["desire_capture"]) > 128:
        raise PhysicalDesignError("desire_capture is invalid")
    for index, desire in enumerate(session["desire_capture"]):
        keys = {"desire_id", "statement", "priority", "status"}
        _strict(desire, keys, keys, f"desire_capture[{index}]")
        if not desire["statement"] or desire["priority"] not in {"required", "preferred", "exploratory"}:
            raise PhysicalDesignError(f"desire_capture[{index}] is invalid")
        if desire["status"] not in {"unverified", "user_confirmed", "measured"}:
            raise PhysicalDesignError(f"desire_capture[{index}].status is invalid")

    if not isinstance(session["material_constraints"], list) or len(session["material_constraints"]) > 128:
        raise PhysicalDesignError("material_constraints is invalid")
    for index, material in enumerate(session["material_constraints"]):
        keys = {"region_id", "candidate_materials", "requirements", "evidence_status"}
        _strict(material, keys, keys, f"material_constraints[{index}]")
        if not material["candidate_materials"] or not all(
                isinstance(item, str) and item for item in material["candidate_materials"]):
            raise PhysicalDesignError(f"material_constraints[{index}] needs candidate materials")
        if material["evidence_status"] not in {"unverified", "datasheet_backed", "test_backed"}:
            raise PhysicalDesignError(f"material_constraints[{index}].evidence_status is invalid")

    manufacturing = session["manufacturing_constraints"]
    mkeys = {"processes", "units", "minimum_wall_mm", "minimum_feature_mm", "status"}
    _strict(manufacturing, mkeys, mkeys, "manufacturing_constraints")
    if not manufacturing["processes"] or manufacturing["units"] != "mm":
        raise PhysicalDesignError("manufacturing constraints require processes and millimetres")
    _finite(manufacturing["minimum_wall_mm"], "minimum_wall_mm", 1e-9, 1000)
    _finite(manufacturing["minimum_feature_mm"], "minimum_feature_mm", 1e-9, 1000)
    if manufacturing["status"] not in {"provisional", "process_reviewed", "supplier_confirmed"}:
        raise PhysicalDesignError("manufacturing status is invalid")

    authority = session["geometry_authority"]
    akeys = {"authoritative_sources", "raster_and_sketch_are_geometry_authority",
             "unknown_dimensions_policy"}
    _strict(authority, akeys, akeys, "geometry_authority")
    if authority["authoritative_sources"] != [
            "freecad_brep", "typed_mechanical_cad_program", "verified_measurements"]:
        raise PhysicalDesignError("geometry authority sources are fixed by the trust contract")
    if authority["raster_and_sketch_are_geometry_authority"] is not False:
        raise PhysicalDesignError("raster and sketch inputs cannot be geometry authority")
    if authority["unknown_dimensions_policy"] != "blocked_until_measured_or_explicitly_constrained":
        raise PhysicalDesignError("unknown dimensions must remain blocked")
    provenance = session["provenance"]
    pkeys = {"created_by", "created_utc", "user_prompt"}
    _strict(provenance, pkeys, pkeys, "provenance")
    if not all(isinstance(provenance[key], str) and provenance[key] for key in pkeys):
        raise PhysicalDesignError("session provenance is incomplete")
    if root is not None:
        resolved_document = (root / document_path).resolve()
        if not resolved_document.is_relative_to(root) or not resolved_document.is_file():
            raise PhysicalDesignError("document.path does not identify a workspace document")
        if file_digest(resolved_document) != document["sha256"]:
            raise PhysicalDesignError("session document digest is stale")
    return session


def validate_local_redesign(value):
    redesign = deepcopy(value)
    allowed = {
        "schema", "redesign_id", "revision", "status", "document",
        "physical_design_session_path", "selection", "intent", "constraints",
        "program", "candidate_command_id", "drawing_output", "provenance",
    }
    required = allowed - {"physical_design_session_path"}
    _strict(redesign, allowed, required, "local redesign")
    if redesign["schema"] != REDESIGN_SCHEMA:
        raise PhysicalDesignError(f"schema must be {REDESIGN_SCHEMA}")
    if not isinstance(redesign["redesign_id"], str) or not _IDENTIFIER.fullmatch(redesign["redesign_id"]):
        raise PhysicalDesignError("redesign_id is invalid")
    if not isinstance(redesign["revision"], int) or isinstance(redesign["revision"], bool) or redesign["revision"] < 1:
        raise PhysicalDesignError("revision must be positive")
    if redesign["status"] not in {"captured", "program_ready"}:
        raise PhysicalDesignError("redesign status is invalid")
    document = redesign["document"]
    _strict(document, {"document_id", "path", "file_sha256"},
            {"document_id", "path", "file_sha256"}, "document")
    _relative(document["path"], "document.path")
    _digest(document["file_sha256"], "document.file_sha256")
    if "physical_design_session_path" in redesign and redesign["physical_design_session_path"] is not None:
        _relative(redesign["physical_design_session_path"], "physical_design_session_path")

    selection = redesign["selection"]
    skeys = {"semantic_id", "object_name", "kind", "subelements",
             "target_shape_sha256", "selection_shape_sha256", "bounds_mm"}
    _strict(selection, skeys, skeys, "selection")
    if not selection["semantic_id"] or not selection["object_name"]:
        raise PhysicalDesignError("selection identity is incomplete")
    if selection["kind"] not in {"object", "faces"}:
        raise PhysicalDesignError("selection.kind must be object or faces")
    if not isinstance(selection["subelements"], list):
        raise PhysicalDesignError("selection.subelements must be an array")
    _unique(selection["subelements"], "selected subelements")
    if selection["kind"] == "object" and selection["subelements"]:
        raise PhysicalDesignError("object selection cannot contain subelements")
    if selection["kind"] == "faces" and (not selection["subelements"] or not all(
            isinstance(item, str) and re.fullmatch(r"Face[1-9][0-9]*", item)
            for item in selection["subelements"])):
        raise PhysicalDesignError("face selection requires valid FaceN subelements")
    _digest(selection["target_shape_sha256"], "selection.target_shape_sha256")
    _digest(selection["selection_shape_sha256"], "selection.selection_shape_sha256")
    selection["bounds_mm"] = _bounds(selection["bounds_mm"], "selection.bounds_mm")

    if not isinstance(redesign["intent"], str) or not redesign["intent"].strip():
        raise PhysicalDesignError("redesign intent must not be empty")
    constraints = redesign["constraints"]
    ckeys = {"max_expansion_mm", "outside_scope_tolerance_mm3", "protected_objects",
             "unselected_objects_must_remain_unchanged", "baseline_must_be_retained"}
    _strict(constraints, ckeys, ckeys, "constraints")
    _finite(constraints["max_expansion_mm"], "max_expansion_mm", 0, 10000)
    _finite(constraints["outside_scope_tolerance_mm3"],
            "outside_scope_tolerance_mm3", 0, 1_000_000)
    if constraints["unselected_objects_must_remain_unchanged"] is not True:
        raise PhysicalDesignError("unselected objects must remain unchanged")
    if constraints["baseline_must_be_retained"] is not True:
        raise PhysicalDesignError("the baseline must be retained")
    if not isinstance(constraints["protected_objects"], list):
        raise PhysicalDesignError("protected_objects must be an array")
    protected_ids = []
    for index, item in enumerate(constraints["protected_objects"]):
        pkeys = {"semantic_id", "shape_sha256", "clearance_mm"}
        _strict(item, pkeys, pkeys, f"protected_objects[{index}]")
        protected_ids.append(item["semantic_id"])
        _digest(item["shape_sha256"], f"protected_objects[{index}].shape_sha256")
        _finite(item["clearance_mm"], f"protected_objects[{index}].clearance_mm", 0, 10000)
    _unique(protected_ids, "protected semantic IDs")
    if selection["semantic_id"] in protected_ids:
        raise PhysicalDesignError("the redesign target cannot also be protected")

    if redesign["status"] == "captured":
        if redesign["program"] is not None or redesign["candidate_command_id"] is not None:
            raise PhysicalDesignError("captured redesign cannot contain a CAD program")
    else:
        if not isinstance(redesign["program"], dict) or not redesign["candidate_command_id"]:
            raise PhysicalDesignError("program_ready redesign requires program and candidate command")
        from .mechanical_cad import validate_program
        redesign["program"] = validate_program(redesign["program"])
        command_ids = {command["id"] for command in redesign["program"]["commands"]}
        if redesign["candidate_command_id"] not in command_ids:
            raise PhysicalDesignError("candidate_command_id is not produced by the CAD program")

    drawing = redesign["drawing_output"]
    dkeys = {"directory", "views", "formats", "source"}
    _strict(drawing, dkeys, dkeys, "drawing_output")
    _relative(drawing["directory"], "drawing_output.directory")
    if drawing["views"] != list(VIEWS) or drawing["formats"] != list(FORMATS):
        raise PhysicalDesignError("drawing output must contain six SVG and six DXF views")
    if drawing["source"] != "candidate_freecad_brep":
        raise PhysicalDesignError("drawings must be projected from candidate FreeCAD B-Rep")
    provenance = redesign["provenance"]
    pkeys = {"created_by", "created_utc", "model_role", "raster_geometry_authority"}
    _strict(provenance, pkeys, pkeys, "provenance")
    if provenance["model_role"] not in {"user", "codex_proposal", "deterministic_host"}:
        raise PhysicalDesignError("provenance.model_role is invalid")
    if provenance["raster_geometry_authority"] is not False:
        raise PhysicalDesignError("raster geometry authority must remain false")
    return redesign


def _add_property(obj, kind, name, group, value):
    if name not in getattr(obj, "PropertiesList", []):
        obj.addProperty(kind, name, group)
    setattr(obj, name, value)


def shape_digest(shape) -> str:
    if shape is None or shape.isNull():
        raise PhysicalDesignError("object has no usable B-Rep shape")
    def rounded(value):
        return round(float(value), 9)

    def vector(value):
        return [rounded(value.x), rounded(value.y), rounded(value.z)]

    def bounds(item):
        box = item.BoundBox
        return [rounded(value) for value in (
            box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)]

    def center(item):
        return vector(getattr(item, "CenterOfMass", item.BoundBox.Center))

    # OpenCascade's textual BREP serializer contains recompute-dependent
    # internal ordering.  Hash a canonical, B-Rep-derived topology signature
    # instead so an unchanged object keeps the same identity across recompute
    # and FCStd reload while still detecting geometric/topological mutations.
    edges = sorted((rounded(edge.Length), center(edge), bounds(edge))
                   for edge in shape.Edges)
    faces = sorted((rounded(face.Area), center(face), bounds(face),
                    len(face.Edges)) for face in shape.Faces)
    vertices = sorted(vector(vertex.Point) for vertex in shape.Vertexes)
    signature = {
        "shape_type": shape.ShapeType,
        "bounds": bounds(shape),
        "volume": rounded(getattr(shape, "Volume", 0.0)),
        "area": rounded(getattr(shape, "Area", 0.0)),
        "length": rounded(getattr(shape, "Length", 0.0)),
        "center_of_mass": center(shape),
        "solid_count": len(shape.Solids),
        "shell_count": len(shape.Shells),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "vertex_count": len(shape.Vertexes),
        "vertices": vertices,
        "edges": edges,
        "faces": faces,
    }
    return canonical_digest(signature)


def _shape_bounds(shape):
    box = shape.BoundBox
    return {"min_mm": [float(box.XMin), float(box.YMin), float(box.ZMin)],
            "max_mm": [float(box.XMax), float(box.YMax), float(box.ZMax)]}


def ensure_semantic_id(obj, role="physical_object") -> str:
    current = str(getattr(obj, "DesignStudioSemanticId", "")).strip()
    if not current:
        document = getattr(obj, "Document", None)
        source = f"{getattr(document, 'FileName', '') or getattr(document, 'Name', '')}:{obj.Name}"
        current = str(uuid.uuid5(uuid.NAMESPACE_URL, "designstudio-semantic:" + source))
        _add_property(obj, "App::PropertyString", "DesignStudioSemanticId",
                      "DesignStudio Semantic", current)
    _add_property(obj, "App::PropertyString", "DesignStudioRole",
                  "DesignStudio Semantic", str(role or "physical_object"))
    return current


def semantic_assembly_manifest(document):
    entries = []
    seen = set()
    for obj in getattr(document, "Objects", []):
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        semantic_id = ensure_semantic_id(obj, str(getattr(obj, "DesignStudioRole", "physical_object")))
        if semantic_id in seen:
            raise PhysicalDesignError(f"duplicate semantic ID in document: {semantic_id}")
        seen.add(semantic_id)
        view = getattr(obj, "ViewObject", None)
        color = list(getattr(view, "ShapeColor", ())) if view is not None else []
        entries.append({
            "semantic_id": semantic_id,
            "object_name": obj.Name,
            "label": obj.Label,
            "role": str(getattr(obj, "DesignStudioRole", "physical_object")),
            "shape_sha256": shape_digest(shape),
            "bounds_mm": _shape_bounds(shape),
            "material": str(getattr(obj, "MaterialName", "")),
            "electrical_reference": str(getattr(obj, "ReferenceDesignator", "")),
            "color_rgb": [round(float(item), 6) for item in color[:3]],
            "visible": bool(getattr(view, "Visibility", True)) if view is not None else True,
        })
    entries.sort(key=lambda item: item["semantic_id"])
    manifest = {"schema": MANIFEST_SCHEMA, "document_id": document.Name,
                "objects": entries}
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def _semantic_objects(document):
    result = {}
    for obj in getattr(document, "Objects", []):
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        semantic_id = str(getattr(obj, "DesignStudioSemanticId", "")).strip()
        if semantic_id:
            if semantic_id in result:
                raise PhysicalDesignError(f"duplicate semantic ID in document: {semantic_id}")
            result[semantic_id] = obj
    return result


def _semantic_snapshot(document):
    return {semantic_id: shape_digest(obj.Shape)
            for semantic_id, obj in _semantic_objects(document).items()}


def _find_semantic(document, semantic_id):
    obj = _semantic_objects(document).get(semantic_id)
    if obj is None:
        raise PhysicalDesignError(f"semantic object no longer exists: {semantic_id}")
    return obj


def _selected_shape(obj, kind, subelements):
    if kind == "object":
        return obj.Shape
    try:
        import Part
        faces = [obj.Shape.Faces[int(name[4:]) - 1] for name in subelements]
        return Part.makeCompound(faces)
    except (IndexError, ValueError) as exc:
        raise PhysicalDesignError("selected face topology no longer exists") from exc


def create_physical_design_session(workspace_root, value):
    root = Path(workspace_root).expanduser().resolve()
    session = validate_physical_design_session(value, root)
    destination = root / "contracts" / f"physical-design-session-{session['session_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    return {"path": str(destination), "session_sha256": canonical_digest(session),
            "workflow_mode": session["workflow_mode"],
            "input_assets": len(session["input_assets"]),
            "occupied_volumes": len(session["occupied_volumes"])}


def capture_local_redesign(document, workspace_root, intent, max_expansion_mm=0.0,
                           protected_semantic_ids=(), selection_ex=None,
                           physical_design_session_path=None):
    root = Path(workspace_root).expanduser().resolve()
    if selection_ex is None:
        import FreeCADGui as Gui
        selection_ex = Gui.Selection.getSelectionEx()
    if len(selection_ex) != 1:
        raise PhysicalDesignError("select exactly one FreeCAD object or faces on one object")
    selected = selection_ex[0]
    obj = selected.Object
    if obj is None or getattr(obj, "Document", None) is not document:
        raise PhysicalDesignError("selection must belong to the active mechanical document")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise PhysicalDesignError("selected object has no B-Rep shape")
    names = list(getattr(selected, "SubElementNames", []) or [])
    if names and not all(re.fullmatch(r"Face[1-9][0-9]*", name) for name in names):
        raise PhysicalDesignError("select the whole object or faces only; edges and vertices are ambiguous")
    names = list(dict.fromkeys(names))
    kind = "faces" if names else "object"
    semantic_id = ensure_semantic_id(obj, str(getattr(obj, "DesignStudioRole", "physical_object")))
    protected = []
    available = _semantic_objects(document)
    for protected_id in protected_semantic_ids:
        protected_obj = available.get(str(protected_id))
        if protected_obj is None:
            raise PhysicalDesignError(f"protected semantic object does not exist: {protected_id}")
        if protected_obj is obj:
            raise PhysicalDesignError("selected object cannot also be protected")
        protected.append({"semantic_id": str(protected_id),
                          "shape_sha256": shape_digest(protected_obj.Shape),
                          "clearance_mm": 0.0})
    document.recompute()
    if document.FileName:
        document.save()
    document_path = Path(document.FileName).resolve()
    if not document_path.is_relative_to(root):
        raise PhysicalDesignError("active mechanical document must be inside the workspace")
    redesign_id = "redesign-" + uuid.uuid4().hex[:16]
    selection_shape = _selected_shape(obj, kind, names)
    session_relative = None
    if physical_design_session_path:
        session_path = Path(physical_design_session_path).expanduser().resolve()
        if not session_path.is_relative_to(root) or not session_path.is_file():
            raise PhysicalDesignError("physical design session must be inside the workspace")
        validate_physical_design_session(
            json.loads(session_path.read_text(encoding="utf-8")), root)
        session_relative = str(session_path.relative_to(root))
    contract = {
        "schema": REDESIGN_SCHEMA,
        "redesign_id": redesign_id,
        "revision": 1,
        "status": "captured",
        "document": {"document_id": document.Name,
                     "path": str(document_path.relative_to(root)),
                     "file_sha256": file_digest(document_path)},
        "physical_design_session_path": session_relative,
        "selection": {
            "semantic_id": semantic_id,
            "object_name": obj.Name,
            "kind": kind,
            "subelements": names,
            "target_shape_sha256": shape_digest(obj.Shape),
            "selection_shape_sha256": shape_digest(selection_shape),
            "bounds_mm": _shape_bounds(selection_shape),
        },
        "intent": str(intent).strip(),
        "constraints": {
            "max_expansion_mm": _finite(max_expansion_mm, "max_expansion_mm", 0, 10000),
            "outside_scope_tolerance_mm3": 0.001,
            "protected_objects": protected,
            "unselected_objects_must_remain_unchanged": True,
            "baseline_must_be_retained": True,
        },
        "program": None,
        "candidate_command_id": None,
        "drawing_output": {
            "directory": f"generated/redesign/{redesign_id}",
            "views": list(VIEWS), "formats": list(FORMATS),
            "source": "candidate_freecad_brep",
        },
        "provenance": {
            "created_by": "DesignStudio selection capture",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "model_role": "deterministic_host",
            "raster_geometry_authority": False,
        },
    }
    contract = validate_local_redesign(contract)
    path = root / "contracts" / f"local-redesign-{redesign_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "redesign_id": redesign_id,
            "selection": contract["selection"],
            "message": "Selection captured; Codex may add only a typed CAD program and candidate command before preview."}


def _expanded_box(bounds, amount):
    import Part
    low = [bounds["min_mm"][i] - amount for i in range(3)]
    high = [bounds["max_mm"][i] + amount for i in range(3)]
    lengths = [max(high[i] - low[i], 1e-6) for i in range(3)]
    import FreeCAD as App
    return Part.makeBox(lengths[0], lengths[1], lengths[2], App.Vector(*low))


def _outside_scope_difference(original, candidate, bounds, expansion):
    scope = _expanded_box(bounds, expansion)
    original_outside = original.cut(scope)
    candidate_outside = candidate.cut(scope)
    common = original_outside.common(candidate_outside)
    return max(0.0, float(original_outside.Volume) + float(candidate_outside.Volume)
               - 2.0 * float(common.Volume))


def _check_candidate(document, redesign, target, candidate, before_snapshot):
    if candidate is target:
        raise PhysicalDesignError("candidate must be a new sibling-branch object")
    shape = getattr(candidate, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid() or not shape.Solids:
        raise PhysicalDesignError("candidate must be a valid FreeCAD solid")
    current = _semantic_snapshot(document)
    changed = [semantic_id for semantic_id, digest in before_snapshot.items()
               if current.get(semantic_id) != digest]
    if changed:
        raise PhysicalDesignError("typed program changed existing semantic objects: " + ", ".join(changed))
    expansion = float(redesign["constraints"]["max_expansion_mm"])
    original_box = target.Shape.BoundBox
    candidate_box = shape.BoundBox
    limits = (
        candidate_box.XMin >= original_box.XMin - expansion,
        candidate_box.YMin >= original_box.YMin - expansion,
        candidate_box.ZMin >= original_box.ZMin - expansion,
        candidate_box.XMax <= original_box.XMax + expansion,
        candidate_box.YMax <= original_box.YMax + expansion,
        candidate_box.ZMax <= original_box.ZMax + expansion,
    )
    if not all(limits):
        raise PhysicalDesignError("candidate exceeds the selected object's maximum expansion envelope")
    outside_difference = 0.0
    if redesign["selection"]["kind"] == "faces":
        outside_difference = _outside_scope_difference(
            target.Shape, shape, redesign["selection"]["bounds_mm"], expansion)
        tolerance = float(redesign["constraints"]["outside_scope_tolerance_mm3"])
        if outside_difference > tolerance:
            raise PhysicalDesignError(
                f"candidate changes {outside_difference:.6f} mm^3 outside the selected face region")
    protected_checks = []
    for item in redesign["constraints"]["protected_objects"]:
        protected = _find_semantic(document, item["semantic_id"])
        current_digest = shape_digest(protected.Shape)
        if current_digest != item["shape_sha256"]:
            raise PhysicalDesignError(f"protected object changed: {item['semantic_id']}")
        distance = float(shape.distToShape(protected.Shape)[0])
        interference = float(shape.common(protected.Shape).Volume)
        clearance = float(item["clearance_mm"])
        passed = interference <= 1e-7 and distance + 1e-7 >= clearance
        protected_checks.append({"semantic_id": item["semantic_id"],
                                 "distance_mm": distance,
                                 "interference_mm3": interference,
                                 "required_clearance_mm": clearance,
                                 "passed": passed})
        if not passed:
            raise PhysicalDesignError(f"candidate interferes with protected object {item['semantic_id']}")
    return {"outside_scope_difference_mm3": outside_difference,
            "protected_checks": protected_checks}


def _edge_points(edge):
    try:
        points = edge.discretize(Deflection=0.05)
    except Exception:
        points = [vertex.Point for vertex in edge.Vertexes]
    if len(points) > 4096:
        step = max(1, len(points) // 4096)
        points = points[::step]
        if points[-1] != edge.Vertexes[-1].Point:
            points.append(edge.Vertexes[-1].Point)
    return points


def _project_point(point, view):
    x, y, z = float(point.x), float(point.y), float(point.z)
    return {
        "front": (x, z), "rear": (-x, z), "left": (y, z),
        "right": (-y, z), "top": (x, y), "bottom": (x, -y),
    }[view]


def _project_edges(shape, view):
    projected = []
    for index, edge in enumerate(shape.Edges, 1):
        points = [_project_point(point, view) for point in _edge_points(edge)]
        if len(points) >= 2:
            projected.append((f"edge-{index}", points))
    if not projected:
        raise PhysicalDesignError("candidate B-Rep has no projectable edges")
    return projected


def _projection_extents(projected):
    points = [point for _, curve in projected for point in curve]
    return min(p[0] for p in points), min(p[1] for p in points), \
        max(p[0] for p in points), max(p[1] for p in points)


def _svg_sheet(projected, view, geometry_digest, semantic_id):
    xmin, ymin, xmax, ymax = _projection_extents(projected)
    width, height = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    scale = min(235.0 / width, 135.0 / height)
    ox, oy = 31.0 - xmin * scale, 158.0 + ymin * scale
    paths = []
    for curve_id, points in projected:
        command = "M " + " L ".join(
            f"{ox + x * scale:.4f} {oy - y * scale:.4f}" for x, y in points)
        paths.append(f'    <path id="{curve_id}" d="{command}"/>')
    metadata = escape(json.dumps({"schema": "design-studio.brep-projection/1",
        "geometry_sha256": geometry_digest, "semantic_id": semantic_id,
        "view": view, "units": "mm", "bounds_mm": [xmin, ymin, xmax, ymax]},
        sort_keys=True, separators=(",", ":")))
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 297 210">',
        f"  <metadata>{metadata}</metadata>",
        '  <defs><marker id="arrow" markerWidth="5" markerHeight="5" refX="2.5" refY="2.5" orient="auto-start-reverse"><path d="M0,0 L5,2.5 L0,5 z"/></marker></defs>',
        '  <rect x="8" y="8" width="281" height="194" fill="white" stroke="black" stroke-width="0.35"/>',
        f'  <g id="brep-projection" fill="none" stroke="black" stroke-width="0.25">',
        *paths, '  </g>',
        f'  <g id="dimensions" fill="none" stroke="#1b5eaa" stroke-width="0.22" marker-start="url(#arrow)" marker-end="url(#arrow)">',
        '    <line x1="31" y1="178" x2="266" y2="178"/>',
        '    <line x1="20" y1="23" x2="20" y2="158"/>',
        '  </g>',
        f'  <g font-family="DejaVu Sans" font-size="4" fill="black"><text x="148.5" y="176" text-anchor="middle">{width:.3f} mm</text>',
        f'    <text x="18" y="91" text-anchor="middle" transform="rotate(-90 18 91)">{height:.3f} mm</text>',
        f'    <text x="12" y="15">LOCAL REDESIGN — {escape(view.upper())} — SCALE {scale:.5f}:1</text>',
        f'    <text x="12" y="187">B-REP SHA-256: {geometry_digest}</text>',
        f'    <text x="12" y="193">SEMANTIC ID: {escape(semantic_id)}</text>',
        '    <text x="285" y="199" text-anchor="end">UNITS: mm · PROJECTION: exact B-Rep edges</text></g>',
        '</svg>', ''])


def _dxf_sheet(projected, view, geometry_digest, semantic_id):
    xmin, ymin, xmax, ymax = _projection_extents(projected)
    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0",
             "ENDSEC", "0", "SECTION", "2", "ENTITIES", "999",
             f"DESIGNSTUDIO BREP SHA256 {geometry_digest}", "999",
             f"SEMANTIC ID {semantic_id}", "999", f"VIEW {view}"]
    for curve_id, points in projected:
        lines.extend(["999", f"CURVE ID {curve_id}", "0", "LWPOLYLINE", "8", "BREP",
                      "90", str(len(points)), "70", "0"])
        for x, y in points:
            lines.extend(["10", f"{x:.9f}", "20", f"{y:.9f}"])
    for text_value, x, y in (
            (f"WIDTH {xmax - xmin:.3f} mm", xmin, ymin - 8.0),
            (f"HEIGHT {ymax - ymin:.3f} mm", xmin, ymin - 14.0)):
        lines.extend(["0", "TEXT", "8", "DIMENSIONS", "10", f"{x:.9f}", "20",
                      f"{y:.9f}", "40", "3.0", "1", text_value])
    lines.extend(["0", "ENDSEC", "0", "EOF", ""])
    return "\n".join(lines)


def generate_measured_projections(shape, destination, semantic_id):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    geometry_digest = shape_digest(shape)
    artifacts = []
    for view in VIEWS:
        projected = _project_edges(shape, view)
        for extension, content in (("svg", _svg_sheet(projected, view, geometry_digest, semantic_id)),
                                   ("dxf", _dxf_sheet(projected, view, geometry_digest, semantic_id))):
            path = destination / f"{view}.{extension}"
            path.write_text(content, encoding="utf-8")
            artifacts.append({"view": view, "format": extension, "path": str(path),
                              "sha256": file_digest(path)})
    index = {"schema": "design-studio.measured-brep-drawings/1",
             "geometry_sha256": geometry_digest, "semantic_id": semantic_id,
             "artifacts": artifacts}
    index["drawing_digest"] = canonical_digest(index)
    index_path = destination / "drawing-index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def _remove_new_objects(document, existing_names):
    for obj in reversed(list(getattr(document, "Objects", []))):
        try:
            name = obj.Name
        except ReferenceError:
            continue
        if name not in existing_names and document.getObject(name) is not None:
            document.removeObject(name)
    document.recompute()


def _workspace_json(root, requested, label, maximum=8 * 1024 * 1024):
    path = Path(requested).expanduser().resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".json" or not path.is_file():
        raise PhysicalDesignError(f"{label} must be an existing workspace JSON file")
    if path.stat().st_size <= 0 or path.stat().st_size > maximum:
        raise PhysicalDesignError(f"{label} is empty or too large")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PhysicalDesignError(f"{label} is invalid JSON: {exc}") from exc


def prepare_local_redesign(workspace_root, captured_path, program, candidate_command_id):
    """Create an immutable program-ready revision from a captured selection.

    This is the only path for agent-authored geometry: the caller supplies a
    typed program to the trusted host, never edits workspace files directly.
    """
    root = Path(workspace_root).expanduser().resolve()
    source, raw = _workspace_json(root, captured_path, "captured redesign contract")
    captured = validate_local_redesign(raw)
    if captured["status"] != "captured":
        raise PhysicalDesignError("only a captured redesign may receive a CAD program")
    ready = deepcopy(captured)
    ready["revision"] += 1
    ready["status"] = "program_ready"
    ready["program"] = program
    ready["candidate_command_id"] = candidate_command_id
    ready["provenance"] = {
        "created_by": "DesignStudio trusted local-redesign tool",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model_role": "codex_proposal",
        "raster_geometry_authority": False,
    }
    ready = validate_local_redesign(ready)
    destination = (root / "contracts" /
                   f"local-redesign-{ready['redesign_id']}-r{ready['revision']}.json")
    destination.write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
    return {"path": str(destination), "source_path": str(source),
            "redesign": ready, "redesign_sha256": canonical_digest(ready)}


def preview_local_redesign(document, workspace_root, redesign_path):
    root = Path(workspace_root).expanduser().resolve()
    path, raw = _workspace_json(root, redesign_path, "redesign contract")
    redesign = validate_local_redesign(raw)
    if redesign["status"] != "program_ready":
        raise PhysicalDesignError("capture is incomplete; add a typed CAD program before preview")
    target = _find_semantic(document, redesign["selection"]["semantic_id"])
    if target.Name != redesign["selection"]["object_name"]:
        raise PhysicalDesignError("selection object identity changed")
    if shape_digest(target.Shape) != redesign["selection"]["target_shape_sha256"]:
        raise PhysicalDesignError("selected target digest is stale")
    selected_shape = _selected_shape(target, redesign["selection"]["kind"],
                                     redesign["selection"]["subelements"])
    if shape_digest(selected_shape) != redesign["selection"]["selection_shape_sha256"]:
        raise PhysicalDesignError("selected face topology or boundary changed")
    before_snapshot = _semantic_snapshot(document)
    existing_names = {obj.Name for obj in getattr(document, "Objects", [])}
    try:
        from .mechanical_cad import execute_program
        receipt = execute_program(document, redesign["program"])
        matches = [item for item in receipt["verification"]
                   if item["command_id"] == redesign["candidate_command_id"]]
        if len(matches) != 1:
            raise PhysicalDesignError("candidate command does not resolve to one created object")
        candidate = document.getObject(matches[0]["object"])
        if candidate is None:
            raise PhysicalDesignError("candidate object is missing after rebuild")
        checks = _check_candidate(document, redesign, target, candidate, before_snapshot)
        controller = document.getObject(receipt["controller"])
        _add_property(controller, "App::PropertyString", "LocalRedesignId", "Local Redesign",
                      redesign["redesign_id"])
        _add_property(controller, "App::PropertyString", "LocalRedesignStatus", "Local Redesign",
                      "preview_verified")
        _add_property(controller, "App::PropertyLink", "RedesignTarget", "Local Redesign", target)
        preview_semantic = f"preview:{redesign['redesign_id']}"
        _add_property(candidate, "App::PropertyString", "DesignStudioSemanticId",
                      "DesignStudio Semantic", preview_semantic)
        _add_property(candidate, "App::PropertyString", "DesignStudioRole",
                      "DesignStudio Semantic", "local_redesign_preview")
        _add_property(candidate, "App::PropertyString", "ReplacesSemanticId",
                      "Local Redesign", redesign["selection"]["semantic_id"])
        view = getattr(candidate, "ViewObject", None)
        if view is not None:
            view.Visibility = True
            view.ShapeColor = (0.20, 0.75, 0.95)
            view.Transparency = 25
        target_view = getattr(target, "ViewObject", None)
        if target_view is not None:
            target_view.Visibility = True
        output = (root / redesign["drawing_output"]["directory"]).resolve()
        if not output.is_relative_to(root):
            raise PhysicalDesignError("drawing output escapes workspace")
        drawings = generate_measured_projections(candidate.Shape, output, preview_semantic)
        document.recompute()
        document.save()
        preview = {
            "schema": PREVIEW_SCHEMA,
            "preview_id": str(uuid.uuid4()),
            "redesign_id": redesign["redesign_id"],
            "redesign_path": str(path.relative_to(root)),
            "redesign_sha256": canonical_digest(redesign),
            "target_semantic_id": redesign["selection"]["semantic_id"],
            "target_object": target.Name,
            "target_shape_sha256": shape_digest(target.Shape),
            "candidate_object": candidate.Name,
            "candidate_shape_sha256": shape_digest(candidate.Shape),
            "controller_object": controller.Name,
            "before_semantic_shapes": before_snapshot,
            "scope_checks": checks,
            "drawings": drawings,
            "baseline_retained": document.getObject(target.Name) is not None,
            "baseline_visible": bool(getattr(target_view, "Visibility", True)) if target_view is not None else True,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        preview["preview_sha256"] = canonical_digest(preview)
        receipt_path = root / "contracts" / f"local-redesign-preview-{redesign['redesign_id']}.json"
        receipt_path.write_text(json.dumps(preview, indent=2) + "\n", encoding="utf-8")
        return {"preview": preview, "receipt_path": str(receipt_path),
                "program_receipt": receipt}
    except Exception:
        _remove_new_objects(document, existing_names)
        raise


def _verify_preview_digest(preview):
    material = dict(preview)
    claimed = material.pop("preview_sha256", None)
    if claimed is None or canonical_digest(material) != claimed:
        raise PhysicalDesignError("preview receipt digest is invalid")


def commit_local_redesign(document, workspace_root, preview_receipt_path):
    root = Path(workspace_root).expanduser().resolve()
    receipt_path, preview = _workspace_json(root, preview_receipt_path, "preview receipt")
    if preview.get("schema") != PREVIEW_SCHEMA:
        raise PhysicalDesignError("preview receipt schema is invalid")
    _verify_preview_digest(preview)
    redesign_path = (root / _relative(preview["redesign_path"], "redesign_path")).resolve()
    path, raw = _workspace_json(root, redesign_path, "redesign contract")
    redesign = validate_local_redesign(raw)
    if canonical_digest(redesign) != preview["redesign_sha256"]:
        raise PhysicalDesignError("redesign contract changed after preview")
    target = _find_semantic(document, preview["target_semantic_id"])
    candidate = document.getObject(preview["candidate_object"])
    controller = document.getObject(preview["controller_object"])
    if candidate is None or controller is None:
        raise PhysicalDesignError("preview branch is no longer present")
    if shape_digest(target.Shape) != preview["target_shape_sha256"]:
        raise PhysicalDesignError("baseline target changed after preview")
    if shape_digest(candidate.Shape) != preview["candidate_shape_sha256"]:
        raise PhysicalDesignError("preview candidate changed after verification")
    before = preview["before_semantic_shapes"]
    current = _semantic_snapshot(document)
    changed = [semantic_id for semantic_id, digest in before.items()
               if current.get(semantic_id) != digest]
    if changed:
        raise PhysicalDesignError("semantic objects changed after preview: " + ", ".join(changed))
    checks = _check_candidate(document, redesign, target, candidate, before)
    stable_id = preview["target_semantic_id"]
    baseline_id = f"{stable_id}::baseline::{preview['preview_id']}"
    opened = hasattr(document, "openTransaction")
    if opened:
        document.openTransaction(f"Commit local redesign {redesign['redesign_id']}")
    try:
        _add_property(target, "App::PropertyString", "OriginalSemanticId",
                      "Local Redesign", stable_id)
        target.DesignStudioSemanticId = baseline_id
        _add_property(target, "App::PropertyString", "SupersededBySemanticId",
                      "Local Redesign", stable_id)
        _add_property(target, "App::PropertyString", "LocalRedesignStatus",
                      "Local Redesign", "retained_baseline")
        candidate.DesignStudioSemanticId = stable_id
        candidate.DesignStudioRole = str(getattr(target, "DesignStudioRole", "physical_object"))
        _add_property(candidate, "App::PropertyString", "BaselineSemanticId",
                      "Local Redesign", baseline_id)
        controller.LocalRedesignStatus = "committed"
        target_view = getattr(target, "ViewObject", None)
        candidate_view = getattr(candidate, "ViewObject", None)
        if target_view is not None:
            target_view.Visibility = False
        if candidate_view is not None:
            candidate_view.Visibility = True
            candidate_view.Transparency = 0
        document.recompute()
        manifest = semantic_assembly_manifest(document)
        manifest_path = root / "mechanical" / "semantic-assembly.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        document.save()
        commit = {
            "schema": COMMIT_SCHEMA,
            "preview_sha256": preview["preview_sha256"],
            "redesign_id": redesign["redesign_id"],
            "stable_semantic_id": stable_id,
            "candidate_object": candidate.Name,
            "retained_baseline_object": target.Name,
            "retained_baseline_semantic_id": baseline_id,
            "baseline_deleted": False,
            "scope_checks": checks,
            "semantic_manifest_path": str(manifest_path.relative_to(root)),
            "semantic_manifest_sha256": manifest["manifest_sha256"],
            "committed_utc": datetime.now(timezone.utc).isoformat(),
        }
        commit["commit_sha256"] = canonical_digest(commit)
        commit_path = root / "contracts" / f"local-redesign-commit-{redesign['redesign_id']}.json"
        commit_path.write_text(json.dumps(commit, indent=2) + "\n", encoding="utf-8")
        if opened:
            document.commitTransaction()
        return {"commit": commit, "receipt_path": str(commit_path),
                "source_preview_path": str(receipt_path), "redesign_path": str(path)}
    except Exception:
        if opened:
            document.abortTransaction()
        document.recompute()
        raise
