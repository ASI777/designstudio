"""Strict AP242/XCAF semantic-assembly sidecar generation.

FreeCAD remains the B-Rep and assembly authority.  This module only publishes
the identity/appearance data needed by the DesignStudio display cache.  Mesh
triangle ranges must be supplied by the same tessellation/export pass; this
module never guesses ownership from screen coordinates or object bounds.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA = "design-studio.semantic-assembly/2"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")


class SemanticAssemblyError(ValueError):
    """The identity contract cannot be published without unsafe guessing."""


def _digest(value: str, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise SemanticAssemblyError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticAssemblyError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise SemanticAssemblyError(f"{label} must be a finite number")
    return number


def _identity(value, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SemanticAssemblyError(f"{label} is not a valid semantic ID")
    return value


def _color(obj) -> list[float]:
    view = getattr(obj, "ViewObject", None)
    raw = getattr(view, "ShapeColor", ()) if view is not None else ()
    if not raw:
        raw = getattr(obj, "DesignStudioColor", ())
    values = list(raw) if raw is not None else []
    if len(values) < 3:
        values = [0.5, 0.5, 0.5]
    return [max(0.0, min(1.0, _finite(values[index], "color_rgba"))) for index in range(3)] + [1.0]


def _matrix(obj) -> list[float]:
    placement = getattr(obj, "Placement", None)
    matrix = placement.toMatrix() if placement is not None and hasattr(placement, "toMatrix") else None
    values = []
    for row in range(1, 5):
        for column in range(1, 5):
            default = 1.0 if row == column else 0.0
            values.append(_finite(getattr(matrix, f"A{row}{column}", default), "placement"))
    return values


def _shape_faces(obj) -> list[str]:
    shape = getattr(obj, "Shape", None)
    faces = getattr(shape, "Faces", ()) if shape is not None else ()
    return [f"{getattr(obj, 'Name', 'object')}:Face{index}"
            for index in range(1, len(faces) + 1)]


def _material(obj) -> str:
    for name in ("MaterialName", "Material", "DesignStudioMaterial"):
        value = str(getattr(obj, name, "") or "").strip()
        if value:
            return value
    return "unspecified"


def build_semantic_assembly(document, step_path, triangle_ranges,
                            tessellation_sha256, objects=None):
    """Return a strict semantic-assembly/2 payload for an AP242 export.

    ``triangle_ranges`` maps each stable semantic ID to ``(start, count)``.
    It is intentionally mandatory: callers must use ownership ranges produced
    by the authoritative OCCT/XCAF tessellation pass.  If a range is missing,
    ambiguous, or does not cover the complete display mesh, generation fails.
    """
    step = Path(step_path).expanduser().resolve()
    if not step.is_file() or step.stat().st_size <= 0:
        raise SemanticAssemblyError("source AP242 STEP does not exist or is empty")
    if not isinstance(triangle_ranges, dict) or not triangle_ranges:
        raise SemanticAssemblyError("explicit XCAF triangle ownership ranges are required")
    source_sha = hashlib.sha256(step.read_bytes()).hexdigest()
    tessellation_sha = _digest(tessellation_sha256, "tessellation_sha256")
    selected = list(objects) if objects is not None else list(getattr(document, "Objects", ()))
    records = []
    ids = set()
    for obj in selected:
        shape = getattr(obj, "Shape", None)
        if shape is None or getattr(shape, "isNull", lambda: True)():
            continue
        semantic_id = _identity(str(getattr(obj, "DesignStudioSemanticId", "")).strip(),
                                "DesignStudioSemanticId")
        if semantic_id in ids:
            raise SemanticAssemblyError(f"duplicate semantic ID: {semantic_id}")
        ids.add(semantic_id)
        if semantic_id not in triangle_ranges:
            raise SemanticAssemblyError(f"no authoritative triangle range for {semantic_id}")
        range_value = triangle_ranges[semantic_id]
        if (not isinstance(range_value, (list, tuple)) or len(range_value) != 2
                or any(isinstance(item, bool) or not isinstance(item, int) for item in range_value)):
            raise SemanticAssemblyError(f"triangle range for {semantic_id} is invalid")
        start, count = range_value
        if start < 0 or count <= 0:
            raise SemanticAssemblyError(f"triangle range for {semantic_id} is invalid")
        parent_id = str(getattr(obj, "DesignStudioParentId", "assembly-root") or "assembly-root")
        _identity(parent_id, f"parent_id for {semantic_id}")
        name = str(getattr(obj, "Label", "") or getattr(obj, "Name", semantic_id)).strip()
        if not name:
            raise SemanticAssemblyError(f"component name for {semantic_id} is empty")
        records.append({
            "semantic_id": semantic_id,
            "parent_id": parent_id,
            "name": name,
            "reference_designator": str(getattr(obj, "ReferenceDesignator", "") or ""),
            "material": _material(obj),
            "color_rgba": _color(obj),
            "placement": _matrix(obj),
            "triangle_start": start,
            "triangle_count": count,
            "face_ids": _shape_faces(obj),
        })
    records.sort(key=lambda item: item["triangle_start"])
    if not records:
        raise SemanticAssemblyError("no shaped semantic objects were supplied")
    if set(triangle_ranges) != ids:
        raise SemanticAssemblyError("triangle ranges contain an unknown or missing semantic ID")
    next_triangle = 0
    for record in records:
        if record["triangle_start"] != next_triangle:
            raise SemanticAssemblyError("triangle ranges must be ordered and contiguous")
        next_triangle += record["triangle_count"]
    payload = {
        "schema": SCHEMA,
        "source_format": "AP242",
        "source_step_sha256": source_sha,
        "tessellation_sha256": tessellation_sha,
        "identity_available": True,
        "legacy_flattened": False,
        "components": records,
    }
    return payload


def write_semantic_assembly_sidecar(document, step_path, triangle_ranges,
                                    tessellation_sha256, objects=None):
    """Write the digest-bound sidecar adjacent to the AP242 STEP file."""
    step = Path(step_path).expanduser().resolve()
    payload = build_semantic_assembly(document, step, triangle_ranges,
                                      tessellation_sha256, objects=objects)
    target = Path(str(step) + ".assembly.json")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return {"path": str(target), "payload": payload,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
