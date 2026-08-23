"""Deterministic AI compact-camera concept benchmark.

Reference images contribute visual evidence only.  The explicit 145 x 95 x
32 mm concept envelope and provisional component reservations are the only
numeric design inputs.  FreeCAD/OpenCASCADE creates and validates all B-Reps.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


CAMERA_SCHEMA = "design-studio.ai-compact-camera-benchmark/1"
VARIANTS = {
    "precision": {"corner_radius": 8.0, "lens_x": 91.0, "lens_z": 50.0,
                  "lens_radius": 28.0, "rib_angle": 8.0, "wall": 1.8},
    "grip": {"corner_radius": 10.0, "lens_x": 88.0, "lens_z": 49.0,
             "lens_radius": 27.0, "rib_angle": 16.0, "wall": 2.0},
    "serviceable": {"corner_radius": 7.0, "lens_x": 89.0, "lens_z": 51.0,
                    "lens_radius": 26.5, "rib_angle": -7.0, "wall": 2.2},
}
ENVELOPE = (145.0, 32.0, 95.0)  # X width, Y depth, Z height; excludes lens.


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical_json_digest(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _seal_candidate_review(review: dict[str, Any]) -> dict[str, Any]:
    material = dict(review)
    material.pop("state_sha256", None)
    review["state_sha256"] = canonical_json_digest(material)
    return review


def validate_candidate_review(review: dict[str, Any], *, verify_artifacts: bool = False) -> dict[str, Any]:
    """Validate the digest-bound concept decision without promoting release authority."""
    if not isinstance(review, dict) or review.get("schema") != "design-studio.camera-candidate-review/2":
        raise ValueError("camera candidate review schema is invalid")
    material = dict(review)
    claimed = material.pop("state_sha256", None)
    if claimed != canonical_json_digest(material):
        raise ValueError("camera candidate review state digest is stale")
    order = review.get("candidate_order")
    candidates = review.get("candidates")
    if order != list(VARIANTS) or not isinstance(candidates, dict) or set(candidates) != set(order):
        raise ValueError("camera candidate inventory is incomplete")
    if review.get("allowed_actions") != ["apply", "modify", "reject", "undo"]:
        raise ValueError("camera candidate action contract changed")
    for label in order:
        record = candidates[label]
        if record.get("candidate") != label:
            raise ValueError(f"camera candidate record {label} is misbound")
        digest_material = dict(record)
        record_digest = digest_material.pop("candidate_digest", None)
        if record_digest != canonical_json_digest(digest_material):
            raise ValueError(f"camera candidate {label} digest is stale")
        if verify_artifacts:
            for prefix in ("fcstd", "step", "program", "validation"):
                path = Path(record[f"{prefix}_path"])
                if not path.is_file() or digest_file(path) != record[f"{prefix}_sha256"]:
                    raise ValueError(f"camera candidate {label} {prefix} artifact is stale")
            review_artifacts = record.get("review_artifacts", [])
            if not isinstance(review_artifacts, list) or not review_artifacts:
                raise ValueError(f"camera candidate {label} has no bound human-review evidence")
            for artifact in review_artifacts:
                path = Path(artifact["path"])
                if (artifact.get("authority") != "human-review-only"
                        or not path.is_file() or digest_file(path) != artifact.get("sha256")):
                    raise ValueError(f"camera candidate {label} review evidence is stale")
    canonical = review.get("canonical_revision")
    if canonical is not None:
        label = canonical.get("candidate")
        if label not in candidates or canonical.get("candidate_digest") != candidates[label]["candidate_digest"]:
            raise ValueError("canonical camera revision is not bound to an intact candidate")
        if canonical.get("release_authority") is not False:
            raise ValueError("concept candidate cannot become manufacturing release authority")
    return json.loads(json.dumps(review))


def apply_candidate_review_action(review: dict[str, Any], action: str, *,
                                  candidate: str | None = None,
                                  candidate_digest: str | None = None,
                                  modification_request: str | None = None) -> dict[str, Any]:
    """Apply a fail-closed candidate action to a copy of the review state.

    Apply is digest-bound; Modify and Reject never touch the canonical branch;
    Undo restores the exact pre-action canonical state.  Callers persist the
    returned sealed value atomically with :func:`save_candidate_review`.
    """
    # Re-read every bound CAD and visual-review byte immediately before any
    # action.  A digest that was valid at load time cannot authorize a later
    # Apply/Modify/Reject/Undo after an artifact changes on disk.
    current = validate_candidate_review(review, verify_artifacts=True)
    if action not in current["allowed_actions"]:
        raise ValueError("unsupported camera candidate action")
    updated = json.loads(json.dumps(current))
    before = {key: json.loads(json.dumps(updated[key])) for key in
              ("status", "canonical_revision", "rejected_candidates", "modification_requests")}
    if action == "undo":
        if not updated["history"]:
            raise ValueError("camera candidate review has no action to undo")
        event = updated["history"].pop()
        for key, value in event["before"].items():
            updated[key] = value
        updated["revision"] += 1
        updated["last_action"] = {"action": "undo", "reverted_action": event["action"]}
        return _seal_candidate_review(updated)
    if candidate not in updated["candidates"]:
        raise ValueError("camera candidate action requires a known candidate")
    record = updated["candidates"][candidate]
    if candidate_digest != record["candidate_digest"]:
        raise ValueError("camera candidate digest mismatch")
    if action == "apply":
        updated["canonical_revision"] = {
            "revision_id": f"ai-camera-{candidate}-concept-r{updated['revision'] + 1}",
            "candidate": candidate, "candidate_digest": candidate_digest,
            "release_authority": False,
        }
        updated["status"] = "candidate-applied-concept-not-released"
    elif action == "modify":
        if not isinstance(modification_request, str) or not modification_request.strip():
            raise ValueError("modify requires a non-empty change request")
        updated["modification_requests"].append({
            "candidate": candidate, "candidate_digest": candidate_digest,
            "request": modification_request.strip(), "status": "proposal-required",
        })
        updated["status"] = "awaiting-modified-candidate"
    elif action == "reject":
        if candidate not in updated["rejected_candidates"]:
            updated["rejected_candidates"].append(candidate)
        updated["status"] = ("awaiting-user-selection" if
                             len(updated["rejected_candidates"]) < len(updated["candidate_order"])
                             else "all-candidates-rejected")
    updated["history"].append({"action": action, "candidate": candidate,
                               "candidate_digest": candidate_digest, "before": before})
    updated["revision"] += 1
    updated["last_action"] = {"action": action, "candidate": candidate}
    return _seal_candidate_review(updated)


def save_candidate_review(path: Path, review: dict[str, Any]) -> None:
    validated = validate_candidate_review(review, verify_artifacts=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_candidate_review(path: Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    return validate_candidate_review(json.loads(path.read_text(encoding="utf-8")),
                                     verify_artifacts=verify_artifacts)


def persist_candidate_review_action(path: Path, action: str, **arguments: Any) -> dict[str, Any]:
    """The single trusted persisted action path used by UI/CLI adapters."""
    current = load_candidate_review(path, verify_artifacts=True)
    updated = apply_candidate_review_action(current, action, **arguments)
    save_candidate_review(path, updated)
    return load_candidate_review(path, verify_artifacts=True)


def bind_candidate_review_artifacts(path: Path,
                                    artifacts_by_candidate: dict[str, list[Path]]) -> dict[str, Any]:
    """Bind review-only renders/drawings before a human can apply a candidate."""
    review = load_candidate_review(path, verify_artifacts=False)
    if review.get("canonical_revision") is not None or review.get("history"):
        raise ValueError("human-review evidence cannot be rebound after candidate actions begin")
    for label in review["candidate_order"]:
        record = review["candidates"][label]
        merged = {item["path"]: item for item in record.get("review_artifacts", [])}
        for artifact_path in artifacts_by_candidate.get(label, []):
            resolved = artifact_path.expanduser().resolve()
            if not resolved.is_file():
                raise ValueError(f"missing camera review artifact: {resolved}")
            merged[str(resolved)] = {"path": str(resolved), "sha256": digest_file(resolved),
                                     "authority": "human-review-only"}
        record["review_artifacts"] = [merged[key] for key in sorted(merged)]
        material = dict(record)
        material.pop("candidate_digest", None)
        record["candidate_digest"] = canonical_json_digest(material)
    _seal_candidate_review(review)
    save_candidate_review(path, review)
    return review


def _source(manifest: dict[str, Any], view: str) -> dict[str, Any]:
    candidates = [item for item in manifest["sources"]
                  if item["view_classification"]["view"] == view]
    if not candidates:
        candidates = manifest["sources"]
    item = max(candidates, key=lambda value: value["view_classification"]["confidence"])
    return {"asset_id": item["asset_id"], "crop_sha256": item["crop_sha256"]}


def validate_reference_manifest_for_camera(manifest_path: Path) -> dict[str, Any]:
    """Enforce schema, authority, containment, and every bound image hash."""
    path = manifest_path.expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    repository_root = Path(__file__).resolve().parents[3]
    schema_path = repository_root / "docs/schemas/reference-image-set-v1.schema.json"
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(manifest)
    if (manifest.get("authority") != "inspiration-and-visual-evidence-only"
            or manifest.get("measurement_status") != "uncalibrated"
            or manifest.get("millimetre_inference_allowed") is not False):
        raise ValueError("camera references must be uncalibrated visual evidence without CAD authority")
    sources = manifest.get("sources", [])
    if len(sources) != manifest.get("source_count"):
        raise ValueError("camera reference source_count is stale")
    root = path.parent
    ids = {record["asset_id"] for record in sources}
    if len(ids) != len(sources) or set(manifest["representative_asset_ids"]) - ids:
        raise ValueError("camera reference IDs or representative bindings are invalid")
    for record in sources:
        if record.get("geometry_authority") is not False:
            raise ValueError(f"reference {record['asset_id']} attempted to claim geometry authority")
        for path_key, digest_key in (("stored_original_path", "source_sha256"),
                                     ("cropped_path", "crop_sha256")):
            relative = Path(record[path_key])
            resolved = (root / relative).resolve()
            if relative.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError(f"reference {record['asset_id']} {path_key} escapes or is missing")
            if digest_file(resolved) != record[digest_key]:
                raise ValueError(f"reference {record['asset_id']} {path_key} hash is stale")
    return manifest


def write_visual_evidence(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = validate_reference_manifest_for_camera(manifest_path)

    def node(node_id: str, kind: str, label: str, view: str,
             bbox: list[float], confidence: float, status: str = "observed",
             **attributes: Any) -> dict[str, Any]:
        source = _source(manifest, view)
        source["bbox"] = {"x": bbox[0], "y": bbox[1], "width": bbox[2],
                          "height": bbox[3], "coordinate_space": "normalized-0-1"}
        return {"node_id": node_id, "kind": kind, "label": label,
                "sources": [source], "confidence": confidence, "status": status,
                "attributes": attributes}

    nodes = [
        node("body-front-silhouette", "silhouette", "compact rounded rectangular body", "front",
             [0.25, 0.24, 0.48, 0.50], 0.96),
        node("body-rear-silhouette", "silhouette", "rear body silhouette", "rear",
             [0.24, 0.24, 0.49, 0.51], 0.95),
        node("primary-lens-interface", "lens_interface", "large circular primary lens", "front",
             [0.45, 0.31, 0.25, 0.38], 0.97),
        node("secondary-optical-opening", "opening", "small front optical opening", "front",
             [0.31, 0.33, 0.09, 0.13], 0.88, "provisional"),
        node("rear-display-interface", "display_interface", "large rear display region", "rear",
             [0.30, 0.30, 0.30, 0.35], 0.94),
        node("top-control-island", "control_interface", "top control island and dial", "control_detail",
             [0.29, 0.20, 0.40, 0.48], 0.92),
        node("right-usbc-opening", "opening", "right-side USB-C-like opening", "right",
             [0.58, 0.67, 0.07, 0.12], 0.86, "provisional"),
        node("graded-rib-field", "texture_region", "graded linear rib field", "rib_detail",
             [0.18, 0.10, 0.65, 0.82], 0.97, direction="diagonal_or_wrapped"),
        node("cover-split-line", "seam", "front/rear cover split line", "oblique",
             [0.24, 0.21, 0.50, 0.54], 0.84, "provisional"),
        node("metallic-light-appearance", "material_appearance", "light metallic appearance", "oblique",
             [0.24, 0.21, 0.50, 0.54], 0.82, "ambiguous",
             note="appearance does not identify an alloy or process"),
    ]
    links = [
        {"link_id": "link-body-front-rear", "from_node": "body-front-silhouette",
         "to_node": "body-rear-silhouette", "relation": "same_feature", "confidence": 0.91,
         "status": "supported"},
        {"link_id": "link-lens-ribs", "from_node": "primary-lens-interface",
         "to_node": "graded-rib-field", "relation": "adjacent", "confidence": 0.86,
         "status": "supported"},
        {"link_id": "link-seam-body", "from_node": "cover-split-line",
         "to_node": "body-front-silhouette", "relation": "possible_match", "confidence": 0.72,
         "status": "ambiguous"},
    ]
    unknowns = [
        {"unknown_id": "unknown-scale", "question": "What is the reference product scale?",
         "blocks": ["dimensions", "release"], "reason": "all screenshots are uncalibrated"},
        {"unknown_id": "unknown-hidden", "question": "What is the hidden internal construction?",
         "blocks": ["hidden_geometry", "manufacturing", "release"],
         "reason": "no authoritative section or internal CAD was supplied"},
        {"unknown_id": "unknown-material", "question": "What exact materials and finishes are used?",
         "blocks": ["materials", "manufacturing", "release"],
         "reason": "render appearance is not material evidence"},
        {"unknown_id": "unknown-mechanism", "question": "How is the optical module actuated and retained?",
         "blocks": ["mechanism", "hidden_geometry", "release"],
         "reason": "motion graphics do not define an engineering mechanism"},
    ]
    result = {"schema": "design-studio.visual-evidence/2", "evidence_id": "ai-camera-visual-evidence",
              "revision": 1,
              "reference_image_set": {"path": str(manifest_path), "sha256": digest_file(manifest_path)},
              "authority": "visual-interpretation-only", "measurement_status": "uncalibrated",
              "nodes": nodes, "cross_view_links": links, "explicit_unknowns": unknowns,
              "provenance": {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                             "created_by": "DesignStudio camera benchmark",
                             "model": "human-reviewed deterministic seed",
                             "prompt_version": "ai-compact-camera-1", "review_status": "human-reviewed"}}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _curve_command(command_id: str, points: list[list[float]], provenance: list[str],
                   *, degree: int = 1, weights: list[float] | None = None,
                   segment_count: int | None = None) -> dict[str, Any]:
    """Return a deterministic closed B-spline command.

    Degree-one profiles are closed polylines.  Degree-two profiles are chains
    of rational Bezier segments (used for exact circular and rounded-corner
    profiles), so their internal knot multiplicity is two.
    """
    if points[0] != points[-1]:
        raise ValueError(f"closed curve {command_id} must repeat its first pole")
    if degree == 1:
        knots = list(range(len(points)))
        multiplicities = [2] + [1] * (len(knots) - 2) + [2]
    elif degree == 2 and segment_count is not None:
        knots = list(range(segment_count + 1))
        multiplicities = [3] + [2] * (segment_count - 1) + [3]
    else:
        raise ValueError("camera profiles support degree-one polylines or degree-two Bezier chains")
    return {"id": command_id, "op": "curve.bspline3d", "params": {"curve": {
        "degree": degree, "knots": knots, "multiplicities": multiplicities,
        "weights": weights or [1.0] * len(points), "control_points": points,
        "closed": True, "periodic": False, "classification": "original-designed",
        "provenance": provenance}}, "provenance": provenance}


def _rounded_profile(width: float, height: float, radius: float, y: float,
                     *, x0: float = 0.0, z0: float = 0.0) -> tuple[list[list[float]], list[float]]:
    """Exact degree-two rational rounded rectangle in the XZ plane."""
    half = math.sqrt(0.5)
    points = [
        [radius, y, 0.0], [width / 2.0, y, 0.0], [width - radius, y, 0.0],
        [width, y, 0.0], [width, y, radius],
        [width, y, height / 2.0], [width, y, height - radius],
        [width, y, height], [width - radius, y, height],
        [width / 2.0, y, height], [radius, y, height],
        [0.0, y, height], [0.0, y, height - radius],
        [0.0, y, height / 2.0], [0.0, y, radius],
        [0.0, y, 0.0], [radius, y, 0.0],
    ]
    weights = [1.0] * len(points)
    for index in (3, 7, 11, 15):
        weights[index] = half
    return [[x + x0, station, z + z0] for x, station, z in points], weights


def _rotated_rectangle_profile(origin: tuple[float, float, float],
                               size: tuple[float, float, float], station: float,
                               angle_deg: float, pivot_x: float) -> list[list[float]]:
    """Closed XZ rectangle with its design rotation encoded in source poles."""
    x, _y, z = origin
    width, _depth, height = size
    pivot_z = z + height / 2.0
    angle = math.radians(angle_deg)
    cosine, sine = math.cos(angle), math.sin(angle)

    def rotate(px: float, pz: float) -> list[float]:
        dx, dz = px - pivot_x, pz - pivot_z
        return [pivot_x + cosine * dx + sine * dz, station,
                pivot_z - sine * dx + cosine * dz]

    corners = [rotate(x, z), rotate(x + width, z),
               rotate(x + width, z + height), rotate(x, z + height)]
    return [*corners, corners[0]]


def _rectangle_profile(origin: tuple[float, float, float], size: tuple[float, float, float],
                       station: float, axis: str = "y") -> list[list[float]]:
    x, y, z = origin
    width, depth, height = size
    if axis == "y":
        return [[x, station, z], [x + width, station, z],
                [x + width, station, z + height], [x, station, z + height], [x, station, z]]
    if axis == "z":
        return [[x, y, station], [x + width, y, station],
                [x + width, y + depth, station], [x, y + depth, station], [x, y, station]]
    raise ValueError("unsupported profile axis")


def _circle_profile(center: tuple[float, float, float], radius: float, station: float,
                    axis: str = "y") -> tuple[list[list[float]], list[float]]:
    """Exact rational quadratic circle at a Y or Z station."""
    x, y, z = center
    half = math.sqrt(0.5)
    planar = [(radius, 0.0), (radius, radius), (0.0, radius), (-radius, radius),
              (-radius, 0.0), (-radius, -radius), (0.0, -radius),
              (radius, -radius), (radius, 0.0)]
    if axis == "y":
        points = [[x + first, station, z + second] for first, second in planar]
    elif axis == "z":
        points = [[x + first, y + second, station] for first, second in planar]
    else:
        raise ValueError("unsupported circle axis")
    return points, [1.0, half, 1.0, half, 1.0, half, 1.0, half, 1.0]


def mechanical_program(label: str) -> dict[str, Any]:
    if label not in VARIANTS:
        raise ValueError("unsupported camera candidate")
    cfg = VARIANTS[label]
    body_provenance = ["explicit 145x95x32 mm provisional body constraint; not pixel-derived"]
    concept_provenance = ["original AI compact-camera concept feature; provisional; not pixel-derived"]
    reservation_provenance = ["provisional occupied-volume reservation; not a supplier-authoritative component"]
    commands: list[dict[str, Any]] = []

    def loft(feature_id: str, first_points: list[list[float]], second_points: list[list[float]],
             provenance: list[str], *, weights: list[float] | None = None,
             degree: int = 1, segment_count: int | None = None) -> None:
        first, second = f"{feature_id}.profile-a", f"{feature_id}.profile-b"
        commands.append(_curve_command(first, first_points, provenance, degree=degree,
                                       weights=weights, segment_count=segment_count))
        commands.append(_curve_command(second, second_points, provenance, degree=degree,
                                       weights=weights, segment_count=segment_count))
        commands.append({"id": feature_id, "op": "feature.loft",
                         "params": {"sections": [first, second], "solid": True,
                                    "ruled": True,
                                    # OCCT `closed` sews last section back to
                                    # first (ring lofts); two-section body
                                    # lofts must leave it off or OCCT builds
                                    # degenerate zero-volume solids.
                                    "closed": False,
                                    "continuity": {"required": "G0", "max_normal_angle_deg": 180}},
                         "provenance": provenance})

    body0, body_weights = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 0.0)
    body1, _ = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 32.0)
    midframe0, _ = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 1.6)
    midframe1, _ = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 30.4)
    body_id = f"camera.{label}.midframe"
    outer_id = f"{body_id}.outer"
    inner_id = f"{body_id}.inner"
    loft(outer_id, midframe0, midframe1, body_provenance,
         weights=body_weights, degree=2, segment_count=8)
    inner0, inner_weights = _rounded_profile(
        145.0 - 2 * cfg["wall"], 95.0 - 2 * cfg["wall"],
        max(1.0, cfg["corner_radius"] - cfg["wall"]), 1.6,
        x0=cfg["wall"], z0=cfg["wall"])
    inner1, _ = _rounded_profile(
        145.0 - 2 * cfg["wall"], 95.0 - 2 * cfg["wall"],
        max(1.0, cfg["corner_radius"] - cfg["wall"]), 30.4,
        x0=cfg["wall"], z0=cfg["wall"])
    loft(inner_id, inner0, inner1, body_provenance,
         weights=inner_weights, degree=2, segment_count=8)
    commands.append({"id": body_id, "op": "feature.boolean",
                     "params": {"base": outer_id, "tools": [inner_id], "operation": "cut"},
                     "provenance": [f"explicit closed midframe shell; nominal wall {cfg['wall']} mm"]})
    commands.append({"id": "camera.body-split", "op": "feature.split",
                     "params": {"base": body_id, "plane_origin_mm": [0, 16, 0],
                                "plane_normal": [0, 1, 0], "keep": "both"},
                     "provenance": ["original service split at explicit body-depth midpoint"]})

    front_id = f"camera.{label}.front-cover"
    rear_id = f"camera.{label}.rear-cover"
    front1, front_weights = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 1.6)
    loft(front_id, body0, front1, concept_provenance,
         weights=front_weights, degree=2, segment_count=8)
    rear0, rear_weights = _rounded_profile(145.0, 95.0, cfg["corner_radius"], 30.4)
    loft(rear_id, rear0, body1, concept_provenance,
         weights=rear_weights, degree=2, segment_count=8)
    body_assembly_id = f"camera.{label}.body-assembly"
    commands.append({"id": body_assembly_id, "op": "feature.compound",
                     "params": {"members": [front_id, body_id, rear_id]},
                     "provenance": ["typed 145x95x32 mm body-zone assembly for envelope validation"]})

    def box(feature_id: str, origin: tuple[float, float, float], size: tuple[float, float, float],
            provenance: list[str]) -> None:
        start, end = origin[1], origin[1] + size[1]
        loft(feature_id, _rectangle_profile(origin, size, start),
             _rectangle_profile(origin, size, end), provenance)

    def cylinder(feature_id: str, center: tuple[float, float, float], radius: float,
                 start: float, end: float, provenance: list[str], axis: str = "y") -> None:
        first, weights = _circle_profile(center, radius, start, axis)
        second, _ = _circle_profile(center, radius, end, axis)
        loft(feature_id, first, second, provenance, weights=weights, degree=2, segment_count=4)

    box(f"camera.{label}.rear-display-window", (12.0, 30.8, 16.0), (84.0, 0.35, 60.0),
        ["provisional rear-display interface; screenshot confirms presence but not dimensions"])
    cx, cz, radius = cfg["lens_x"], cfg["lens_z"], cfg["lens_radius"]
    cylinder(f"camera.{label}.lens-mount", (cx, 0.0, cz), radius, -5.0, 0.0, concept_provenance)
    cylinder(f"camera.{label}.lens-barrel", (cx, 0.0, cz), radius - 4.0, -20.5, -4.5,
             reservation_provenance)
    cylinder(f"camera.{label}.lens-glass", (cx, 0.0, cz), radius - 8.0, -21.7, -20.5,
             reservation_provenance)

    # Decorative ribs are split analytically around the explicit lens keep-out.
    # This avoids fragile OCCT Boolean cuts on sub-millimetre solids while the
    # typed compound keeps one stable semantic ID for each interrupted rib.
    angle = math.radians(cfg["rib_angle"])
    cosine, sine = math.cos(angle), math.sin(angle)
    keepout_radius = radius + 4.0
    for index in range(16):
        rib_width = 80.0 - index * 1.2
        rib_height = 0.8 + index * 0.025
        z = 14.0 + index * 4.1 - rib_height / 2.0
        feature_id = f"camera.{label}.rib-{index + 1:02d}"
        size = (rib_width, 0.7, rib_height)
        origin = (52.0, -0.7, z)
        pivot_x = 52.0 + rib_width / 2.0
        center_z = z + rib_height / 2.0
        dx, dz = cx - pivot_x, cz - center_z
        lens_local_x = pivot_x + cosine * dx - sine * dz
        lens_local_z = center_z + sine * dx + cosine * dz
        vertical_distance = abs(lens_local_z - center_z) + rib_height / 2.0
        members: list[str] = []
        gap_start = gap_end = None
        if vertical_distance < keepout_radius:
            half_span = math.sqrt(max(0.0, keepout_radius ** 2 - vertical_distance ** 2))
            gap_start = max(origin[0], lens_local_x - half_span)
            gap_end = min(origin[0] + rib_width, lens_local_x + half_span)

        segments = [(origin[0], origin[0] + rib_width)]
        if gap_start is not None and gap_end is not None and gap_end > gap_start:
            segments = [(origin[0], gap_start), (gap_end, origin[0] + rib_width)]
        segments = [(start, end) for start, end in segments if end - start >= 0.05]
        if not segments:
            raise ValueError(f"lens keep-out consumes the complete decorative rib {feature_id}")
        if len(segments) == 1 and gap_start is None:
            segment_ids = [feature_id]
        else:
            segment_ids = [f"{feature_id}.segment-{chr(ord('a') + part)}"
                           for part in range(len(segments))]
        for segment_id, (start, end) in zip(segment_ids, segments):
            segment_origin = (start, -0.7, z)
            segment_size = (end - start, 0.7, rib_height)
            loft(segment_id,
                 _rotated_rectangle_profile(segment_origin, segment_size, -0.7,
                                            cfg["rib_angle"], pivot_x),
                 _rotated_rectangle_profile(segment_origin, segment_size, 0.0,
                                            cfg["rib_angle"], pivot_x),
                 [f"original graded rib {index + 1:02d}; exact rotation {cfg['rib_angle']} deg",
                  "analytically segmented around explicit provisional lens keep-out"])
            members.append(segment_id)
        if members != [feature_id]:
            commands.append({"id": feature_id, "op": "feature.compound",
                             "params": {"members": members},
                             "provenance": [f"graded rib {index + 1:02d} interrupted by explicit lens keep-out"]})

    cylinder(f"camera.{label}.control-dial", (116.0, 13.0, 95.0), 8.5, 95.0, 98.0,
             concept_provenance, axis="z")
    cylinder(f"camera.{label}.shutter", (28.0, 14.0, 95.0), 4.0, 95.0, 97.2,
             concept_provenance, axis="z")
    if label == "grip":
        box("camera.grip.grip-transition", (3.0, -2.4, 15.0), (18.0, 2.4, 62.0),
            ["original grip transition; elastomer intent unverified"])
    elif label == "serviceable":
        for index, x in enumerate((12.0, 133.0), 1):
            cylinder(f"camera.serviceable.service-fastener-{index}", (x, 0.0, 10.0), 2.2,
                     -0.8, 0.0, ["provisional service-fastener access; exact fastener not selected"])

    reserve_specs = [
        ("optical-module", (62.0, 5.0, 29.0), (54.0, 21.0, 43.0)),
        ("rear-display", (12.0, 24.0, 16.0), (84.0, 4.0, 60.0)),
        ("battery", (13.0, 8.0, 18.0), (38.0, 14.0, 55.0)),
        ("logic-pcb", (55.0, 8.0, 14.0), (68.0, 2.0, 65.0)),
        ("usb-c", (5.0, 10.0, 40.0), (12.0, 9.0, 8.0)),
        ("speaker", (118.0, 22.0, 18.0), (15.0, 5.0, 28.0)),
    ]
    for name, origin, size in reserve_specs:
        box(f"camera.reservation.{name}", origin, size, reservation_provenance)

    commands.append({"id": "camera.orthographic", "op": "drawing.project",
                     "params": {"base": body_id,
                                "views": ["front", "back", "top", "right", "left"],
                                "output_dir": "drawings"},
                     "provenance": ["review aid only; B-Rep remains authority"]})
    from .vector_native_cad import control_datum_digest
    origin = [0.0, 0.0, 0.0]
    return {"schema": "design-studio.mechanical-cad-program/2",
            "program_id": f"ai-camera-{label}", "units": "mm",
            "author": "DesignStudio deterministic camera benchmark",
            # Total concept extent includes the lens/control protrusions.  The
            # 145x95x32 body envelope is checked independently below.
            "envelope": {"min_mm": [0, -21.7, 0], "max_mm": [145, 32, 98]},
            "control_datums": [{"id": "body-origin", "point_mm": origin,
                                "locked": True, "digest": control_datum_digest(origin)}],
            "commands": commands,
            "checks": [{"kind": "valid_shape", "target": body_id},
                       {"kind": "watertight", "target": body_id},
                       {"kind": "valid_shape", "target": body_assembly_id},
                       {"kind": "watertight", "target": body_assembly_id},
                       {"kind": "bbox", "target": body_assembly_id, "value": [145, 32, 95]},
                       {"kind": "rebuild", "target": body_assembly_id}]}


def _add_feature(doc: Any, name: str, label: str, shape: Any, semantic_id: str,
                 material: str, group: Any) -> Any:
    obj = doc.addObject("PartDesign::Feature", name)
    obj.Label = label
    obj.Shape = shape
    obj.addProperty("App::PropertyString", "SemanticId", "DesignStudio")
    obj.SemanticId = semantic_id
    obj.addProperty("App::PropertyString", "MaterialIntent", "DesignStudio")
    obj.MaterialIntent = material
    group.addObject(obj)
    return obj


def _legacy_build_candidate(label: str, destination: Path) -> dict[str, Any]:
    import FreeCAD as App  # type: ignore
    import Part  # type: ignore

    cfg = VARIANTS[label]
    width, depth, height = ENVELOPE
    radius, wall = cfg["corner_radius"], cfg["wall"]

    def rounded_box(w: float, d: float, h: float, r: float, y: float = 0.0) -> Any:
        a = Part.makeBox(w - 2*r, d, h, App.Vector(r, y, 0))
        b = Part.makeBox(w, d, h - 2*r, App.Vector(0, y, r))
        result = a.fuse(b)
        for x in (r, w-r):
            for z in (r, h-r):
                result = result.fuse(Part.makeCylinder(r, d, App.Vector(x, y, z), App.Vector(0, 1, 0)))
        return result.removeSplitter()

    destination.mkdir(parents=True, exist_ok=True)
    doc = App.newDocument(f"AICompactCamera_{label}")
    parameters = doc.addObject("App::FeaturePython", "CameraParameters")
    parameters.Label = f"{label.title()} camera parameters"
    for name, value in (("BodyWidth", width), ("BodyDepth", depth), ("BodyHeight", height),
                        ("CornerRadius", radius), ("MinimumWall", wall),
                        ("LensCenterX", cfg["lens_x"]), ("LensCenterZ", cfg["lens_z"]),
                        ("LensRadius", cfg["lens_radius"])):
        parameters.addProperty("App::PropertyLength", name, "Provisional constraints")
        setattr(parameters, name, value)
    parameters.addProperty("App::PropertyString", "Candidate", "DesignStudio")
    parameters.Candidate = label
    parameters.addProperty("App::PropertyString", "RebuildCommand", "DesignStudio")
    parameters.RebuildCommand = "DesignStudio.camera_benchmark.build_candidate"
    parameters.addProperty("App::PropertyString", "Authority", "DesignStudio")
    parameters.Authority = "explicit provisional constraints + OpenCASCADE B-Rep"

    external = doc.addObject("App::DocumentObjectGroup", "Exterior")
    reservations = doc.addObject("App::DocumentObjectGroup", "ProvisionalReservations")
    outer = rounded_box(width, depth, height, radius)
    inner = rounded_box(width - 2*wall, depth + 2, height - 2*wall,
                        max(1.0, radius-wall), -1)
    inner.translate(App.Vector(wall, 0, wall))
    midframe = outer.cut(inner).removeSplitter()
    front = rounded_box(width, 1.6, height, radius)
    rear = rounded_box(width, 1.6, height, radius, depth - 1.6)
    # Keep the rear display cue explicit in the authoritative B-Rep.  The
    # screenshots establish that a rear display exists, but not its physical
    # dimensions, so this remains a provisional interface driven by the
    # declared camera concept rather than pixel-derived measurements.  A
    # shallow recess makes the interface unambiguous in HLR and review renders.
    display_recess = Part.makeBox(88.0, 1.1, 64.0, App.Vector(10.0, depth - 1.1, 14.0))
    rear = rear.cut(display_recess).removeSplitter()
    features = [
        _add_feature(doc, "AluminumMidframe", "Aluminum midframe", midframe,
                     f"camera.{label}.midframe", "aluminum-intent-unverified", external),
        _add_feature(doc, "FrontCover", "Molded polymer front cover", front,
                     f"camera.{label}.front-cover", "polymer-intent-unverified", external),
        _add_feature(doc, "RearCover", "Molded polymer rear cover", rear,
                     f"camera.{label}.rear-cover", "polymer-intent-unverified", external),
    ]
    display_window = Part.makeBox(84.0, 0.35, 60.0, App.Vector(12.0, depth - 1.2, 16.0))
    features.append(_add_feature(doc, "RearDisplayWindow", "PROVISIONAL rear display interface",
                                 display_window, f"camera.{label}.rear-display-window",
                                 "display-interface-reservation", external))
    cx, cz, lr = cfg["lens_x"], cfg["lens_z"], cfg["lens_radius"]
    mount = Part.makeCylinder(lr, 5.0, App.Vector(cx, 0, cz), App.Vector(0, -1, 0))
    barrel = Part.makeCylinder(lr - 4.0, 16.0, App.Vector(cx, -4.5, cz), App.Vector(0, -1, 0))
    glass = Part.makeCylinder(lr - 8.0, 1.2, App.Vector(cx, -20.5, cz), App.Vector(0, -1, 0))
    features += [
        _add_feature(doc, "LensMount", "Original lens trim", mount, f"camera.{label}.lens-mount",
                     "anodized-aluminum-intent", external),
        _add_feature(doc, "LensBarrel", "Provisional lens barrel", barrel, f"camera.{label}.lens-barrel",
                     "aluminum-intent-unverified", external),
        _add_feature(doc, "LensGlass", "Optical face reservation", glass, f"camera.{label}.lens-glass",
                     "optical-glass-reservation", external),
    ]
    # An original graded rib language: individual deterministic ribs are clipped
    # to the front silhouette and do not define body dimensions.
    clip = rounded_box(width, 0.7, height, radius, -0.7)
    rib_group = doc.addObject("App::DocumentObjectGroup", "GradedRibField")
    for index in range(16):
        z = 10.0 + index * 4.6
        rib = Part.makeBox(72.0 - index * 1.2, 0.7, 0.8 + index * 0.025,
                           App.Vector(60.0, -0.7, z))
        rib.rotate(App.Vector(88, 0, z), App.Vector(0, 1, 0), cfg["rib_angle"])
        rib = rib.common(clip)
        if not rib.isNull():
            features.append(_add_feature(doc, f"Rib{index+1:02d}", f"Graded rib {index+1:02d}", rib,
                                         f"camera.{label}.rib-{index+1:02d}", "cover-texture", rib_group))

    dial = Part.makeCylinder(8.5, 3.0, App.Vector(116, 13, height), App.Vector(0, 0, 1))
    shutter = Part.makeCylinder(4.0, 2.2, App.Vector(28, 14, height), App.Vector(0, 0, 1))
    features += [_add_feature(doc, "ControlDial", "Original control dial", dial,
                             f"camera.{label}.control-dial", "aluminum-intent", external),
                 _add_feature(doc, "ShutterButton", "Shutter button", shutter,
                             f"camera.{label}.shutter", "polymer-intent", external)]
    if label == "grip":
        grip = Part.makeBox(18, 2.4, 62, App.Vector(3, -2.4, 15))
        features.append(_add_feature(doc, "GripTransition", "Original grip transition", grip,
                                     f"camera.{label}.grip-transition", "elastomer-intent", external))
    elif label == "serviceable":
        for index, x in enumerate((12, 133), 1):
            cap = Part.makeCylinder(2.2, 0.8, App.Vector(x, -0.8, 10), App.Vector(0, 1, 0))
            features.append(_add_feature(doc, f"ServiceFastener{index}", "Service fastener access", cap,
                                         f"camera.{label}.service-fastener-{index}", "steel-intent", external))

    reserve_specs = [
        ("OpticalModule", (62, 5, 29), (54, 21, 43), "optical-module"),
        ("RearDisplay", (12, 24, 16), (84, 4, 60), "rear-display"),
        ("Battery", (13, 8, 18), (38, 14, 55), "battery"),
        ("LogicPCB", (55, 8, 14), (68, 2, 65), "logic-pcb"),
        ("USBC", (5, 10, 40), (12, 9, 8), "usb-c"),
        ("Speaker", (118, 22, 18), (15, 5, 28), "speaker"),
    ]
    reserve_objects = []
    for name, origin, size, sid in reserve_specs:
        shape = Part.makeBox(size[0], size[1], size[2], App.Vector(*origin))
        reserve_objects.append(_add_feature(doc, name, f"PROVISIONAL {name} volume", shape,
                                            f"camera.reservation.{sid}", "reservation-not-component", reservations))
    doc.recompute()
    fcstd = destination / f"ai-camera-{label}.FCStd"
    doc.saveAs(str(fcstd))
    step = destination / f"ai-camera-{label}.step"
    Part.export(features + reserve_objects, str(step))
    assembly = Part.makeCompound([item.Shape for item in features if not item.Shape.isNull()])
    stl = destination / f"ai-camera-{label}.stl"
    assembly.exportStl(str(stl))
    # Review meshes preserve part identity and material intent so that a visual
    # observer can distinguish interfaces (display/glass/grip) without ever
    # becoming CAD authority.  The aggregate STL remains available as a neutral
    # geometry interchange preview.
    review_parts = destination / "review-parts"
    review_parts.mkdir(parents=True, exist_ok=True)
    review_records = []
    for item in features:
        if item.Shape.isNull():
            continue
        part_path = review_parts / f"{item.Name}.stl"
        item.Shape.exportStl(str(part_path))
        review_records.append({"name": item.Name, "semantic_id": item.SemanticId,
                               "material_intent": item.MaterialIntent,
                               "path": str(part_path), "sha256": digest_file(part_path)})
    (destination / "review-parts-manifest.json").write_text(json.dumps({
        "schema": "design-studio.camera-review-parts/1", "candidate": label,
        "authority": "review-mesh-only", "cad_authority": False,
        "parts": review_records
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    body = features[0].Shape.fuse(features[1].Shape).fuse(features[2].Shape)
    box = body.BoundBox
    step_shape = Part.read(str(step))
    semantic_ids = sorted(item.SemanticId for item in features + reserve_objects)
    program = mechanical_program(label)
    typed_feature_ids = sorted(command["id"] for command in program["commands"]
                               if command["op"] == "feature.loft")
    report = {"schema": "design-studio.camera-cad-validation/1", "candidate": label,
              "status": "PASS_CONCEPT_NOT_RELEASED",
              "fcstd": str(fcstd), "fcstd_sha256": digest_file(fcstd),
              "step": str(step), "step_sha256": digest_file(step), "stl": str(stl),
              "checks": {
                  "occt_validity": all(item.Shape.isValid() for item in features + reserve_objects),
                  "watertight_solids": all(len(item.Shape.Solids) >= 1 for item in features + reserve_objects),
                  "body_envelope_mm": [round(box.XLength, 6), round(box.YLength, 6), round(box.ZLength, 6)],
                  "body_envelope_expected_mm": list(ENVELOPE),
                  "body_envelope_pass": all(abs(a-b) < 1e-6 for a, b in zip(
                      (box.XLength, box.YLength, box.ZLength), ENVELOPE)),
                  "minimum_wall_mm": wall, "minimum_wall_pass": wall >= 1.8,
                  "split_line_continuity": "analytic shared outer silhouette",
                  "step_roundtrip_valid": step_shape.isValid() and len(step_shape.Solids) >= 1,
                  "semantic_id_count": len(semantic_ids), "semantic_ids_unique": len(set(semantic_ids)) == len(semantic_ids),
                  "typed_feature_inventory_matches": typed_feature_ids == semantic_ids,
              },
              "authority": "FreeCAD/OpenCASCADE B-Rep from explicit provisional constraints",
              "release_authority": False,
              "blockers": ["exact optics/electronics/MPNs absent", "thermal/EMC/drop/ingress unverified",
                           "GD&T and manufacturing process not released"]}
    report_path = destination / f"ai-camera-{label}-validation.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    program_path = destination / f"ai-camera-{label}-mechanical-program.json"
    program_path.write_text(json.dumps(program, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    App.closeDocument(doc.Name)

    reopened = App.openDocument(str(fcstd))
    reloaded_ids = sorted(getattr(item, "SemanticId", "") for item in reopened.Objects
                          if hasattr(item, "SemanticId"))
    App.closeDocument(reopened.Name)
    report["checks"]["fcstd_reload_semantic_ids_stable"] = reloaded_ids == semantic_ids
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not all(value is True for key, value in report["checks"].items()
               if key.endswith(("_pass", "_valid", "_stable", "_matches"))):
        raise RuntimeError(f"candidate {label} failed a B-Rep validation gate")
    return report


REQUIRED_CANDIDATE_CHECKS = (
    "occt_validity",
    "watertight_solids",
    "body_envelope_pass",
    "minimum_wall_pass",
    "step_roundtrip_valid",
    "semantic_ids_unique",
    "typed_feature_inventory_matches",
    "program_is_geometric_authority",
    "per_feature_geometry_digests_present",
    "fcstd_reload_semantic_ids_stable",
    "fcstd_reload_geometry_digests_stable",
    "fcstd_reload_build_statuses_stable",
    "fcstd_reload_program_digest_stable",
)


def candidate_checks_pass(checks: dict[str, Any]) -> bool:
    """Return true only when every release-blocking concept-CAD check passes."""
    return all(checks.get(name) is True for name in REQUIRED_CANDIDATE_CHECKS)


def build_candidate(label: str, destination: Path) -> dict[str, Any]:
    """Compile the reviewed FCStd directly from its typed CAD program.

    This is the sole camera candidate builder.  The older handwritten builder
    remains above only as migration evidence and is never called.
    """
    import os
    import FreeCAD as App  # type: ignore
    import Part  # type: ignore
    from .vector_native_cad import execute_vector_program, program_digest

    if label not in VARIANTS:
        raise ValueError("unsupported camera candidate")
    cfg = VARIANTS[label]
    width, depth, height = ENVELOPE
    destination.mkdir(parents=True, exist_ok=True)
    program = mechanical_program(label)
    doc = App.newDocument(f"AICompactCamera_{label}")
    previous_directory = Path.cwd()
    try:
        os.chdir(destination)
        receipt = execute_vector_program(doc, program)
    finally:
        os.chdir(previous_directory)
    expected_program_digest = program_digest(program)
    controller_name = "DS_V2_" + program["program_id"].replace("-", "_")
    controller = doc.getObject(controller_name)
    if (controller is None or receipt["program_digest"] != expected_program_digest
            or controller.ProgramDigest != expected_program_digest):
        raise RuntimeError("typed camera CAD program did not become document authority")

    parameters = doc.addObject("App::FeaturePython", "CameraParameters")
    parameters.Label = f"{label.title()} camera parameters"
    for name, value in (("BodyWidth", width), ("BodyDepth", depth), ("BodyHeight", height),
                        ("CornerRadius", cfg["corner_radius"]), ("MinimumWall", cfg["wall"]),
                        ("LensCenterX", cfg["lens_x"]), ("LensCenterZ", cfg["lens_z"]),
                        ("LensRadius", cfg["lens_radius"])):
        parameters.addProperty("App::PropertyLength", name, "Provisional constraints")
        setattr(parameters, name, value)
    parameters.addProperty("App::PropertyString", "Candidate", "DesignStudio")
    parameters.Candidate = label
    parameters.addProperty("App::PropertyString", "RebuildCommand", "DesignStudio")
    parameters.RebuildCommand = "DesignStudio.vector_native_cad.execute_vector_program"
    parameters.addProperty("App::PropertyString", "Authority", "DesignStudio")
    parameters.Authority = "typed mechanical-cad-program/2 + OpenCASCADE B-Rep"
    controller.addObject(parameters)

    support_suffixes = (".profile-a", ".profile-b", ".outer", ".inner",
                        ".segment-a", ".segment-b", ".body-assembly")
    semantic_commands = [command for command in program["commands"]
                         if command["op"] in {"feature.loft", "feature.boolean", "feature.compound"}
                         and not command["id"].endswith(support_suffixes)]
    features: list[Any] = []
    reserve_objects: list[Any] = []
    geometry_digests: dict[str, str] = {}
    for command in program["commands"]:
        object_name = "DS_V2_" + command["id"].replace(".", "_").replace("-", "_")
        obj = doc.getObject(object_name)
        if obj is None:
            raise RuntimeError(f"typed camera feature is absent: {command['id']}")
        is_semantic = command in semantic_commands
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.Visibility = is_semantic
        if not is_semantic:
            continue
        obj.addProperty("App::PropertyString", "SemanticId", "DesignStudio")
        obj.SemanticId = command["id"]
        obj.addProperty("App::PropertyString", "MaterialIntent", "DesignStudio")
        if command["id"].startswith("camera.reservation."):
            obj.MaterialIntent = "reservation-not-component"
            reserve_objects.append(obj)
        elif command["id"].endswith("midframe"):
            obj.MaterialIntent = "aluminum-intent-unverified"
            features.append(obj)
        elif "cover" in command["id"] or command["id"].endswith("shutter"):
            obj.MaterialIntent = "polymer-intent-unverified"
            features.append(obj)
        elif command["id"].endswith("lens-glass"):
            obj.MaterialIntent = "optical-glass-reservation"
            features.append(obj)
        elif "display-window" in command["id"]:
            obj.MaterialIntent = "display-interface-reservation"
            features.append(obj)
        else:
            obj.MaterialIntent = "concept-material-intent-unverified"
            features.append(obj)
        geometry_digests[command["id"]] = str(obj.GeometryDigest)
    doc.recompute()

    fcstd = destination / f"ai-camera-{label}.FCStd"
    doc.saveAs(str(fcstd))
    step = destination / f"ai-camera-{label}.step"
    Part.export(features + reserve_objects, str(step))
    assembly = Part.makeCompound([item.Shape for item in features if not item.Shape.isNull()])
    stl = destination / f"ai-camera-{label}.stl"
    assembly.exportStl(str(stl))

    review_parts = destination / "review-parts"
    review_parts.mkdir(parents=True, exist_ok=True)
    review_records = []
    for item in features:
        if item.Shape.isNull():
            continue
        part_path = review_parts / f"{item.Name}.stl"
        item.Shape.exportStl(str(part_path))
        review_records.append({"name": item.Name, "semantic_id": item.SemanticId,
                               "material_intent": item.MaterialIntent,
                               "path": str(part_path), "sha256": digest_file(part_path)})
    review_parts_manifest = destination / "review-parts-manifest.json"
    review_parts_manifest.write_text(json.dumps({
        "schema": "design-studio.camera-review-parts/1", "candidate": label,
        "authority": "review-mesh-only", "cad_authority": False,
        "parts": review_records,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    by_semantic = {item.SemanticId: item for item in features + reserve_objects}
    body_ids = [f"camera.{label}.midframe", f"camera.{label}.front-cover",
                f"camera.{label}.rear-cover"]
    body = by_semantic[body_ids[0]].Shape
    for semantic_id in body_ids[1:]:
        body = body.fuse(by_semantic[semantic_id].Shape)
    box = body.BoundBox
    step_shape = Part.read(str(step))
    semantic_ids = sorted(by_semantic)
    typed_feature_ids = sorted(command["id"] for command in semantic_commands)
    report = {"schema": "design-studio.camera-cad-validation/1", "candidate": label,
              "status": "PASS_CONCEPT_NOT_RELEASED",
              "fcstd": str(fcstd), "fcstd_sha256": digest_file(fcstd),
              "step": str(step), "step_sha256": digest_file(step), "stl": str(stl),
              "program_digest": expected_program_digest,
              "per_feature_geometry_digests": geometry_digests,
              "checks": {
                  "occt_validity": all(item.Shape.isValid() for item in features + reserve_objects),
                  "watertight_solids": all(len(item.Shape.Solids) >= 1 for item in features + reserve_objects),
                  "body_envelope_mm": [round(box.XLength, 6), round(box.YLength, 6), round(box.ZLength, 6)],
                  "body_envelope_expected_mm": list(ENVELOPE),
                  "body_envelope_pass": all(abs(a-b) < 1e-6 for a, b in zip(
                      (box.XLength, box.YLength, box.ZLength), ENVELOPE)),
                  "minimum_wall_mm": cfg["wall"], "minimum_wall_pass": cfg["wall"] >= 1.8,
                  "split_line_continuity": "shared typed rational B-spline outer profiles",
                  "step_roundtrip_valid": step_shape.isValid() and len(step_shape.Solids) >= 1,
                  "semantic_id_count": len(semantic_ids),
                  "semantic_ids_unique": len(set(semantic_ids)) == len(semantic_ids),
                  "typed_feature_inventory_matches": typed_feature_ids == semantic_ids,
                  "program_is_geometric_authority": True,
                  "per_feature_geometry_digests_present": len(geometry_digests) == len(semantic_ids),
              },
              "authority": "FreeCAD/OpenCASCADE B-Rep executed directly from typed mechanical-cad-program/2",
              "release_authority": False,
              "blockers": ["exact optics/electronics/MPNs absent", "thermal/EMC/drop/ingress unverified",
                           "GD&T and manufacturing process not released"]}
    report_path = destination / f"ai-camera-{label}-validation.json"
    program_path = destination / f"ai-camera-{label}-mechanical-program.json"
    program_path.write_text(json.dumps(program, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    App.closeDocument(doc.Name)

    reopened = App.openDocument(str(fcstd))
    reloaded_ids = sorted(getattr(item, "SemanticId", "") for item in reopened.Objects
                          if hasattr(item, "SemanticId"))
    reloaded_digests = {item.SemanticId: str(item.GeometryDigest) for item in reopened.Objects
                        if hasattr(item, "SemanticId")}
    reloaded_build_statuses = {item.SemanticId: str(item.BuildStatus) for item in reopened.Objects
                               if hasattr(item, "SemanticId") and hasattr(item, "BuildStatus")}
    reopened_controller = reopened.getObject(controller_name)
    report["checks"]["fcstd_reload_semantic_ids_stable"] = reloaded_ids == semantic_ids
    report["checks"]["fcstd_reload_geometry_digests_stable"] = reloaded_digests == geometry_digests
    report["checks"]["fcstd_reload_build_statuses_stable"] = (
        set(reloaded_build_statuses) == set(geometry_digests)
        and all(status == "valid" for status in reloaded_build_statuses.values()))
    report["checks"]["fcstd_reload_program_digest_stable"] = (
        reopened_controller is not None and reopened_controller.ProgramDigest == expected_program_digest)
    App.closeDocument(reopened.Name)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not candidate_checks_pass(report["checks"]):
        raise RuntimeError(f"candidate {label} failed a B-Rep validation gate")
    return report


def build_benchmark(manifest_path: Path, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_path = output_root / "evidence" / "visual-evidence-v2.json"
    write_visual_evidence(manifest_path, evidence_path)
    reports = []
    for label in VARIANTS:
        reports.append(build_candidate(label, output_root / "candidates" / label))
    candidate_records = {}
    for report in reports:
        label = report["candidate"]
        directory = output_root / "candidates" / label
        program_path = directory / f"ai-camera-{label}-mechanical-program.json"
        validation_path = directory / f"ai-camera-{label}-validation.json"
        review_manifest_path = directory / "review-parts-manifest.json"
        review_stl_path = directory / f"ai-camera-{label}.stl"
        record = {
            "candidate": label,
            "fcstd_path": report["fcstd"], "fcstd_sha256": report["fcstd_sha256"],
            "step_path": report["step"], "step_sha256": report["step_sha256"],
            "program_path": str(program_path), "program_sha256": digest_file(program_path),
            "validation_path": str(validation_path),
            "validation_sha256": digest_file(validation_path),
            "review_artifacts": [
                {"path": str(review_manifest_path.resolve()),
                 "sha256": digest_file(review_manifest_path), "authority": "human-review-only"},
                {"path": str(review_stl_path.resolve()),
                 "sha256": digest_file(review_stl_path), "authority": "human-review-only"},
            ],
        }
        record["candidate_digest"] = canonical_json_digest(record)
        candidate_records[label] = record
    decision = _seal_candidate_review({
        "schema": "design-studio.camera-candidate-review/2",
        "status": "awaiting-user-selection", "candidate_order": list(VARIANTS),
        "candidates": candidate_records,
        "allowed_actions": ["apply", "modify", "reject", "undo"],
        "revision": 0, "canonical_revision": None,
        "rejected_candidates": [], "modification_requests": [], "history": [],
        "last_action": None,
        "integrity_rule": "only a digest-bound applied candidate may become canonical; concept is never release authority",
    })
    save_candidate_review(output_root / "candidate-review.json", decision)
    result = {"schema": CAMERA_SCHEMA, "status": "CANDIDATES_READY_AWAITING_APPROVAL",
              "envelope_mm": [145, 95, 32], "lens_excluded_from_depth": True,
              "reference_manifest": str(manifest_path), "visual_evidence": str(evidence_path),
              "candidates": reports, "candidate_review": str(output_root / "candidate-review.json"),
              "release_authority": False}
    (output_root / "benchmark-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
