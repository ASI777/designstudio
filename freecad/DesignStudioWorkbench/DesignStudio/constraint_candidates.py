"""Constraint-driven, editable physical-design alternatives.

This module is intentionally separate from the v1 physical-design receipt.  A
v2 session records the evidence and constraints supplied by the user, then
builds exactly three typed mechanical-cad-program/2 candidates.  FreeCAD and
OpenCASCADE remain the geometry authority; images and sketches only influence
the recorded intent, calibration and deterministic parameter choices.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from .physical_design import PhysicalDesignError, canonical_digest, file_digest, ensure_semantic_id, shape_digest


SESSION_SCHEMA = "design-studio.physical-design-session/2"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_KINDS = {"hardware", "mechanism", "pcb", "battery", "display", "connector", "cable",
          "human_clearance", "service", "other"}
_LABELS = ("compact", "balanced", "comfort")


class ConstraintCandidateError(PhysicalDesignError):
    """Raised when a physical candidate cannot be verified fail-closed."""


def _strict(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ConstraintCandidateError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise ConstraintCandidateError(
            f"{label} keys invalid; unknown={sorted(unknown)}, missing={sorted(missing)}")


def _finite(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ConstraintCandidateError(f"{label} must be finite")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConstraintCandidateError(f"{label} must be >= {minimum}")
    return value


def _id(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ConstraintCandidateError(f"{label} is not a valid identifier")
    return value


def _digest(value, label):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ConstraintCandidateError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _point(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ConstraintCandidateError(f"{label} must contain three coordinates")
    return [_finite(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _bounds(value, label):
    _strict(value, {"min_mm", "max_mm"}, {"min_mm", "max_mm"}, label)
    low, high = _point(value["min_mm"], f"{label}.min_mm"), _point(value["max_mm"], f"{label}.max_mm")
    if any(high[index] <= low[index] for index in range(3)):
        raise ConstraintCandidateError(f"{label} must have positive extents")
    return {"min_mm": low, "max_mm": high}


def _relative(path, label):
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ConstraintCandidateError(f"{label} must be a workspace-relative path")
    return Path(path)


def _schema_validate(value):
    """Run the public Draft 2020-12 schema when jsonschema is available."""
    try:
        from jsonschema import Draft202012Validator
        schema_path = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "physical-design-session-v2.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
        if errors:
            detail = f"{errors[0].validator}: {errors[0].message}"
            if errors[0].context:
                detail += "; " + "; ".join(f"{item.validator}:{item.message}" for item in errors[0].context[:8])
            raise ConstraintCandidateError("public v2 schema rejected session at " + ".".join(str(item) for item in errors[0].absolute_path) + ": " + detail)
    except ImportError:
        # The host validator below remains authoritative on minimal FreeCAD
        # installations that do not ship Python jsonschema.
        return


def _validate_asset(asset, index, root):
    keys = {"asset_id", "path", "sha256", "media_type", "input_kind", "role",
            "geometry_authority", "measurement_status", "calibration"}
    _strict(asset, keys, keys - {"calibration"}, f"input_assets[{index}]")
    _id(asset["asset_id"], f"input_assets[{index}].asset_id")
    relative = _relative(asset["path"], f"input_assets[{index}].path")
    claimed = _digest(asset["sha256"], f"input_assets[{index}].sha256")
    if asset["media_type"] not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml", "application/pdf"}:
        raise ConstraintCandidateError(f"input_assets[{index}].media_type is unsupported")
    if asset["input_kind"] not in {"object_photo", "render", "hand_sketch", "vector_sketch", "engineering_drawing", "scan"}:
        raise ConstraintCandidateError(f"input_assets[{index}].input_kind is unsupported")
    if asset["role"] not in {"inspiration", "silhouette", "layout", "measurement_reference", "material_reference"}:
        raise ConstraintCandidateError(f"input_assets[{index}].role is unsupported")
    if asset["geometry_authority"] is not False:
        raise ConstraintCandidateError("raster and sketch evidence cannot be geometry authority")
    if asset["measurement_status"] not in {"none", "uncalibrated", "user_calibrated", "drawing_dimensions_verified"}:
        raise ConstraintCandidateError(f"input_assets[{index}].measurement_status is unsupported")
    if "calibration" in asset and asset["calibration"] is not None:
        calibration = asset["calibration"]
        _strict(calibration, {"method", "pixels_per_mm", "verified"}, {"method", "pixels_per_mm", "verified"},
                f"input_assets[{index}].calibration")
        if calibration["method"] not in {"known_length", "drawing_scale", "manual"}:
            raise ConstraintCandidateError("unsupported image calibration method")
        _finite(calibration["pixels_per_mm"], "pixels_per_mm", 1e-12)
        if not isinstance(calibration["verified"], bool):
            raise ConstraintCandidateError("calibration.verified must be boolean")
    if root is not None:
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file() or file_digest(resolved) != claimed:
            raise ConstraintCandidateError(f"input asset {asset['asset_id']} is missing or stale")


def _validate_volume(volume, index, label):
    keys = {"volume_id", "semantic_id", "kind", "min_mm", "max_mm", "clearance_mm", "locked", "source"}
    _strict(volume, keys, keys, f"{label}[{index}]")
    _id(volume["volume_id"], f"{label}[{index}].volume_id")
    _id(volume["semantic_id"], f"{label}[{index}].semantic_id")
    if volume["kind"] not in _KINDS:
        raise ConstraintCandidateError(f"{label}[{index}].kind is unsupported")
    bounds = _bounds({"min_mm": volume["min_mm"], "max_mm": volume["max_mm"]}, f"{label}[{index}]")
    _finite(volume["clearance_mm"], f"{label}[{index}].clearance_mm", 0.0)
    if volume["locked"] is not True:
        raise ConstraintCandidateError(f"{label}[{index}] must be locked")
    if not isinstance(volume["source"], str) or not volume["source"].strip():
        raise ConstraintCandidateError(f"{label}[{index}].source is required")
    return bounds


def validate_physical_design_session_v2(value, workspace_root=None):
    """Validate a v2 session before any FreeCAD document mutation."""
    session = deepcopy(value)
    _schema_validate(session)
    allowed = {"schema", "session_id", "revision", "product_family", "workflow_mode", "document", "intent",
               "input_assets", "verified_dimensions", "occupied_volumes", "clearance_volumes", "desire_capture",
               "interaction_objectives", "material_constraints", "manufacturing_constraints", "hard_constraints",
               "scoring_weights", "unresolved_evidence", "geometry_authority", "candidate_policy", "candidates",
               "provenance", "ranking_status"}
    required = allowed - {"ranking_status"}
    _strict(session, allowed, required, "physical design session v2")
    if session["schema"] != SESSION_SCHEMA:
        raise ConstraintCandidateError(f"schema must be {SESSION_SCHEMA}")
    _id(session["session_id"], "session_id")
    if type(session["revision"]) is not int or session["revision"] < 1:
        raise ConstraintCandidateError("revision must be a positive integer")
    if session["workflow_mode"] not in {"inside_out", "outside_in", "co_design"}:
        raise ConstraintCandidateError("workflow_mode is invalid")
    if not isinstance(session["product_family"], str) or not session["product_family"].strip():
        raise ConstraintCandidateError("product_family is required")
    if session.get("ranking_status", "provisional") != "provisional":
        raise ConstraintCandidateError(
            "ranking_status must remain provisional until a digest-bound "
            "physical candidate decision is available")
    if not isinstance(session["intent"], str) or not session["intent"].strip():
        raise ConstraintCandidateError("intent is required")
    document = session["document"]
    _strict(document, {"document_id", "path", "sha256"}, {"document_id", "path", "sha256"}, "document")
    _id(document["document_id"], "document.document_id")
    document_path = _relative(document["path"], "document.path")
    _digest(document["sha256"], "document.sha256")
    root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else None
    if root is not None:
        resolved = (root / document_path).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file() or file_digest(resolved) != document["sha256"]:
            raise ConstraintCandidateError("document.path is missing or has a stale digest")

    assets = session["input_assets"]
    if not isinstance(assets, list):
        raise ConstraintCandidateError("input_assets must be an array")
    seen = set()
    for index, asset in enumerate(assets):
        _validate_asset(asset, index, root)
        if asset["asset_id"] in seen:
            raise ConstraintCandidateError("input asset IDs must be unique")
        seen.add(asset["asset_id"])

    dimensions = session["verified_dimensions"]
    if not isinstance(dimensions, list):
        raise ConstraintCandidateError("verified_dimensions must be an array")
    seen.clear()
    for index, dimension in enumerate(dimensions):
        keys = {"dimension_id", "source", "axis", "value_mm", "tolerance_mm", "status"}
        _strict(dimension, keys, keys, f"verified_dimensions[{index}]")
        _id(dimension["dimension_id"], f"verified_dimensions[{index}].dimension_id")
        if dimension["dimension_id"] in seen:
            raise ConstraintCandidateError("verified dimension IDs must be unique")
        seen.add(dimension["dimension_id"])
        if dimension["axis"] not in {"X", "Y", "Z", "diameter", "radius", "other"}:
            raise ConstraintCandidateError("verified dimension axis is invalid")
        _finite(dimension["value_mm"], "verified dimension value", 1e-12)
        _finite(dimension["tolerance_mm"], "verified dimension tolerance", 0.0)
        if dimension["status"] not in {"verified", "user_calibrated", "unresolved"}:
            raise ConstraintCandidateError("verified dimension status is invalid")

    volumes = session["occupied_volumes"]
    if not isinstance(volumes, list) or not volumes:
        raise ConstraintCandidateError("at least one locked occupied volume is required")
    volume_ids, semantic_ids = set(), set()
    for index, volume in enumerate(volumes):
        _validate_volume(volume, index, "occupied_volumes")
        if volume["volume_id"] in volume_ids or volume["semantic_id"] in semantic_ids:
            raise ConstraintCandidateError("occupied volume and semantic IDs must be unique")
        volume_ids.add(volume["volume_id"])
        semantic_ids.add(volume["semantic_id"])
    for index, volume in enumerate(session["clearance_volumes"]):
        _validate_volume(volume, index, "clearance_volumes")

    for index, desire in enumerate(session["desire_capture"]):
        keys = {"desire_id", "statement", "priority", "status"}
        _strict(desire, keys, keys, f"desire_capture[{index}]")
        _id(desire["desire_id"], f"desire_capture[{index}].desire_id")
        if not desire["statement"].strip() or desire["priority"] not in {"required", "preferred", "exploratory"}:
            raise ConstraintCandidateError("desire capture is invalid")
        if desire["status"] not in {"unverified", "user_confirmed", "measured"}:
            raise ConstraintCandidateError("desire status is invalid")
    for index, objective in enumerate(session["interaction_objectives"]):
        keys = {"objective_id", "metric", "target", "unit", "status"}
        _strict(objective, keys, keys, f"interaction_objectives[{index}]")
        _id(objective["objective_id"], f"interaction_objectives[{index}].objective_id")
        if objective["metric"] not in {"reach", "grip", "viewing_angle", "finger_clearance", "service_access", "other"}:
            raise ConstraintCandidateError("interaction objective metric is invalid")
        _finite(objective["target"], "interaction objective target")
        if not isinstance(objective["unit"], str) or not objective["unit"].strip() or objective["status"] not in {"verified", "user_confirmed", "unresolved"}:
            raise ConstraintCandidateError("interaction objective is invalid")
    for index, material in enumerate(session["material_constraints"]):
        keys = {"region_id", "candidate_materials", "requirements", "evidence_status"}
        _strict(material, keys, keys, f"material_constraints[{index}]")
        _id(material["region_id"], f"material_constraints[{index}].region_id")
        if not material["candidate_materials"] or not all(isinstance(item, str) and item for item in material["candidate_materials"]):
            raise ConstraintCandidateError("material candidate list is empty")
        if material["evidence_status"] not in {"unverified", "datasheet_backed", "test_backed"}:
            raise ConstraintCandidateError("material evidence status is invalid")

    manufacturing = session["manufacturing_constraints"]
    _strict(manufacturing, {"processes", "units", "minimum_wall_mm", "minimum_feature_mm", "status"},
            {"processes", "units", "minimum_wall_mm", "minimum_feature_mm", "status"}, "manufacturing_constraints")
    if not manufacturing["processes"] or manufacturing["units"] != "mm":
        raise ConstraintCandidateError("manufacturing process and millimetres are required")
    _finite(manufacturing["minimum_wall_mm"], "minimum_wall_mm", 1e-9)
    _finite(manufacturing["minimum_feature_mm"], "minimum_feature_mm", 1e-9)
    if manufacturing["status"] not in {"provisional", "process_reviewed", "supplier_confirmed"}:
        raise ConstraintCandidateError("manufacturing status is invalid")

    hard = session["hard_constraints"]
    _strict(hard, {"locked_envelope", "max_external_dimensions_mm", "minimum_clearance_mm", "require_watertight_solid", "forbid_interference"},
            {"locked_envelope", "max_external_dimensions_mm", "minimum_clearance_mm", "require_watertight_solid", "forbid_interference"}, "hard_constraints")
    envelope = _bounds(hard["locked_envelope"], "hard_constraints.locked_envelope")
    max_dims = _point(hard["max_external_dimensions_mm"], "hard_constraints.max_external_dimensions_mm")
    spans = [envelope["max_mm"][i] - envelope["min_mm"][i] for i in range(3)]
    if any(spans[i] > max_dims[i] + 1e-9 for i in range(3)):
        raise ConstraintCandidateError("locked envelope exceeds external dimension limit")
    _finite(hard["minimum_clearance_mm"], "hard_constraints.minimum_clearance_mm", 0.0)
    if not all(isinstance(hard[key], bool) for key in ("require_watertight_solid", "forbid_interference")):
        raise ConstraintCandidateError("hard constraint booleans are invalid")

    weights = session["scoring_weights"]
    expected = {"occupied_volume", "reach", "clearance", "wall", "continuity", "manufacturability", "mass", "material_use", "user_priority"}
    _strict(weights, expected, expected, "scoring_weights")
    for key, weight in weights.items():
        _finite(weight, f"scoring_weights.{key}", 0.0)
    if sum(weights.values()) <= 0.0:
        raise ConstraintCandidateError("at least one scoring weight must be positive")

    authority = session["geometry_authority"]
    _strict(authority, {"authoritative_sources", "raster_and_sketch_are_geometry_authority", "unknown_dimensions_policy"},
            {"authoritative_sources", "raster_and_sketch_are_geometry_authority", "unknown_dimensions_policy"}, "geometry_authority")
    if authority["authoritative_sources"] != ["freecad_brep", "typed_mechanical_cad_program", "verified_measurements"] or authority["raster_and_sketch_are_geometry_authority"] is not False or authority["unknown_dimensions_policy"] != "blocked_until_measured_or_explicitly_constrained":
        raise ConstraintCandidateError("geometry authority contract is invalid")

    policy = session["candidate_policy"]
    _strict(policy, {"exact_candidate_count", "labels", "ranking_policy"}, {"exact_candidate_count", "labels", "ranking_policy"}, "candidate_policy")
    if policy["exact_candidate_count"] != 3 or policy["ranking_policy"] != "hard_failures_removed_missing_analysis_incomplete":
        raise ConstraintCandidateError("candidate policy must require exactly three fail-closed alternatives")
    labels = [_id(label, "candidate_policy.labels") for label in policy["labels"]]
    family = session["product_family"].lower()
    if "controller" in family and tuple(labels) != _LABELS:
        raise ConstraintCandidateError("controller candidates must be compact, balanced and comfort")
    if len(set(labels)) != 3:
        raise ConstraintCandidateError("candidate labels must be unique")
    if session["candidates"] is not None:
        if not isinstance(session["candidates"], list) or len(session["candidates"]) != 3:
            raise ConstraintCandidateError("completed sessions must contain exactly three candidates")
        for index, candidate in enumerate(session["candidates"]):
            _validate_candidate(candidate, index, labels)
    provenance = session["provenance"]
    _strict(provenance, {"created_by", "created_utc", "user_prompt"}, {"created_by", "created_utc", "user_prompt"}, "provenance")
    if not all(isinstance(provenance[key], str) and provenance[key].strip() for key in provenance):
        raise ConstraintCandidateError("session provenance is incomplete")
    return session


def _validate_candidate(candidate, index, labels):
    keys = {"candidate_id", "label", "status", "program", "score_breakdown", "hard_failures", "analysis", "semantic_ids", "rank", "shape_digest"}
    _strict(candidate, keys, {"candidate_id", "label", "status", "program", "score_breakdown", "hard_failures", "analysis", "semantic_ids"}, f"candidates[{index}]")
    _id(candidate["candidate_id"], f"candidates[{index}].candidate_id")
    if candidate["label"] not in labels or candidate["status"] not in {"incomplete", "passed", "rejected"}:
        raise ConstraintCandidateError(f"candidate {index} label/status is invalid")
    if candidate["status"] == "rejected" and not candidate["hard_failures"]:
        raise ConstraintCandidateError("rejected candidate must explain its hard failure")
    if candidate["program"] is not None:
        from .vector_native_cad import validate_vector_program
        validate_vector_program(candidate["program"])
    if candidate.get("shape_digest") is not None:
        _digest(candidate["shape_digest"], f"candidates[{index}].shape_digest")
    if candidate.get("rank") is not None and (type(candidate["rank"]) is not int or candidate["rank"] < 1):
        raise ConstraintCandidateError("candidate rank is invalid")


def create_physical_design_session_v2(workspace_root, value):
    root = Path(workspace_root).expanduser().resolve()
    session = deepcopy(value)
    session.setdefault("ranking_status", "provisional")
    session = validate_physical_design_session_v2(session, root)
    destination = root / "contracts" / f"physical-design-session-{session['session_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(destination), "session_sha256": canonical_digest(session), "candidate_count": 0,
            "workflow_mode": session["workflow_mode"], "geometry_authority": "freecad_brep"}


def _curve(z, x0, x1, y0, y1):
    return {"degree": 1, "knots": [0.0, 1.0, 2.0, 3.0, 4.0],
            "multiplicities": [2, 1, 1, 1, 2], "weights": [1.0] * 5,
            "control_points": [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z], [x0, y0, z]],
            "closed": True, "periodic": False, "classification": "original-designed",
            "provenance": ["physical-design-session/2", "freecad-brep-authority"]}


def _program(session, label, x0, x1, y0, y1, z0, z1, mid_scale):
    mid_x0 = (x0 + x1) / 2.0 - (x1 - x0) * mid_scale / 2.0
    mid_x1 = (x0 + x1) / 2.0 + (x1 - x0) * mid_scale / 2.0
    mid_y0 = (y0 + y1) / 2.0 - (y1 - y0) * mid_scale / 2.0
    mid_y1 = (y0 + y1) / 2.0 + (y1 - y0) * mid_scale / 2.0
    base = f"candidate_{label}"
    commands = []
    for suffix, z, px0, px1, py0, py1 in (
            ("bottom", z0, x0, x1, y0, y1),
            ("middle", (z0 + z1) / 2.0, mid_x0, mid_x1, mid_y0, mid_y1),
            ("top", z1, x0, x1, y0, y1)):
        commands.append({"id": f"{label}_profile_{suffix}", "op": "curve.bspline3d",
                         "params": {"curve": _curve(z, px0, px1, py0, py1)},
                         "provenance": [f"{base}:editable-section:{suffix}"]})
    commands.append({"id": f"{label}_body", "op": "feature.loft",
                     "params": {"sections": [f"{label}_profile_bottom", f"{label}_profile_middle", f"{label}_profile_top"],
                                 "solid": True, "ruled": False,
                                 "continuity": {"required": "G1", "max_normal_angle_deg": 20.0}},
                     "provenance": [f"{base}:freecad-occt-loft"]})
    envelope = session["hard_constraints"]["locked_envelope"]
    origin = list(envelope["min_mm"])
    from .vector_native_cad import control_datum_digest, MECHANICAL_SCHEMA
    return {"schema": MECHANICAL_SCHEMA, "program_id": base, "units": "mm", "author": "DesignStudio physical candidate",
            "envelope": deepcopy(envelope),
            "control_datums": [{"id": f"{label}_origin", "point_mm": origin, "locked": True,
                                 "digest": control_datum_digest(origin)}],
            "commands": commands,
            "checks": [{"kind": "valid_shape", "target": f"{label}_body"},
                        {"kind": "watertight", "target": f"{label}_body"},
                        {"kind": "non_self_intersecting", "target": f"{label}_body"},
                        {"kind": "solid_count", "target": f"{label}_body"}]}


def _required_bounds(session):
    volumes = session["occupied_volumes"] + session["clearance_volumes"]
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    clearance = max(float(session["hard_constraints"]["minimum_clearance_mm"]),
                    *(float(item["clearance_mm"]) for item in volumes))
    for volume in volumes:
        for axis in range(3):
            low[axis] = min(low[axis], float(volume["min_mm"][axis]) - clearance)
            high[axis] = max(high[axis], float(volume["max_mm"][axis]) + clearance)
    return low, high, clearance


def _candidate_dimensions(session, label):
    envelope = session["hard_constraints"]["locked_envelope"]
    env_low, env_high = envelope["min_mm"], envelope["max_mm"]
    req_low, req_high, clearance = _required_bounds(session)
    spans = [env_high[i] - env_low[i] for i in range(3)]
    required_spans = [req_high[i] - req_low[i] for i in range(3)]
    if any(req_low[i] < env_low[i] - 1e-7 or req_high[i] > env_high[i] + 1e-7 for i in range(3)):
        return None, [f"locked occupied volume plus {clearance:g} mm clearance exceeds envelope"]
    # Measured dimensions and workflow actively choose the initial occupied
    # proportion; candidate variants then vary only within the locked envelope.
    measured = {axis: [float(item["value_mm"]) for item in session["verified_dimensions"]
                       if item["axis"] == axis and item["status"] != "unresolved"]
                for axis in ("X", "Y", "Z")}
    mode_factor = {"inside_out": 1.05, "outside_in": 0.92, "co_design": 1.0}[session["workflow_mode"]]
    variant_factor = {"compact": 0.96, "balanced": 1.0, "comfort": 1.04}.get(label, 1.0)
    dimensions = []
    for axis in range(3):
        measured_span = (sum(measured["XYZ"[axis]]) / len(measured["XYZ"[axis]])
                         if measured["XYZ"[axis]] else spans[axis] * mode_factor)
        desired = max(required_spans[axis] * 1.08, min(spans[axis], measured_span * variant_factor))
        if axis == 2:
            desired = max(required_spans[axis], min(spans[axis], desired))
        dimensions.append(min(spans[axis], desired))
    # Keep the locked volumes centred in their legal envelope while retaining
    # the exact outer envelope contract and variant-specific cross-sections.
    center = [(req_low[i] + req_high[i]) / 2.0 for i in range(3)]
    low = [max(env_low[i], center[i] - dimensions[i] / 2.0) for i in range(3)]
    high = [min(env_high[i], low[i] + dimensions[i]) for i in range(3)]
    for axis in range(3):
        if low[axis] > req_low[axis] + 1e-7 or high[axis] < req_high[axis] - 1e-7:
            low[axis], high[axis] = env_low[axis], env_high[axis]
    return (low, high), []


def _score_candidate(session, label, bounds, analyses):
    low, high = bounds
    envelope = session["hard_constraints"]["locked_envelope"]
    env_span = [envelope["max_mm"][i] - envelope["min_mm"][i] for i in range(3)]
    span = [high[i] - low[i] for i in range(3)]
    volume_score = max(0.0, min(100.0, 100.0 * (1.0 - (span[0] * span[1] * span[2]) /
                                                   max(1.0, env_span[0] * env_span[1] * env_span[2]))))
    desire_bonus = 100.0 if session["desire_capture"] else 50.0
    # Do not turn missing analysis into a heuristic score.  Only a trusted
    # adapter's explicit pass may enter either the numerator or denominator;
    # incomplete and failed metrics remain visible through ``analysis`` but
    # have no numeric influence on a provisional rank.
    pass_values = {
        "occupied_volume": volume_score,
        "clearance": 100.0,
        "continuity": 100.0,
        "user_priority": desire_bonus,
    }
    weights = session["scoring_weights"]
    scored = {}
    excluded = []
    weighted_total = 0.0
    scored_weight = 0.0
    for key, weight in weights.items():
        status = str(analyses.get(key, {}).get("status", "incomplete"))
        if status == "pass" and key in pass_values:
            value = float(pass_values[key])
            numeric_weight = float(weight)
            scored[key] = round(value, 6)
            weighted_total += value * numeric_weight
            scored_weight += numeric_weight
        else:
            excluded.append(key)
    scored["total"] = round(weighted_total / scored_weight, 6) if scored_weight else None
    scored["scored_weight"] = round(scored_weight, 6)
    # The metadata is deliberately outside score_breakdown's numeric map so
    # old receipts remain readable and incomplete metrics cannot be mistaken
    # for zero-valued evidence.
    return scored


def generate_constraint_candidates(document, workspace_root, session_path):
    """Generate exactly three editable candidates and a deterministic receipt."""
    root = Path(workspace_root).expanduser().resolve()
    source = Path(session_path).expanduser()
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    if not source.is_relative_to(root) or source.suffix.lower() != ".json" or not source.is_file():
        raise ConstraintCandidateError("session_path must identify a workspace JSON file")
    original_bytes = source.read_bytes()
    session = validate_physical_design_session_v2(json.loads(original_bytes), root)
    from .vector_native_cad import execute_vector_program
    initial_names = {obj.Name for obj in getattr(document, "Objects", [])}
    original_file = Path(document.FileName).resolve() if getattr(document, "FileName", "") else None
    original_document_bytes = original_file.read_bytes() if original_file and original_file.is_file() else None
    candidates = []
    generated_names = []
    try:
        labels = list(session["candidate_policy"]["labels"])
        locked_before = {str(volume["semantic_id"]): None for volume in session["occupied_volumes"]}
        semantic_objects = {str(getattr(obj, "DesignStudioSemanticId", "")): obj for obj in getattr(document, "Objects", [])
                            if getattr(obj, "Shape", None) is not None and not obj.Shape.isNull()}
        for semantic_id in locked_before:
            if semantic_id in semantic_objects:
                locked_before[semantic_id] = shape_digest(semantic_objects[semantic_id].Shape)
        for label in labels:
            dimensions, hard_failures = _candidate_dimensions(session, label)
            candidate_program = None
            analyses = {
                "occupied_volume": {"status": "pass" if not hard_failures else "fail", "source": "locked_volumes"},
                "clearance": {"status": "pass" if not hard_failures else "fail", "source": "locked_clearance_volumes"},
                "reach": {"status": "incomplete", "source": "exact_human_solver_unavailable"},
                "wall": {"status": "incomplete", "source": "wall_thickness_solver_required", "minimum_wall_mm": session["manufacturing_constraints"]["minimum_wall_mm"]},
                "continuity": {"status": "incomplete", "source": "pending_candidate_brep"},
                "manufacturability": {"status": "incomplete", "source": "overhang_and_process_solver_required"},
                "mass": {"status": "incomplete", "source": "material_density_unresolved"},
                "material_use": {"status": "incomplete", "source": "material_density_unresolved"},
                "user_priority": {"status": "pass" if session["desire_capture"] else "incomplete", "source": "desire_capture"},
            }
            entry = {"candidate_id": f"{session['session_id']}-{label}", "label": label,
                     "status": "rejected" if hard_failures else "incomplete", "program": None,
                     "score_breakdown": {}, "hard_failures": hard_failures, "analysis": analyses,
                     "semantic_ids": [], "rank": None, "shape_digest": None}
            if dimensions is None:
                candidates.append(entry)
                continue
            low, high = dimensions
            candidate_program = _program(session, label, low[0], high[0], low[1], high[1], low[2], high[2],
                                         {"compact": 0.92, "balanced": 0.97, "comfort": 1.0}.get(label, 0.97))
            entry["program"] = candidate_program
            try:
                result = execute_vector_program(document, candidate_program)
                generated_names.extend(result["objects"])
                body = document.getObject(f"DS_V2_{label}_body")
                if body is None or body.Shape.isNull() or not body.Shape.isValid() or len(body.Shape.Solids) != 1:
                    raise ConstraintCandidateError("candidate body is not one valid solid: "
                                                   f"type={getattr(body.Shape, 'ShapeType', None) if body else None}, "
                                                   f"solids={len(body.Shape.Solids) if body else 0}, "
                                                   f"shells={len(body.Shape.Shells) if body else 0}")
                analyses["continuity"] = {"status": "pass", "source": "OCCT loft continuity receipt",
                                           "checks": result["checks"]}
                candidate_controller = document.getObject(result["controller"])
                semantic_id = f"candidate-{session['session_id']}-{label}"
                if "DesignStudioSemanticId" not in candidate_controller.PropertiesList:
                    candidate_controller.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
                candidate_controller.DesignStudioSemanticId = semantic_id
                candidate_controller.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic") if "DesignStudioRole" not in candidate_controller.PropertiesList else None
                candidate_controller.DesignStudioRole = "physical_design_candidate"
                body_semantic = f"candidate-body-{session['session_id']}-{label}"
                if "DesignStudioSemanticId" not in body.PropertiesList:
                    body.addProperty("App::PropertyString", "DesignStudioSemanticId", "DesignStudio Semantic")
                body.DesignStudioSemanticId = body_semantic
                if "DesignStudioRole" not in body.PropertiesList:
                    body.addProperty("App::PropertyString", "DesignStudioRole", "DesignStudio Semantic")
                body.DesignStudioRole = "physical_design_candidate_body"
                candidate_controller.addProperty("App::PropertyString", "DesignStudioCandidateLabel", "Physical Design")
                candidate_controller.DesignStudioCandidateLabel = label
                candidate_controller.addProperty("App::PropertyString", "DesignStudioSessionId", "Physical Design")
                candidate_controller.DesignStudioSessionId = session["session_id"]
                candidate_controller.addProperty("App::PropertyString", "CandidateProgramJSON", "Physical Design")
                candidate_controller.CandidateProgramJSON = json.dumps(candidate_program, sort_keys=True, separators=(",", ":"))
                entry["semantic_ids"] = [semantic_id, body_semantic]
                entry["shape_digest"] = shape_digest(body.Shape)
                entry["score_breakdown"] = _score_candidate(session, label, dimensions, analyses)
            except Exception as exc:
                entry["program"] = candidate_program
                entry["status"] = "rejected"
                entry["hard_failures"].append(str(exc))
                entry["score_breakdown"] = {}
                for previous in candidates:
                    if previous["status"] != "rejected":
                        previous["status"] = "rejected"
                        previous["hard_failures"].append(
                            "candidate batch rolled back after a sibling failed")
                        previous["semantic_ids"] = []
                        previous["shape_digest"] = None
                        previous["score_breakdown"] = {}
                for name in list(generated_names):
                    if document.getObject(name) is not None:
                        document.removeObject(name)
                generated_names.clear()
            candidates.append(entry)
        for semantic_id, digest in locked_before.items():
            if digest is not None:
                obj = next((item for item in getattr(document, "Objects", []) if str(getattr(item, "DesignStudioSemanticId", "")) == semantic_id), None)
                if obj is None or shape_digest(obj.Shape) != digest:
                    raise ConstraintCandidateError(f"locked semantic object changed: {semantic_id}")
        ranked = [candidate for candidate in candidates if candidate["status"] != "rejected"]

        def rank_key(candidate):
            total = candidate["score_breakdown"].get("total")
            has_numeric_score = isinstance(total, (int, float)) and not isinstance(total, bool)
            return (0 if has_numeric_score else 1,
                    -float(total) if has_numeric_score else 0.0,
                    candidate["label"])

        ranked.sort(key=rank_key)
        for rank, candidate in enumerate(ranked, 1):
            candidate["rank"] = rank
        session["candidates"] = candidates
        session["ranking_status"] = "provisional"
        # Persist the document only after every candidate and locked-object gate passes.
        if original_file is None:
            raise ConstraintCandidateError("active document must have a saved FCStd path")
        document.recompute()
        document.saveAs(str(original_file))
        session["document"]["sha256"] = file_digest(original_file)
        session = validate_physical_design_session_v2(session, root)
        source.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt = root / "contracts" / f"physical-design-candidates-{session['session_id']}.json"
        receipt.write_text(json.dumps({"schema": SESSION_SCHEMA, "session_id": session["session_id"],
                                       "session_path": str(source.relative_to(root)), "candidates": candidates,
                                       "generated_utc": datetime.now(timezone.utc).isoformat()},
                                      indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"session_path": str(source), "receipt_path": str(receipt), "candidate_count": 3,
                "ranked": [candidate["label"] for candidate in ranked],
                "rejected": [candidate["label"] for candidate in candidates if candidate["status"] == "rejected"],
                "incomplete": [candidate["label"] for candidate in candidates if candidate["status"] == "incomplete"],
                "candidates": candidates}
    except Exception:
        for candidate in list(getattr(document, "Objects", [])):
            try:
                name = candidate.Name
            except ReferenceError:
                continue
            if name not in initial_names:
                try:
                    document.removeObject(candidate.Name)
                except Exception:
                    pass
        document.recompute()
        if original_file is not None and original_document_bytes is not None:
            original_file.write_bytes(original_document_bytes)
        source.write_bytes(original_bytes)
        raise
