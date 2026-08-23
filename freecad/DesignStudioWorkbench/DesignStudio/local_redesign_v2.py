"""Topology-aware local surface replacement for ``local-redesign/2``.

The v1 local-redesign flow remains untouched.  This module adds a stricter
face-level branch: a typed candidate patch is built by FreeCAD, the selected
target faces are removed, and the candidate faces are sewn back with the
unchanged surrounding B-Rep.  Every preview is a sibling branch; commit only
transfers the stable semantic identity after the same checks are repeated.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from .physical_design import (
    PhysicalDesignError,
    _add_property,
    _find_semantic,
    _relative,
    _semantic_objects,
    _semantic_snapshot,
    _shape_bounds,
    canonical_digest,
    file_digest,
    ensure_semantic_id,
    shape_digest,
)
from .vector_native_cad import VectorNativeCadError, execute_vector_program, validate_vector_program


SCHEMA = "design-studio.local-redesign/2"
PREVIEW_SCHEMA = "design-studio.local-redesign-preview/2"
COMMIT_SCHEMA = "design-studio.local-redesign-commit/2"
DISCARD_SCHEMA = "design-studio.local-redesign-discard/2"
ROLLBACK_SCHEMA = "design-studio.local-redesign-rollback/2"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_FACE = re.compile(r"^Face([1-9][0-9]*)$")


def _strict(value, allowed, required, label):
    if not isinstance(value, dict):
        raise PhysicalDesignError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
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
    number = float(value)
    if minimum is not None and number < minimum:
        raise PhysicalDesignError(f"{label} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise PhysicalDesignError(f"{label} must be <= {maximum}")
    return number


def _point(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise PhysicalDesignError(f"{label} must contain three coordinates")
    return [_finite(item, f"{label}[{index}]", -1_000_000, 1_000_000)
            for index, item in enumerate(value)]


def _unit(value, label):
    vector = _point(value, label)
    length = math.sqrt(sum(item * item for item in vector))
    if abs(length - 1.0) > 1.0e-5:
        raise PhysicalDesignError(f"{label} must be a unit vector")
    return vector


def _bounds(value, label):
    _strict(value, {"min_mm", "max_mm"}, {"min_mm", "max_mm"}, label)
    low, high = _point(value["min_mm"], f"{label}.min_mm"), _point(value["max_mm"], f"{label}.max_mm")
    if any(low[index] > high[index] for index in range(3)):
        raise PhysicalDesignError(f"{label} has reversed coordinates")
    return {"min_mm": low, "max_mm": high}


def _face_index(name, label="face"):
    match = _FACE.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        raise PhysicalDesignError(f"{label} must be FaceN")
    return int(match.group(1))


def _dot(first, second):
    return sum(float(first[index]) * float(second[index]) for index in range(3))


def _cross(first, second):
    return [first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0]]


def _normalize(vector, label):
    length = math.sqrt(_dot(vector, vector))
    if length <= 1.0e-12:
        raise PhysicalDesignError(f"{label} is zero length")
    return [float(item) / length for item in vector]


def _vector(point):
    return [float(point.x), float(point.y), float(point.z)]


def _edge_points(edge):
    try:
        points = list(edge.discretize(Deflection=0.05))
    except Exception:
        points = [vertex.Point for vertex in edge.Vertexes]
    if len(points) < 2:
        raise PhysicalDesignError("B-Rep edge has fewer than two sample points")
    return points


def _edge_signature(edge):
    points = _edge_points(edge)
    box = edge.BoundBox
    return {
        "curve_type": type(getattr(edge, "Curve", None)).__name__,
        "length_mm": round(float(edge.Length), 9),
        "bounds": [round(float(item), 9) for item in
                   (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)],
        "samples": [[round(float(point.x), 9), round(float(point.y), 9), round(float(point.z), 9)]
                    for point in points],
    }


def _face_signature(face):
    box = face.BoundBox
    center = getattr(face, "CenterOfMass", box.Center)
    return {
        "shape_sha256": shape_digest(face),
        "area_mm2": round(float(face.Area), 9),
        "center_mm": [round(float(item), 9) for item in _vector(center)],
    }


def _face_normal(face, point):
    surface = getattr(face, "Surface", None)
    if surface is None:
        raise PhysicalDesignError("face has no parametric surface")
    parameter = surface.parameter(point)
    normal = face.normalAt(float(parameter[0]), float(parameter[1]))
    return _normalize(_vector(normal), "face normal")


def _face_curvature(face, point):
    surface = getattr(face, "Surface", None)
    if surface is None:
        raise PhysicalDesignError("face has no parametric surface")
    parameter = surface.parameter(point)
    if hasattr(surface, "curvature"):
        value = surface.curvature(float(parameter[0]), float(parameter[1]))
        if hasattr(value, "MaxCurvature"):
            return abs(float(value.MaxCurvature))
        if isinstance(value, (tuple, list)):
            return max(abs(float(item)) for item in value)
        return abs(float(value))
    if hasattr(face, "curvatureAt"):
        return abs(float(face.curvatureAt(float(parameter[0]), float(parameter[1]))))
    raise PhysicalDesignError("face curvature is unavailable")


def _face_adjacency(shape):
    adjacency = {}
    for edge_index, edge in enumerate(shape.Edges, 1):
        faces = []
        for face_index, face in enumerate(shape.Faces, 1):
            if any(edge.isSame(candidate) for candidate in face.Edges):
                faces.append(face_index)
        adjacency[edge_index] = faces
    return adjacency


def _boundary_records(shape, selected_faces):
    selected = set(selected_faces)
    adjacency = _face_adjacency(shape)
    records = []
    for edge_index, adjacent in adjacency.items():
        selected_adjacent = [face for face in adjacent if face in selected]
        if selected_adjacent and not set(adjacent).issubset(selected):
            edge = shape.Edges[edge_index - 1]
            signature = _edge_signature(edge)
            records.append({
                "edge": f"Edge{edge_index}",
                "signature_sha256": canonical_digest({"edge": signature,
                                                       "adjacent_faces": adjacent}),
                "adjacent_faces": [f"Face{face}" for face in adjacent],
                "length_mm": signature["length_mm"],
                "midpoint_mm": [round(float(item), 9)
                                 for item in _vector(edge.valueAt(
                                     (edge.FirstParameter + edge.LastParameter) * 0.5))],
            })
    if not records:
        raise PhysicalDesignError("selected faces have no external boundary edges")
    return records


def _selected_frame(shape, selected_faces, boundary_records):
    faces = [shape.Faces[index - 1] for index in selected_faces]
    origin = [sum(_vector(getattr(face, "CenterOfMass", face.BoundBox.Center))[axis]
                  for face in faces) / len(faces) for axis in range(3)]
    normals = [_face_normal(face, getattr(face, "CenterOfMass", face.BoundBox.Center))
               for face in faces]
    z_axis = _normalize([sum(normal[axis] for normal in normals) for axis in range(3)],
                        "selected-face normal")
    first_edge = shape.Edges[int(boundary_records[0]["edge"][4:]) - 1]
    tangent = first_edge.tangentAt((first_edge.FirstParameter + first_edge.LastParameter) * 0.5)
    x_raw = _vector(tangent)
    x_raw = [x_raw[axis] - _dot(x_raw, z_axis) * z_axis[axis] for axis in range(3)]
    x_axis = _normalize(x_raw, "local frame tangent")
    y_axis = _normalize(_cross(z_axis, x_axis), "local frame y-axis")
    # Re-orthogonalize x so persisted frames are numerically stable.
    x_axis = _normalize(_cross(y_axis, z_axis), "local frame x-axis")
    return {"origin_mm": [round(item, 9) for item in origin],
            "x_axis": [round(item, 9) for item in x_axis],
            "y_axis": [round(item, 9) for item in y_axis],
            "z_axis": [round(item, 9) for item in z_axis]}


def _capture_target(obj, selected_names):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise PhysicalDesignError("target object has no usable B-Rep")
    selected = sorted({_face_index(name, "selected face") for name in selected_names})
    if not selected or any(index > len(shape.Faces) for index in selected):
        raise PhysicalDesignError("selected face does not exist on target")
    boundary = _boundary_records(shape, selected)
    frame = _selected_frame(shape, selected, boundary)
    signatures = []
    for index in selected:
        signature = _face_signature(shape.Faces[index - 1])
        signatures.append({"face": f"Face{index}", **signature})
    return {"semantic_id": ensure_semantic_id(obj), "object_name": obj.Name,
            "target_shape_sha256": shape_digest(shape),
            "selected_faces": [f"Face{index}" for index in selected],
            "selected_face_signatures": signatures,
            "boundary_edges": boundary, "local_frame": frame}


def _validate_target(target):
    keys = {"semantic_id", "object_name", "target_shape_sha256", "selected_faces",
            "selected_face_signatures", "boundary_edges", "local_frame"}
    _strict(target, keys, keys, "target")
    _id(target["semantic_id"], "target.semantic_id")
    if not isinstance(target["object_name"], str) or not target["object_name"]:
        raise PhysicalDesignError("target object_name is empty")
    _digest(target["target_shape_sha256"], "target.target_shape_sha256")
    selected = target["selected_faces"]
    if not isinstance(selected, list) or not selected:
        raise PhysicalDesignError("target.selected_faces must be non-empty")
    if len(selected) != len(set(selected)):
        raise PhysicalDesignError("target.selected_faces must be unique")
    for name in selected:
        _face_index(name, "target.selected_faces")
    signatures = target["selected_face_signatures"]
    if len(signatures) != len(selected):
        raise PhysicalDesignError("selected face signatures do not match selected_faces")
    signature_names = set()
    for index, signature in enumerate(signatures):
        _strict(signature, {"face", "shape_sha256", "area_mm2", "center_mm"},
                {"face", "shape_sha256", "area_mm2", "center_mm"},
                f"target.selected_face_signatures[{index}]")
        if signature["face"] in signature_names:
            raise PhysicalDesignError("duplicate selected face signature")
        signature_names.add(signature["face"])
        _face_index(signature["face"], "selected face signature")
        _digest(signature["shape_sha256"], "selected face signature digest")
        _finite(signature["area_mm2"], "selected face area", 0)
        _point(signature["center_mm"], "selected face center")
    if signature_names != set(selected):
        raise PhysicalDesignError("selected face signatures do not cover selected_faces")
    boundaries = target["boundary_edges"]
    if not isinstance(boundaries, list) or not boundaries:
        raise PhysicalDesignError("target.boundary_edges must be non-empty")
    edge_names = set()
    for index, boundary in enumerate(boundaries):
        _strict(boundary, {"edge", "signature_sha256", "adjacent_faces", "length_mm", "midpoint_mm"},
                {"edge", "signature_sha256", "adjacent_faces", "length_mm", "midpoint_mm"},
                f"target.boundary_edges[{index}]")
        if boundary["edge"] in edge_names:
            raise PhysicalDesignError("duplicate target boundary edge")
        edge_names.add(boundary["edge"])
        _digest(boundary["signature_sha256"], "boundary signature")
        if not boundary["adjacent_faces"]:
            raise PhysicalDesignError("boundary edge has no adjacent face")
        for name in boundary["adjacent_faces"]:
            _face_index(name, "boundary adjacent face")
        _finite(boundary["length_mm"], "boundary length", 0)
        _point(boundary["midpoint_mm"], "boundary midpoint")
    frame = target["local_frame"]
    _strict(frame, {"origin_mm", "x_axis", "y_axis", "z_axis"},
            {"origin_mm", "x_axis", "y_axis", "z_axis"}, "target.local_frame")
    _point(frame["origin_mm"], "frame origin")
    x_axis, y_axis, z_axis = (_unit(frame[key], f"frame {key}")
                              for key in ("x_axis", "y_axis", "z_axis"))
    if max(abs(_dot(x_axis, y_axis)), abs(_dot(y_axis, z_axis)), abs(_dot(z_axis, x_axis))) > 1.0e-4:
        raise PhysicalDesignError("local frame axes must be orthogonal")


def validate_local_redesign_v2(value):
    redesign = deepcopy(value)
    allowed = {"schema", "redesign_id", "revision", "status", "document", "target",
               "intent", "constraints", "program", "candidate_command_id",
               "candidate_faces", "provenance"}
    _strict(redesign, allowed, allowed, "local redesign v2")
    if redesign["schema"] != SCHEMA:
        raise PhysicalDesignError(f"schema must be {SCHEMA}")
    _id(redesign["redesign_id"], "redesign_id")
    if not isinstance(redesign["revision"], int) or isinstance(redesign["revision"], bool) or redesign["revision"] < 1:
        raise PhysicalDesignError("revision must be positive")
    if redesign["status"] not in {"captured", "program_ready"}:
        raise PhysicalDesignError("status must be captured or program_ready")
    document = redesign["document"]
    _strict(document, {"document_id", "path", "file_sha256"},
            {"document_id", "path", "file_sha256"}, "document")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise PhysicalDesignError("document_id is empty")
    _relative(document["path"], "document.path")
    _digest(document["file_sha256"], "document.file_sha256")
    _validate_target(redesign["target"])
    if not isinstance(redesign["intent"], str) or not redesign["intent"].strip():
        raise PhysicalDesignError("intent must not be empty")
    constraints = redesign["constraints"]
    ckeys = {"permitted_expansion_mm", "boundary_deviation_mm", "g1_normal_angle_deg",
             "g2_curvature_delta", "continuity_required", "protected_objects",
             "manufacturing", "unselected_objects_must_remain_unchanged",
             "baseline_must_be_retained"}
    _strict(constraints, ckeys, ckeys, "constraints")
    _finite(constraints["permitted_expansion_mm"], "permitted_expansion_mm", 0, 10000)
    _finite(constraints["boundary_deviation_mm"], "boundary_deviation_mm", 1.0e-9, 100)
    _finite(constraints["g1_normal_angle_deg"], "g1_normal_angle_deg", 0, 180)
    _finite(constraints["g2_curvature_delta"], "g2_curvature_delta", 0, 1_000_000)
    if constraints["continuity_required"] not in {"G0", "G1", "G2"}:
        raise PhysicalDesignError("continuity_required must be G0, G1 or G2")
    if constraints["unselected_objects_must_remain_unchanged"] is not True:
        raise PhysicalDesignError("unselected objects must remain unchanged")
    if constraints["baseline_must_be_retained"] is not True:
        raise PhysicalDesignError("baseline must be retained")
    protected_ids = set()
    for index, item in enumerate(constraints["protected_objects"]):
        _strict(item, {"semantic_id", "shape_sha256", "clearance_mm"},
                {"semantic_id", "shape_sha256", "clearance_mm"},
                f"protected_objects[{index}]")
        if item["semantic_id"] in protected_ids:
            raise PhysicalDesignError("duplicate protected semantic ID")
        protected_ids.add(item["semantic_id"])
        _digest(item["shape_sha256"], "protected shape digest")
        _finite(item["clearance_mm"], "protected clearance", 0, 10000)
    if redesign["target"]["semantic_id"] in protected_ids:
        raise PhysicalDesignError("target cannot be protected")
    manufacturing = constraints["manufacturing"]
    _strict(manufacturing, {"process", "minimum_wall_mm", "minimum_blend_radius_mm", "max_overhang_deg"},
            {"process", "minimum_wall_mm", "minimum_blend_radius_mm", "max_overhang_deg"},
            "manufacturing")
    if manufacturing["process"] not in {"fdm", "sla", "cnc", "injection_molding", "unknown"}:
        raise PhysicalDesignError("unsupported manufacturing process")
    _finite(manufacturing["minimum_wall_mm"], "minimum wall", 1.0e-9, 1000)
    _finite(manufacturing["minimum_blend_radius_mm"], "minimum blend radius", 0, 1000)
    _finite(manufacturing["max_overhang_deg"], "maximum overhang", 0, 90)
    candidate_faces = redesign["candidate_faces"]
    if len(candidate_faces) != len(set(candidate_faces)):
        raise PhysicalDesignError("candidate_faces must be unique")
    for name in candidate_faces:
        _face_index(name, "candidate_faces")
    if redesign["status"] == "captured":
        if redesign["program"] is not None or redesign["candidate_command_id"] is not None or candidate_faces:
            raise PhysicalDesignError("captured redesign cannot contain candidate geometry")
    else:
        if not isinstance(redesign["program"], dict) or not isinstance(redesign["candidate_command_id"], str) or not candidate_faces:
            raise PhysicalDesignError("program_ready redesign requires program, command and candidate faces")
        try:
            normalized = validate_vector_program(redesign["program"])
        except VectorNativeCadError as exc:
            raise PhysicalDesignError(f"candidate vector program is invalid: {exc}") from exc
        command_ids = {command["id"] for command in normalized["commands"]}
        if redesign["candidate_command_id"] not in command_ids:
            raise PhysicalDesignError("candidate command is not produced by the vector program")
        if any(command["op"] == "drawing.project" for command in normalized["commands"]):
            raise PhysicalDesignError("local-redesign/2 candidate programs cannot emit drawings; package 3 owns sheets")
        redesign["program"] = normalized
    provenance = redesign["provenance"]
    _strict(provenance, {"created_by", "created_utc", "model_role", "raster_geometry_authority"},
            {"created_by", "created_utc", "model_role", "raster_geometry_authority"}, "provenance")
    if provenance["model_role"] not in {"user", "codex_proposal", "deterministic_host"}:
        raise PhysicalDesignError("invalid provenance model_role")
    if provenance["raster_geometry_authority"] is not False:
        raise PhysicalDesignError("raster geometry cannot be geometry authority")
    return redesign


def _workspace_json(root, requested, label):
    path = Path(requested).expanduser().resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".json" or not path.is_file():
        raise PhysicalDesignError(f"{label} must be an existing workspace JSON file")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PhysicalDesignError(f"{label} is invalid JSON: {exc}") from exc


def capture_local_redesign_v2(document, workspace_root, intent, permitted_expansion_mm=0.0,
                              protected_objects=(), continuity_required="G1",
                              manufacturing=None, selection_ex=None, redesign_id=None):
    root = Path(workspace_root).expanduser().resolve()
    if selection_ex is None:
        import FreeCADGui as Gui
        selection_ex = Gui.Selection.getSelectionEx()
    if len(selection_ex) != 1:
        raise PhysicalDesignError("select exactly one object and one or more faces")
    selected = selection_ex[0]
    obj = selected.Object
    if obj is None or getattr(obj, "Document", None) is not document:
        raise PhysicalDesignError("selection must belong to the active document")
    names = list(dict.fromkeys(getattr(selected, "SubElementNames", []) or []))
    if not names or not all(_FACE.fullmatch(name) for name in names):
        raise PhysicalDesignError("local-redesign/2 requires one or more selected FaceN elements")
    if not document.FileName:
        raise PhysicalDesignError("save the document before capturing a local redesign")
    document_path = Path(document.FileName).resolve()
    if not document_path.is_relative_to(root):
        raise PhysicalDesignError("document must remain inside workspace_root")
    protected = []
    available = _semantic_objects(document)
    for item in protected_objects:
        if isinstance(item, str):
            semantic_id, clearance = item, 0.0
        elif isinstance(item, dict):
            semantic_id, clearance = item.get("semantic_id"), item.get("clearance_mm", 0.0)
        else:
            raise PhysicalDesignError("protected_objects must contain IDs or objects")
        protected_obj = available.get(str(semantic_id))
        if protected_obj is None or protected_obj is obj:
            raise PhysicalDesignError(f"protected object is unavailable: {semantic_id}")
        protected.append({"semantic_id": str(semantic_id),
                          "shape_sha256": shape_digest(protected_obj.Shape),
                          "clearance_mm": float(clearance)})
    if manufacturing is None:
        manufacturing = {"process": "fdm", "minimum_wall_mm": 2.0,
                         "minimum_blend_radius_mm": 1.2, "max_overhang_deg": 45.0}
    identifier = redesign_id or f"local-{uuid.uuid4().hex[:12]}"
    contract = {
        "schema": SCHEMA, "redesign_id": identifier, "revision": 1,
        "status": "captured",
        "document": {"document_id": document.Name, "path": str(document_path.relative_to(root)),
                     "file_sha256": file_digest(document_path)},
        "target": _capture_target(obj, names),
        "intent": str(intent),
        "constraints": {
            "permitted_expansion_mm": float(permitted_expansion_mm),
            "boundary_deviation_mm": 0.05, "g1_normal_angle_deg": 1.0,
            "g2_curvature_delta": 1.0e-3, "continuity_required": continuity_required,
            "protected_objects": protected, "manufacturing": manufacturing,
            "unselected_objects_must_remain_unchanged": True,
            "baseline_must_be_retained": True,
        },
        "program": None, "candidate_command_id": None, "candidate_faces": [],
        "provenance": {"created_by": "DesignStudio topology capture",
                       "created_utc": datetime.now(timezone.utc).isoformat(),
                       "model_role": "user", "raster_geometry_authority": False},
    }
    contract = validate_local_redesign_v2(contract)
    destination = root / "contracts" / f"local-redesign-v2-{identifier}-r1.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return {"path": str(destination), "redesign_sha256": canonical_digest(contract),
            "redesign": contract}


def prepare_local_redesign_v2(workspace_root, captured_path, program, candidate_command_id,
                              candidate_faces):
    root = Path(workspace_root).expanduser().resolve()
    source, raw = _workspace_json(root, captured_path, "captured local-redesign/2 contract")
    captured = validate_local_redesign_v2(raw)
    if captured["status"] != "captured":
        raise PhysicalDesignError("only a captured contract may receive a candidate program")
    ready = deepcopy(captured)
    ready["revision"] += 1
    ready["status"] = "program_ready"
    ready["program"] = program
    ready["candidate_command_id"] = candidate_command_id
    ready["candidate_faces"] = list(candidate_faces)
    ready["provenance"] = {"created_by": "DesignStudio trusted topology tool",
                           "created_utc": datetime.now(timezone.utc).isoformat(),
                           "model_role": "codex_proposal", "raster_geometry_authority": False}
    ready = validate_local_redesign_v2(ready)
    destination = root / "contracts" / f"local-redesign-v2-{ready['redesign_id']}-r{ready['revision']}.json"
    destination.write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
    return {"path": str(destination), "source_path": str(source),
            "redesign": ready, "redesign_sha256": canonical_digest(ready)}


def _verify_target_snapshot(target, contract_target):
    if target.Name != contract_target["object_name"]:
        raise PhysicalDesignError("target object identity changed")
    if shape_digest(target.Shape) != contract_target["target_shape_sha256"]:
        raise PhysicalDesignError("target B-Rep digest is stale")
    selected = [_face_index(name) for name in contract_target["selected_faces"]]
    current_faces = target.Shape.Faces
    for signature in contract_target["selected_face_signatures"]:
        face = current_faces[_face_index(signature["face"]) - 1]
        if shape_digest(face) != signature["shape_sha256"]:
            raise PhysicalDesignError("selected face topology changed after capture")
    current_boundary = _boundary_records(target.Shape, selected)
    if canonical_digest(current_boundary) != canonical_digest(contract_target["boundary_edges"]):
        raise PhysicalDesignError("selected boundary topology changed after capture")


def _candidate_face_edges(shape, selected_faces):
    return _boundary_records(shape, selected_faces)


def _edge_distance(first, second):
    try:
        return float(first.distToShape(second)[0])
    except Exception as exc:
        raise PhysicalDesignError(f"unable to measure boundary deviation: {exc}") from exc


def _boundary_verification(target_shape, candidate_shape, target_faces, candidate_faces, tolerance):
    target_records = _boundary_records(target_shape, target_faces)
    candidate_records = _boundary_records(candidate_shape, candidate_faces)
    target_edges = [target_shape.Edges[int(record["edge"][4:]) - 1] for record in target_records]
    candidate_edges = [candidate_shape.Edges[int(record["edge"][4:]) - 1] for record in candidate_records]
    # A candidate B-spline may encode an entire closed boundary as one edge,
    # while the baseline box stores the same loop as four line edges.  Compare
    # sampled geometry in both directions instead of requiring identical edge
    # fragmentation; topology identity is still checked for every unselected
    # face below.
    import Part
    target_points = [point for edge in target_edges for point in _edge_points(edge)]
    candidate_points = [point for edge in candidate_edges for point in _edge_points(edge)]
    target_to_candidate = [min(float(edge.distToShape(Part.Vertex(point))[0])
                               for edge in candidate_edges) for point in target_points]
    candidate_to_target = [min(float(edge.distToShape(Part.Vertex(point))[0])
                               for edge in target_edges) for point in candidate_points]
    distances = target_to_candidate + candidate_to_target
    maximum = max(distances, default=0.0)
    if maximum > tolerance + 1.0e-9:
        raise PhysicalDesignError(
            f"candidate boundary deviation {maximum:.6f} mm exceeds {tolerance:.6f} mm")
    return {"target_edge_count": len(target_edges), "candidate_edge_count": len(candidate_edges),
            "target_sample_count": len(target_points), "candidate_sample_count": len(candidate_points),
            "deviation_mm": distances, "max_deviation_mm": maximum,
            "passed": True}


def _continuity_verification(shape, selected_faces, required, angle_limit, curvature_limit):
    selected = set(selected_faces)
    adjacency = _face_adjacency(shape)
    normals, curvatures, samples = [], [], []
    for edge_index, adjacent in adjacency.items():
        inside = [face for face in adjacent if face in selected]
        outside = [face for face in adjacent if face not in selected]
        if not inside or not outside:
            continue
        edge = shape.Edges[edge_index - 1]
        point = edge.valueAt((edge.FirstParameter + edge.LastParameter) * 0.5)
        patch_face = shape.Faces[inside[0] - 1]
        neighbor_face = shape.Faces[outside[0] - 1]
        first = _face_normal(patch_face, point)
        second = _face_normal(neighbor_face, point)
        dot = max(-1.0, min(1.0, _dot(first, second)))
        angle = math.degrees(math.acos(dot))
        normals.append(angle)
        sample = {"edge": f"Edge{edge_index}", "normal_angle_deg": angle}
        if required == "G2":
            curvature = abs(_face_curvature(patch_face, point) - _face_curvature(neighbor_face, point))
            curvatures.append(curvature)
            sample["curvature_delta"] = curvature
        samples.append(sample)
    max_angle = max(normals, default=0.0)
    max_curvature = max(curvatures, default=0.0)
    passed = (required == "G0" or max_angle <= angle_limit + 1.0e-9) and \
             (required != "G2" or max_curvature <= curvature_limit + 1.0e-9)
    if not passed:
        raise PhysicalDesignError(
            f"candidate continuity failed: G1={max_angle:.6f} deg, G2={max_curvature:.6g}")
    return {"required": required, "max_normal_angle_deg": max_angle,
            "max_curvature_delta": max_curvature, "samples": samples,
            "analysis_available": True, "passed": True}


def _quality(shape):
    if shape is None or shape.isNull() or not shape.isValid():
        raise PhysicalDesignError("candidate local patch is null or invalid")
    try:
        check = shape.check()
        errors = [] if check is None else [str(item) for item in check]
    except Exception as exc:
        raise PhysicalDesignError(f"B-Rep quality analysis unavailable: {exc}") from exc
    if errors:
        raise PhysicalDesignError("candidate B-Rep check failed: " + "; ".join(errors))
    solids = list(getattr(shape, "Solids", []))
    shells = list(getattr(shape, "Shells", []))
    if not solids and not shells:
        raise PhysicalDesignError("candidate patch has no shell or solid")
    if solids and not all(solid.isClosed() and solid.isValid() for solid in solids):
        raise PhysicalDesignError("candidate solid is not watertight")
    if not solids and not all(shell.isClosed() and shell.isValid() for shell in shells):
        raise PhysicalDesignError("candidate shell is not closed and valid")
    # OCCT's B-Rep checker is the authority for this gate.  A valid closed
    # shell/solid with no checker errors is the only accepted result; persist
    # the method so the receipt cannot be mistaken for visual inspection.
    return {"valid": True, "solid_count": len(solids), "shell_count": len(shells),
            "watertight": bool(solids) or all(shell.isClosed() for shell in shells),
            "self_intersecting": False, "self_intersection_check": "occt_shape_check",
            "check_errors": []}


def _replace_faces(target_shape, patch_shape, target_faces, candidate_faces):
    import Part
    target_set = set(target_faces)
    patch_set = set(candidate_faces)
    if any(index > len(target_shape.Faces) for index in target_set):
        raise PhysicalDesignError("target face index is out of range")
    if any(index > len(patch_shape.Faces) for index in patch_set):
        raise PhysicalDesignError("candidate face index is out of range")
    unselected = [face for index, face in enumerate(target_shape.Faces, 1) if index not in target_set]
    patches = [face for index, face in enumerate(patch_shape.Faces, 1) if index in patch_set]
    if not patches:
        raise PhysicalDesignError("candidate patch face set is empty")
    try:
        sewn_shell = Part.makeShell(unselected + patches)
        healed = sewn_shell.removeSplitter()
        if healed.isNull() or not healed.isValid():
            raise PhysicalDesignError("sewn local patch is invalid")
        result = Part.makeSolid(healed) if healed.isClosed() else healed
        result_kind = "solid" if healed.isClosed() else "shell"
        result_faces = []
        for patch in patches:
            patch_center = getattr(patch, "CenterOfMass", patch.BoundBox.Center)
            patch_box = patch.BoundBox
            patch_normal = _face_normal(patch, patch_center)
            normal_candidates = []
            for index, face in enumerate(result.Faces, 1):
                try:
                    face_normal = _face_normal(face, getattr(face, "CenterOfMass", face.BoundBox.Center))
                    normal_dot = abs(_dot(face_normal, patch_normal))
                except Exception:
                    normal_dot = 0.0
                if normal_dot >= 0.9999:
                    normal_candidates.append((index, face, normal_dot))
            candidates = [(index, face) for index, face in enumerate(result.Faces, 1)
                          if abs(float(face.Area) - float(patch.Area)) <= 1.0e-5
                          and max(abs(float(getattr(face.BoundBox, key)) - float(getattr(patch_box, key)))
                                  for key in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")) <= 1.0e-5]
            match = min(normal_candidates or candidates, key=lambda item: math.sqrt(
                sum((float(item[1].CenterOfMass[axis]) - float(patch_center[axis])) ** 2
                    for axis in range(3))))[0] if (normal_candidates or candidates) else None
            if match is None:
                match = next((index for index, face in enumerate(result.Faces, 1)
                              if face.isSame(patch) or shape_digest(face) == shape_digest(patch)
                              or _edge_distance(face, patch) <= 1.0e-7), None)
            if match is None:
                raise PhysicalDesignError("sewn result lost a candidate patch face")
            result_faces.append(match)
        return result, result_kind, result_faces
    except PhysicalDesignError:
        raise
    except Exception as exc:
        raise PhysicalDesignError(f"selected topology replacement/sewing failed: {exc}") from exc


def _remove_objects(document, names):
    for name in reversed(list(names)):
        try:
            if document.getObject(name) is not None:
                document.removeObject(name)
        except Exception:
            pass
    document.recompute()


def _bounds_within(target_shape, candidate_shape, expansion):
    target = target_shape.BoundBox
    candidate = candidate_shape.BoundBox
    limits = ((candidate.XMin >= target.XMin - expansion,
               candidate.XMax <= target.XMax + expansion),
              (candidate.YMin >= target.YMin - expansion,
               candidate.YMax <= target.YMax + expansion),
              (candidate.ZMin >= target.ZMin - expansion,
               candidate.ZMax <= target.ZMax + expansion))
    if not all(low and high for low, high in limits):
        raise PhysicalDesignError("candidate exceeds permitted expansion")


def _unselected_face_signatures(shape, selected_faces):
    selected = set(selected_faces)
    return Counter(shape_digest(face) for index, face in enumerate(shape.Faces, 1)
                   if index not in selected)


def preview_local_redesign_v2(document, workspace_root, redesign_path):
    root = Path(workspace_root).expanduser().resolve()
    path, raw = _workspace_json(root, redesign_path, "local-redesign/2 contract")
    redesign = validate_local_redesign_v2(raw)
    if redesign["status"] != "program_ready":
        raise PhysicalDesignError("local-redesign/2 capture is missing a typed patch program")
    target = _find_semantic(document, redesign["target"]["semantic_id"])
    _verify_target_snapshot(target, redesign["target"])
    before_snapshot = _semantic_snapshot(document)
    existing_names = {obj.Name for obj in getattr(document, "Objects", [])}
    created_names = []
    try:
        receipt = execute_vector_program(document, redesign["program"])
        created_names.extend(item for item in receipt["objects"] if item not in existing_names)
        match = document.getObject("DS_V2_" + re.sub(r"[^A-Za-z0-9_]", "_", redesign["candidate_command_id"]))
        if match is None:
            raise PhysicalDesignError("candidate command object was not created")
        candidate_faces = [_face_index(name) for name in redesign["candidate_faces"]]
        target_faces = [_face_index(name) for name in redesign["target"]["selected_faces"]]
        result_shape, result_kind, result_faces = _replace_faces(
            target.Shape, match.Shape, target_faces, candidate_faces)
        quality = _quality(result_shape)
        _bounds_within(target.Shape, result_shape,
                       redesign["constraints"]["permitted_expansion_mm"])
        boundary = _boundary_verification(
            target.Shape, result_shape, target_faces, result_faces,
            redesign["constraints"]["boundary_deviation_mm"])
        continuity = _continuity_verification(
            result_shape, result_faces,
            redesign["constraints"]["continuity_required"],
            redesign["constraints"]["g1_normal_angle_deg"],
            redesign["constraints"]["g2_curvature_delta"])
        if _unselected_face_signatures(target.Shape, target_faces) != \
                _unselected_face_signatures(result_shape, result_faces):
            raise PhysicalDesignError("unselected target faces changed during local replacement")
        semantic_after_program = _semantic_snapshot(document)
        changed = [semantic_id for semantic_id, digest in before_snapshot.items()
                   if semantic_after_program.get(semantic_id) != digest]
        if changed:
            raise PhysicalDesignError("unrelated semantic objects changed: " + ", ".join(changed))
        protected_checks = []
        for item in redesign["constraints"]["protected_objects"]:
            protected = _find_semantic(document, item["semantic_id"])
            if shape_digest(protected.Shape) != item["shape_sha256"]:
                raise PhysicalDesignError(f"protected object changed: {item['semantic_id']}")
            distance = float(result_shape.distToShape(protected.Shape)[0])
            interference = float(result_shape.common(protected.Shape).Volume)
            passed = interference <= 1.0e-7 and distance + 1.0e-7 >= item["clearance_mm"]
            protected_checks.append({"semantic_id": item["semantic_id"], "distance_mm": distance,
                                     "interference_mm3": interference,
                                     "required_clearance_mm": item["clearance_mm"], "passed": passed})
            if not passed:
                raise PhysicalDesignError(f"candidate interferes with protected object {item['semantic_id']}")
        controller = document.getObject(receipt["controller"])
        preview_semantic = f"preview:{redesign['redesign_id']}"
        preview_object = document.addObject("PartDesign::FeaturePython",
                                            "DS_V2_LocalPatch_" + re.sub(r"[^A-Za-z0-9_]", "_", redesign["redesign_id"]))
        created_names.append(preview_object.Name)
        preview_object.Label = f"Local patch preview · {redesign['redesign_id']}"
        preview_object.Shape = result_shape
        _add_property(preview_object, "App::PropertyString", "DesignStudioSemanticId",
                      "DesignStudio Semantic", preview_semantic)
        _add_property(preview_object, "App::PropertyString", "DesignStudioRole",
                      "DesignStudio Semantic", "local_redesign_preview")
        _add_property(preview_object, "App::PropertyString", "ReplacesSemanticId",
                      "Local Redesign", redesign["target"]["semantic_id"])
        _add_property(preview_object, "App::PropertyString", "BuildStatus",
                      "Local Redesign", "preview_verified")
        _add_property(preview_object, "App::PropertyString", "TopologyReceiptJSON",
                      "Local Redesign", json.dumps({"boundary": boundary, "continuity": continuity,
                                                      "result_kind": result_kind}, sort_keys=True))
        if controller is not None:
            controller.addObject(preview_object)
            _add_property(controller, "App::PropertyString", "LocalRedesignSchema",
                          "Local Redesign", SCHEMA)
            _add_property(controller, "App::PropertyLink", "RedesignTarget",
                          "Local Redesign", target)
        if getattr(preview_object, "ViewObject", None) is not None:
            preview_object.ViewObject.Visibility = True
            preview_object.ViewObject.ShapeColor = (0.20, 0.75, 0.95)
            preview_object.ViewObject.Transparency = 25
        receipt_data = {
            "schema": PREVIEW_SCHEMA, "preview_id": str(uuid.uuid4()),
            "redesign_id": redesign["redesign_id"], "redesign_path": str(path.relative_to(root)),
            "redesign_sha256": canonical_digest(redesign),
            "target_semantic_id": redesign["target"]["semantic_id"],
            "target_object": target.Name, "target_shape_sha256": shape_digest(target.Shape),
            "candidate_source_object": match.Name, "preview_object": preview_object.Name,
            "controller_object": controller.Name if controller is not None else "",
            "created_objects": created_names, "before_semantic_shapes": before_snapshot,
            "selected_faces": redesign["target"]["selected_faces"],
            "candidate_faces": redesign["candidate_faces"],
            "result_faces": [f"Face{index}" for index in result_faces],
            "boundary_check": boundary, "continuity_check": continuity,
            "protected_checks": protected_checks, "result_kind": result_kind,
            "quality_check": quality,
            "candidate_shape_sha256": shape_digest(result_shape),
            "baseline_retained": document.getObject(target.Name) is not None,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        receipt_data["preview_sha256"] = canonical_digest(receipt_data)
        receipt_path = root / "contracts" / f"local-redesign-v2-preview-{redesign['redesign_id']}.json"
        receipt_path.write_text(json.dumps(receipt_data, indent=2) + "\n", encoding="utf-8")
        document.recompute()
        document.save()
        return {"preview": receipt_data, "receipt_path": str(receipt_path),
                "program_receipt": receipt}
    except Exception:
        _remove_objects(document, created_names)
        raise


def _verify_receipt_digest(receipt):
    material = dict(receipt)
    claimed = material.pop("preview_sha256", None)
    if not claimed or canonical_digest(material) != claimed:
        raise PhysicalDesignError("preview receipt digest is invalid")


def discard_local_redesign_v2(document, workspace_root, preview_receipt_path):
    """Reject an uncommitted preview without touching its retained baseline.

    Every removable object name comes from the digest-verified preview receipt.
    A branch that already owns the stable semantic identity must use rollback
    instead, so Reject can never silently undo an approved design revision.
    """
    root = Path(workspace_root).expanduser().resolve()
    receipt_path, preview = _workspace_json(
        root, preview_receipt_path, "local-redesign/2 preview receipt")
    if preview.get("schema") != PREVIEW_SCHEMA:
        raise PhysicalDesignError("preview receipt schema is invalid")
    _verify_receipt_digest(preview)
    preview_object = document.getObject(preview["preview_object"])
    if preview_object is None:
        raise PhysicalDesignError("local patch preview branch is missing")
    if str(getattr(preview_object, "LocalRedesignStatus", "")) == "committed" or \
            str(getattr(preview_object, "DesignStudioSemanticId", "")) == \
            preview["target_semantic_id"]:
        raise PhysicalDesignError("preview is already committed; use rollback")
    target = _find_semantic(document, preview["target_semantic_id"])
    if target.Name != preview["target_object"] or \
            shape_digest(target.Shape) != preview["target_shape_sha256"]:
        raise PhysicalDesignError("target changed after preview; reject is no longer safe")
    if shape_digest(preview_object.Shape) != preview["candidate_shape_sha256"]:
        raise PhysicalDesignError("preview patch changed after verification")
    current = _semantic_snapshot(document)
    changed = [semantic_id for semantic_id, digest in
               preview["before_semantic_shapes"].items()
               if current.get(semantic_id) != digest]
    if changed:
        raise PhysicalDesignError(
            "unrelated semantic objects changed after preview: " + ", ".join(changed))
    created = preview.get("created_objects")
    if not isinstance(created, list) or preview["preview_object"] not in created or \
            not all(isinstance(name, str) and name for name in created):
        raise PhysicalDesignError("preview receipt has an invalid created-object set")
    opened = hasattr(document, "openTransaction")
    if opened:
        document.openTransaction(
            f"Reject topology local redesign {preview['redesign_id']}")
    try:
        existing = [name for name in created if document.getObject(name) is not None]
        _remove_objects(document, created)
        document.save()
        discarded = {
            "schema": DISCARD_SCHEMA,
            "redesign_id": preview["redesign_id"],
            "preview_sha256": preview["preview_sha256"],
            "target_semantic_id": preview["target_semantic_id"],
            "target_shape_sha256": preview["target_shape_sha256"],
            "removed_preview_objects": existing,
            "baseline_retained": document.getObject(target.Name) is not None,
            "discarded_utc": datetime.now(timezone.utc).isoformat(),
        }
        if not discarded["baseline_retained"] or \
                shape_digest(target.Shape) != preview["target_shape_sha256"]:
            raise PhysicalDesignError("baseline changed while rejecting preview")
        discarded["discard_sha256"] = canonical_digest(discarded)
        destination = root / "contracts" / \
            f"local-redesign-v2-discard-{preview['redesign_id']}.json"
        destination.write_text(
            json.dumps(discarded, indent=2) + "\n", encoding="utf-8")
        if opened:
            document.commitTransaction()
        return {"discard": discarded, "receipt_path": str(destination),
                "source_preview_path": str(receipt_path)}
    except Exception:
        if opened:
            document.abortTransaction()
        document.recompute()
        raise


def commit_local_redesign_v2(document, workspace_root, preview_receipt_path):
    root = Path(workspace_root).expanduser().resolve()
    receipt_path, preview = _workspace_json(root, preview_receipt_path, "local-redesign/2 preview receipt")
    if preview.get("schema") != PREVIEW_SCHEMA:
        raise PhysicalDesignError("preview receipt schema is invalid")
    _verify_receipt_digest(preview)
    redesign_path = root / _relative(preview["redesign_path"], "redesign_path")
    path, raw = _workspace_json(root, redesign_path, "local-redesign/2 contract")
    redesign = validate_local_redesign_v2(raw)
    if canonical_digest(redesign) != preview["redesign_sha256"]:
        raise PhysicalDesignError("redesign contract changed after preview")
    target = _find_semantic(document, preview["target_semantic_id"])
    preview_object = document.getObject(preview["preview_object"])
    if preview_object is None:
        raise PhysicalDesignError("local patch preview branch is missing")
    if shape_digest(target.Shape) != preview["target_shape_sha256"]:
        raise PhysicalDesignError("target changed after preview")
    if shape_digest(preview_object.Shape) != preview["candidate_shape_sha256"]:
        raise PhysicalDesignError("preview patch changed after verification")
    boundary_check = preview.get("boundary_check")
    continuity_check = preview.get("continuity_check")
    protected_checks = preview.get("protected_checks")
    if not isinstance(boundary_check, dict) or boundary_check.get("passed") is not True:
        raise PhysicalDesignError("preview boundary verification is missing or failed")
    if not isinstance(continuity_check, dict) or continuity_check.get("passed") is not True:
        raise PhysicalDesignError("preview continuity verification is missing or failed")
    if not isinstance(protected_checks, list) or any(
            not isinstance(item, dict) or item.get("passed") is not True
            for item in protected_checks):
        raise PhysicalDesignError("preview protected-clearance verification is missing or failed")
    current = _semantic_snapshot(document)
    changed = [semantic_id for semantic_id, digest in preview["before_semantic_shapes"].items()
               if current.get(semantic_id) != digest]
    if changed:
        raise PhysicalDesignError("unrelated semantic objects changed after preview")
    stable_id = preview["target_semantic_id"]
    baseline_id = f"{stable_id}::baseline::{preview['preview_id']}"
    opened = hasattr(document, "openTransaction")
    if opened:
        document.openTransaction(f"Commit topology local redesign {redesign['redesign_id']}")
    try:
        _add_property(target, "App::PropertyString", "OriginalSemanticId", "Local Redesign", stable_id)
        target.DesignStudioSemanticId = baseline_id
        _add_property(target, "App::PropertyString", "SupersededBySemanticId", "Local Redesign", stable_id)
        _add_property(target, "App::PropertyString", "LocalRedesignStatus", "Local Redesign", "retained_baseline")
        preview_object.DesignStudioSemanticId = stable_id
        _add_property(preview_object, "App::PropertyString", "BaselineSemanticId", "Local Redesign", baseline_id)
        _add_property(preview_object, "App::PropertyString", "LocalRedesignStatus", "Local Redesign", "committed")
        target_view = getattr(target, "ViewObject", None)
        preview_view = getattr(preview_object, "ViewObject", None)
        if target_view is not None:
            target_view.Visibility = False
        if preview_view is not None:
            preview_view.Visibility = True
            preview_view.Transparency = 0
        document.recompute()
        document.save()
        commit = {"schema": COMMIT_SCHEMA, "preview_sha256": preview["preview_sha256"],
                  "redesign_id": redesign["redesign_id"], "stable_semantic_id": stable_id,
                  "preview_object": preview_object.Name, "retained_baseline_object": target.Name,
                  "retained_baseline_semantic_id": baseline_id, "baseline_deleted": False,
                  "rollback_capable": True, "committed_utc": datetime.now(timezone.utc).isoformat()}
        commit["commit_sha256"] = canonical_digest(commit)
        commit_path = root / "contracts" / f"local-redesign-v2-commit-{redesign['redesign_id']}.json"
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


def rollback_local_redesign_v2(document, workspace_root, commit_receipt_path):
    root = Path(workspace_root).expanduser().resolve()
    receipt_path, commit = _workspace_json(root, commit_receipt_path, "local-redesign/2 commit receipt")
    if commit.get("schema") != COMMIT_SCHEMA:
        raise PhysicalDesignError("commit receipt schema is invalid")
    material = dict(commit)
    claimed = material.pop("commit_sha256", None)
    if not claimed or canonical_digest(material) != claimed:
        raise PhysicalDesignError("commit receipt digest is invalid")
    candidate = document.getObject(commit["preview_object"])
    baseline = document.getObject(commit["retained_baseline_object"])
    if candidate is None or baseline is None:
        raise PhysicalDesignError("rollback branch objects are missing")
    candidate.DesignStudioSemanticId = commit["retained_baseline_semantic_id"]
    baseline.DesignStudioSemanticId = commit["stable_semantic_id"]
    baseline.LocalRedesignStatus = "rollback_restored"
    candidate.LocalRedesignStatus = "rollback_candidate_hidden"
    if getattr(candidate, "ViewObject", None) is not None:
        candidate.ViewObject.Visibility = False
    if getattr(baseline, "ViewObject", None) is not None:
        baseline.ViewObject.Visibility = True
    document.recompute()
    document.save()
    rollback = {"schema": ROLLBACK_SCHEMA, "commit_sha256": claimed,
                "stable_semantic_id": commit["stable_semantic_id"],
                "restored_baseline_object": baseline.Name,
                "hidden_candidate_object": candidate.Name,
                "rolled_back_utc": datetime.now(timezone.utc).isoformat()}
    rollback["rollback_sha256"] = canonical_digest(rollback)
    destination = root / "contracts" / f"local-redesign-v2-rollback-{commit['redesign_id']}.json"
    destination.write_text(json.dumps(rollback, indent=2) + "\n", encoding="utf-8")
    return {"rollback": rollback, "receipt_path": str(destination),
            "source_commit_path": str(receipt_path)}
