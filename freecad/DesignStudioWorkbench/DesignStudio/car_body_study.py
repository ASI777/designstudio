"""Fail-closed 2026 Porsche 911 Turbo S exterior surfacing contracts.

The module is deliberately FreeCAD-independent.  It owns evidence, candidate,
revision and local-refit gates; :mod:`car_body_reconstruction` owns the actual
FreeCAD B-spline objects.  Generated meshes are evidence, never CAD authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any


STUDY_SCHEMA = "design-studio.car-body-study/1"
AUDIT_SCHEMA = "design-studio.car-body-reference-audit/1"
CANDIDATE_SCHEMA = "design-studio.car-body-candidate/1"
BASELINE_SCHEMA = "design-studio.car-body-baseline/1"
EXPERIMENT_SCHEMA = "design-studio.car-body-experiment/1"

VIEWPOINTS = ("front", "rear", "left", "right", "top", "bottom")
REGION_LABELS = (
    "lock/preserve", "reshape", "opening", "seam", "separate part",
    "detail/decal", "ignore",
)
DETAIL_ROLES = {
    "lights", "intakes", "windows", "mirrors", "spoiler", "badges",
    "wheel_arches",
}
THREE_QUARTER_ROLES = {
    "front_left_three_quarter", "front_right_three_quarter",
    "rear_left_three_quarter", "rear_right_three_quarter",
}
LOW_RES_PROVENANCE_NAMES = (
    "frontcarview.jpeg", "frontsidepov.jpeg", "rearcarview.jpeg",
    "sideview.jpeg", "rearsidepov.jpeg", "rearbackpov.jpeg",
)

# 4,551 mm is the authoritative scaling axis.  Values are rounded to 0.01 mm
# after one consistent scale, including the separate body-width datum.
DATUMS_MM = {
    "overall_length": 200.00,
    "body_width_excluding_mirrors": 83.50,
    "overall_width_including_mirrors": 89.34,
    "overall_height": 57.35,
    "wheelbase": 107.67,
    "front_track": 69.57,
    "rear_track": 69.79,
}
SOURCE_DATUMS_MM = {
    "overall_length": 4551.0,
    "body_width_excluding_mirrors": 1900.0,
    "overall_width_including_mirrors": 2033.0,
    "overall_height": 1305.0,
    "wheelbase": 2450.0,
    "front_track": 1583.0,
    "rear_track": 1588.0,
}
TARGET_BOUNDS_MM = (200.00, 89.34, 57.35)

FIDELITY_LIMITS = {
    "silhouette_iou_min": 0.95,
    "landmark_error_mm_max": 0.5,
    "bounding_box_error_mm_max": 0.1,
    "cad_deviation_median_mm_max": 0.5,
    "cad_deviation_p95_mm_max": 0.75,
    "cad_deviation_max_mm_max": 1.5,
}

INITIAL_VARIANTS = {
    "lower-roofline": {"roofline_height_delta_mm": -2.0},
    "wider-rear-fenders": {"rear_fender_volume_delta_mm": 2.5},
    "spoiler-intake-emphasis": {
        "spoiler_emphasis": 0.25, "intake_scale": 1.15,
    },
}


class CarBodyStudyError(ValueError):
    """Untrusted, stale, incomplete or non-local study data."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_file(path: str | Path) -> str:
    source = Path(path)
    if not source.is_file():
        raise CarBodyStudyError(f"asset is missing: {source}")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _safe_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise CarBodyStudyError("asset path must be a non-empty relative path")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise CarBodyStudyError("asset path escapes the study root")
    candidate = (root / Path(*pure.parts)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise CarBodyStudyError("asset path escapes the study root")
    return candidate


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise CarBodyStudyError(f"{label} must be finite")
    result = float(value)
    if result < minimum:
        raise CarBodyStudyError(f"{label} must be at least {minimum:g}")
    return result


def validate_datums(datums: Any) -> dict[str, float]:
    if not isinstance(datums, dict) or set(datums) != set(DATUMS_MM):
        raise CarBodyStudyError("study datums must contain the complete Porsche scale set")
    normalized = {}
    for name, expected in DATUMS_MM.items():
        actual = _number(datums[name], f"datum {name}", minimum=0.000001)
        if abs(actual - expected) > 0.005:
            raise CarBodyStudyError(
                f"datum {name} is {actual:g} mm; expected {expected:.2f} mm"
            )
        normalized[name] = actual
    return normalized


def new_study_manifest() -> dict[str, Any]:
    """Return an intentionally blocked manifest with no fabricated evidence."""
    value = {
        "schema": STUDY_SCHEMA,
        "study_id": "porsche-911-turbo-s-9922-private-study",
        "title": "200 mm 2026 Porsche 911 Turbo S-inspired body study",
        "private_design_study": True,
        "model_year": 2026,
        "configuration": {
            "body": "911 Turbo S Coupé (992.2)",
            "paint": "Vanadium Grey Metallic",
            "wheels": "official Vanadium Grey press configuration; must be consistent",
            "mirrors": "production exterior mirrors",
            "spoiler_state": "raised active rear spoiler",
        },
        "datums_mm": dict(DATUMS_MM),
        "source_datums_mm": dict(SOURCE_DATUMS_MM),
        "sources": {
            "gallery": (
                "https://newsroom.porsche.com/en_US/model-range/911/"
                "911-turbo-s.html"
            ),
            "press_collection": "911 Turbo S Coupé, Vanadium Grey Metallic (22 images)",
            "technical_data": (
                "https://newsroom.porsche.com/dam/"
                "jcr%3Abe13fd23-11cf-4783-b509-a702aed10df3/"
                "pag-911-turbo-s-en.pdf.PDF"
            ),
        },
        "assets": [],
        "silhouette_revisions": [],
        "candidate_runs": [],
        "selected_candidate_sha256": None,
        "manual_baseline_approval": None,
        "license_status": "review_required",
        "distribution_authorized": False,
        "a_surface_scope": "visible exterior only; underbody provisional",
        "created_utc": _utc(),
    }
    value["manifest_sha256"] = digest_value(value)
    return value


def _asset_identity(asset: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(asset.get("model_year")),
        str(asset.get("configuration")),
        str(asset.get("wheels")),
        str(asset.get("mirrors")),
        str(asset.get("spoiler_state")),
    )


def audit_reference_evidence(
    manifest: Any, root: str | Path, *, verify_files: bool = True
) -> dict[str, Any]:
    """Audit reference coverage without ever upgrading review-only rights."""
    reference_errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema") != STUDY_SCHEMA:
        raise CarBodyStudyError(f"manifest schema must be {STUDY_SCHEMA}")
    validate_datums(manifest.get("datums_mm"))
    study_root = Path(root).expanduser().resolve()
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise CarBodyStudyError("assets must be an array")

    authoritative: list[dict[str, Any]] = []
    low_res_names: set[str] = set()
    verified_hashes = 0
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            reference_errors.append(f"assets[{index}] is not an object")
            continue
        relative = asset.get("path")
        try:
            path = _safe_path(study_root, relative)
        except CarBodyStudyError as exc:
            reference_errors.append(f"assets[{index}]: {exc}")
            continue
        basename = path.name.lower()
        if basename in LOW_RES_PROVENANCE_NAMES:
            low_res_names.add(basename)
        if verify_files:
            if not path.is_file():
                reference_errors.append(f"missing asset: {relative}")
                continue
            claimed = asset.get("sha256")
            actual = digest_file(path)
            if claimed != actual:
                reference_errors.append(f"digest mismatch: {relative}")
                continue
            verified_hashes += 1
        source_tier = asset.get("source_tier")
        if source_tier in {"low_resolution_provenance", "user_supplied_provenance"}:
            continue
        if source_tier == "official_context_only":
            warnings.append(f"context-only asset excluded from geometry: {relative}")
            continue
        if source_tier not in {
            "official_porsche_press", "authoritative_private_reference"
        }:
            reference_errors.append(f"unrecognized source tier: {relative}")
            continue
        if asset.get("permitted_use_status") not in {
            "review_required", "approved_for_private_study",
        }:
            reference_errors.append(f"missing permitted-use status: {relative}")
        if not asset.get("source_url") or not asset.get("copyright"):
            reference_errors.append(f"incomplete official provenance: {relative}")
        width = asset.get("width_px")
        height = asset.get("height_px")
        minimum_edge = 1600 if source_tier == "authoritative_private_reference" else 2048
        if (
            type(width) is not int or type(height) is not int
            or max(width, height) < minimum_edge
        ):
            reference_errors.append(
                f"authoritative asset below {minimum_edge} px: {relative}"
            )
        roles = asset.get("roles")
        if not isinstance(roles, list) or not roles:
            reference_errors.append(f"official asset has no review role: {relative}")
        mask = asset.get("mask")
        directly_approved = (
            isinstance(mask, dict)
            and mask.get("approval_basis") == "user_directed_cleanup_and_use"
        )
        if not isinstance(mask, dict) \
                or not (mask.get("brush_corrected") is True or directly_approved) \
                or mask.get("approved") is not True \
                or not mask.get("revision_sha256"):
            reference_errors.append(f"mask is not corrected and approved: {relative}")
        authoritative.append(asset)

    missing_low = sorted(set(LOW_RES_PROVENANCE_NAMES) - low_res_names)
    if missing_low:
        reference_errors.append(f"missing user-supplied provenance imports: {missing_low}")
    if not authoritative:
        reference_errors.append("no authoritative cleaned reference assets")

    identities = {_asset_identity(asset) for asset in authoritative}
    if len(identities) > 1:
        reference_errors.append("official assets mix model year/body/wheels/mirrors")

    roles = {
        role
        for asset in authoritative
        for role in asset.get("roles", [])
        if isinstance(role, str)
    }
    private_reference_mode = any(
        asset.get("source_tier") == "authoritative_private_reference"
        for asset in authoritative
    )
    required_cardinals = (
        ("rear", "left", "right")
        if private_reference_mode else ("front", "rear", "left", "right")
    )
    for role in required_cardinals:
        if role not in roles:
            reference_errors.append(f"missing near-orthographic role: {role}")
    if private_reference_mode and "front" not in roles:
        warnings.append(
            "front orthographic evidence will be derived from the approved "
            "bootstrap; both front three-quarter references are present"
        )
    missing_quarters = sorted(THREE_QUARTER_ROLES - roles)
    if missing_quarters:
        reference_errors.append(f"missing three-quarter roles: {missing_quarters}")
    elevated_count = sum(
        1 for asset in authoritative if "elevated_roof" in asset.get("roles", [])
    )
    if elevated_count < 2:
        reference_errors.append("fewer than two elevated roof views")
    missing_details = sorted(DETAIL_ROLES - roles)
    if missing_details:
        reference_errors.append(f"missing detail roles: {missing_details}")

    silhouettes = manifest.get("silhouette_revisions", [])
    approved_silhouettes = {
        item.get("viewpoint")
        for item in silhouettes
        if isinstance(item, dict)
        and item.get("approved") is True
        and item.get("brush_corrected") is True
        and isinstance(item.get("revision_sha256"), str)
        and len(item["revision_sha256"]) == 64
    }
    missing_silhouettes = sorted(set(VIEWPOINTS) - approved_silhouettes)
    silhouette_errors = []
    if missing_silhouettes:
        silhouette_errors.append(
            f"missing approved cardinal silhouettes: {missing_silhouettes}"
        )
    if "top" not in approved_silhouettes or "bottom" not in approved_silhouettes:
        warnings.append("top and bottom remain provisional until explicitly approved")
    if manifest.get("license_status") == "review_required":
        warnings.append("press assets and generated outputs remain distribution-ineligible")
    if manifest.get("distribution_authorized") is not False:
        reference_errors.append(
            "private study must not authorize distribution at this gate"
        )

    errors = reference_errors + silhouette_errors
    bootstrap_allowed = not reference_errors
    omni_allowed = bootstrap_allowed and not silhouette_errors
    material = {
        "schema": AUDIT_SCHEMA,
        "study_id": manifest.get("study_id"),
        "status": "pass" if not errors else "blocked",
        "errors": errors,
        "warnings": warnings,
        "official_asset_count": len(authoritative),
        "authoritative_asset_count": len(authoritative),
        "verified_asset_hashes": verified_hashes,
        "approved_silhouettes": sorted(approved_silhouettes),
        "coverage_roles": sorted(roles),
        "bootstrap_compute_allowed": bootstrap_allowed,
        "omni_generation_allowed": omni_allowed,
        # Compatibility alias: provisioning is allowed for the bootstrap as
        # soon as reference evidence passes. Omni still requires six views.
        "compute_provisioning_allowed": bootstrap_allowed,
        "distribution_authorized": False,
    }
    material["audit_sha256"] = digest_value(material)
    return material


def validate_region_marks(marks: Any, patch_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(marks, list):
        raise CarBodyStudyError("region marks must be an array")
    normalized = []
    seen = set()
    for index, mark in enumerate(marks):
        if not isinstance(mark, dict) or set(mark) != {
            "mark_id", "patch_id", "label", "stroke", "revision"
        }:
            raise CarBodyStudyError(f"region mark {index} fields are invalid")
        if mark["mark_id"] in seen:
            raise CarBodyStudyError("region mark IDs must be unique")
        if mark["patch_id"] not in patch_ids or mark["label"] not in REGION_LABELS:
            raise CarBodyStudyError(f"region mark {index} binding or label is invalid")
        stroke = mark["stroke"]
        if not isinstance(stroke, list) or len(stroke) < 2:
            raise CarBodyStudyError(f"region mark {index} needs a brush/lasso stroke")
        for point in stroke:
            if not isinstance(point, list) or len(point) not in (2, 3) or any(
                type(coordinate) not in (int, float)
                or not math.isfinite(float(coordinate))
                for coordinate in point
            ):
                raise CarBodyStudyError(f"region mark {index} stroke is invalid")
        if type(mark["revision"]) is not int or mark["revision"] < 1:
            raise CarBodyStudyError(f"region mark {index} revision is invalid")
        normalized.append(deepcopy(mark))
        seen.add(mark["mark_id"])
    return normalized


def candidate_diagnostics(
    candidate: Any, *, expected_revision: int,
    approved_viewpoints: tuple[str, ...] = VIEWPOINTS,
    mesh_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply all mesh fidelity gates and return measured, hashable reasons."""
    reasons: list[str] = []
    if not isinstance(candidate, dict) or candidate.get("schema") != CANDIDATE_SCHEMA:
        raise CarBodyStudyError(f"candidate schema must be {CANDIDATE_SCHEMA}")
    if candidate.get("reference_revision") != expected_revision:
        reasons.append("stale reference revision")
    if candidate.get("release_eligible") is not False:
        reasons.append("generated candidates must remain release-ineligible")
    if candidate.get("mesh_valid") is not True:
        reasons.append("mesh is invalid or empty")
    if type(candidate.get("mesh_bytes")) is not int or candidate.get("mesh_bytes", 0) <= 0:
        reasons.append("mesh byte count is empty")
    seed = candidate.get("seed")
    if type(seed) is not int or seed < 0:
        reasons.append("seed is not a non-negative integer")

    dimensions = candidate.get("dimensions_mm")
    if not isinstance(dimensions, list) or len(dimensions) != 3:
        reasons.append("candidate dimensions are incomplete")
    else:
        for actual, expected in zip(dimensions, TARGET_BOUNDS_MM):
            if type(actual) not in (int, float) or not math.isfinite(float(actual)) \
                    or abs(float(actual) - expected) > 0.1:
                reasons.append(
                    "scaled candidate bounds differ from "
                    f"{TARGET_BOUNDS_MM[0]:.2f}×{TARGET_BOUNDS_MM[1]:.2f}×"
                    f"{TARGET_BOUNDS_MM[2]:.2f} mm"
                )
                break
    body_width = candidate.get("body_width_excluding_mirrors_mm")
    # Keep malformed producer output in the diagnostics report rather than
    # leaking a TypeError from float().  Candidate records are untrusted JSON
    # at this boundary and must always fail closed with a useful reason.
    if type(body_width) not in (int, float) or not math.isfinite(float(body_width)) \
            or abs(float(body_width) - DATUMS_MM["body_width_excluding_mirrors"]) > 0.1:
        reasons.append("body-width datum was not retained separately")

    errors = candidate.get("bounding_box_error_mm")
    if not isinstance(errors, list) or len(errors) != 3 or any(
        type(value) not in (int, float)
        or not math.isfinite(float(value))
        or abs(float(value)) > FIDELITY_LIMITS["bounding_box_error_mm_max"]
        for value in errors
    ):
        reasons.append("bounding-box error exceeds 0.1 mm")

    scores = candidate.get("silhouette_iou")
    if not isinstance(scores, dict):
        reasons.append("silhouette scores are missing")
    else:
        below = [
            viewpoint for viewpoint in approved_viewpoints
            if type(scores.get(viewpoint)) not in (int, float)
            or not math.isfinite(float(scores[viewpoint]))
            or float(scores[viewpoint]) < FIDELITY_LIMITS["silhouette_iou_min"]
        ]
        if below:
            reasons.append(f"silhouette IoU below 0.95: {sorted(below)}")

    landmarks = candidate.get("landmark_error_mm")
    required_landmarks = {
        "axles", "wheel_arches", "roof_peak", "lamps", "windows",
        "intakes", "spoiler",
    }
    if not isinstance(landmarks, dict) or set(landmarks) != required_landmarks:
        reasons.append("landmark diagnostics are incomplete")
    else:
        failed = sorted(
            name for name, error in landmarks.items()
            if type(error) not in (int, float)
            or not math.isfinite(float(error))
            or float(error) > FIDELITY_LIMITS["landmark_error_mm_max"]
        )
        if failed:
            reasons.append(f"landmark error exceeds 0.5 mm: {failed}")

    claimed_sha = candidate.get("mesh_sha256")
    if not isinstance(claimed_sha, str) or len(claimed_sha) != 64:
        reasons.append("mesh SHA-256 is invalid")
    if mesh_path is not None:
        path = Path(mesh_path)
        if not path.is_file() or path.stat().st_size <= 0:
            reasons.append("mesh artifact is missing or empty")
        else:
            if path.stat().st_size != candidate.get("mesh_bytes"):
                reasons.append("mesh byte count does not match artifact")
            if digest_file(path) != claimed_sha:
                reasons.append("mesh digest does not match artifact")

    material = {
        "schema": "design-studio.car-body-candidate-diagnostics/1",
        "candidate_id": candidate.get("candidate_id"),
        "reference_revision": candidate.get("reference_revision"),
        "passed": not reasons,
        "reasons": reasons,
        "limits": dict(FIDELITY_LIMITS),
        "release_eligible": False,
    }
    material["diagnostics_sha256"] = digest_value(material)
    return material


def rank_omni_candidates(
    candidates: Any, *, expected_revision: int
) -> dict[str, Any]:
    if not isinstance(candidates, list) or len(candidates) != 4:
        raise CarBodyStudyError("exactly four Omni candidates are required")
    seeds = [candidate.get("seed") for candidate in candidates]
    if any(type(seed) is not int for seed in seeds) or len(set(seeds)) != 4:
        raise CarBodyStudyError("Omni seeds must be four distinct integers")
    if seeds != list(range(seeds[0], seeds[0] + 4)):
        raise CarBodyStudyError("Omni seeds must be sequential")
    diagnostics = [
        candidate_diagnostics(candidate, expected_revision=expected_revision)
        for candidate in candidates
    ]
    passing = [
        (candidate, report)
        for candidate, report in zip(candidates, diagnostics)
        if report["passed"]
    ]
    passing.sort(
        key=lambda item: (
            -min(item[0]["silhouette_iou"].values()),
            max(item[0]["landmark_error_mm"].values()),
            item[0]["seed"],
        )
    )
    material = {
        "schema": "design-studio.car-body-candidate-ranking/1",
        "reference_revision": expected_revision,
        "candidate_diagnostics": diagnostics,
        "passing_candidate_ids": [
            item[0]["candidate_id"] for item in passing
        ],
        "recommended_candidate_id": (
            passing[0][0]["candidate_id"] if passing else None
        ),
        "automatic_cad_handoff": False,
        "requires_explicit_user_selection": True,
        "release_eligible": False,
    }
    material["ranking_sha256"] = digest_value(material)
    return material


def validate_deviation_metrics(metrics: Any) -> dict[str, float]:
    fields = {"median_mm", "p95_mm", "maximum_mm"}
    if not isinstance(metrics, dict) or set(metrics) != fields:
        raise CarBodyStudyError("deviation metrics must contain median, p95 and maximum")
    normalized = {
        name: _number(metrics[name], f"deviation {name}") for name in fields
    }
    if not (
        normalized["median_mm"] <= FIDELITY_LIMITS["cad_deviation_median_mm_max"]
        and normalized["p95_mm"] <= FIDELITY_LIMITS["cad_deviation_p95_mm_max"]
        and normalized["maximum_mm"] <= FIDELITY_LIMITS["cad_deviation_max_mm_max"]
    ):
        raise CarBodyStudyError("mesh-to-CAD deviation exceeds the accepted limits")
    return normalized


def make_baseline_manifest(
    *, reference_audit: dict[str, Any], candidate: dict[str, Any],
    candidate_report: dict[str, Any], patch_hashes: dict[str, str],
    continuity: dict[str, str], deviation_metrics: dict[str, float],
    ap242_roundtrip_valid: bool, manual_approval: dict[str, Any],
    fit_evidence: dict[str, Any],
) -> dict[str, Any]:
    try:
        from .mesh_surface_fit import MeshSurfaceFitError, validate_fit_evidence
    except ImportError:
        from mesh_surface_fit import MeshSurfaceFitError, validate_fit_evidence

    if reference_audit.get("status") != "pass":
        raise CarBodyStudyError("reference-evidence gate has not passed")
    if not candidate_report.get("passed"):
        raise CarBodyStudyError("candidate fidelity gate has not passed")
    if manual_approval.get("approved") is not True \
            or not manual_approval.get("reviewer") \
            or not manual_approval.get("approved_utc"):
        raise CarBodyStudyError("explicit side-by-side overlay approval is required")
    if ap242_roundtrip_valid is not True:
        raise CarBodyStudyError("AP242 export/re-import gate has not passed")
    if not isinstance(patch_hashes, dict) or not patch_hashes:
        raise CarBodyStudyError("editable patch hashes are required")
    if not isinstance(continuity, dict) or any(
        grade not in {"G0", "G1", "G2"} for grade in continuity.values()
    ):
        raise CarBodyStudyError("continuity diagnostics are invalid")
    deviations = validate_deviation_metrics(deviation_metrics)
    try:
        validated_fit = validate_fit_evidence(
            fit_evidence, expected_native_sha256=candidate["mesh_sha256"]
        )
    except MeshSurfaceFitError as error:
        raise CarBodyStudyError(str(error)) from error
    if validated_fit["patch_hashes"] != patch_hashes \
            or validated_fit["continuity"] != continuity \
            or validated_fit["deviation_metrics"] != deviations:
        raise CarBodyStudyError(
            "baseline claims differ from digest-bound mesh-fit evidence")
    material = {
        "schema": BASELINE_SCHEMA,
        "revision_name": "CarBodyBaseline",
        "immutable": True,
        "reference_audit_sha256": reference_audit["audit_sha256"],
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["mesh_sha256"],
        "candidate_diagnostics_sha256": candidate_report["diagnostics_sha256"],
        "reference_mesh": {
            "sha256": candidate["mesh_sha256"],
            "hidden": True,
            "immutable": True,
        },
        "patch_hashes": dict(sorted(patch_hashes.items())),
        "continuity": dict(sorted(continuity.items())),
        "deviation_metrics": deviations,
        "mesh_fit_evidence_sha256": validated_fit["result_sha256"],
        "ap242_roundtrip_valid": True,
        "manual_overlay_approval": deepcopy(manual_approval),
        "datums_mm": dict(DATUMS_MM),
        "underbody_fidelity": "provisional",
        "created_utc": _utc(),
    }
    material["baseline_sha256"] = digest_value(material)
    return material


def make_experiment(
    baseline: dict[str, Any], *, experiment_id: str, parameters: dict[str, Any],
    changed_patches: list[str], transition_patches: list[str],
    locked_patches: list[str], output_patch_hashes: dict[str, str],
    continuity_failures: list[str], dimensions_mm: dict[str, float],
    silhouette_changes: dict[str, float],
) -> dict[str, Any]:
    """Create a branch and prove edits did not escape the transition band."""
    if baseline.get("schema") != BASELINE_SCHEMA or baseline.get("immutable") is not True:
        raise CarBodyStudyError("experiments must branch from immutable CarBodyBaseline")
    source_hashes = baseline.get("patch_hashes", {})
    if set(output_patch_hashes) != set(source_hashes):
        raise CarBodyStudyError("experiment patch set differs from the baseline")
    changed = set(changed_patches)
    transition = set(transition_patches)
    locked = set(locked_patches)
    if changed & locked or transition & locked:
        raise CarBodyStudyError("locked patches cannot be changed or used as transitions")
    allowed = changed | transition
    escaped = sorted(
        patch_id for patch_id, digest in output_patch_hashes.items()
        if digest != source_hashes[patch_id] and patch_id not in allowed
    )
    if escaped:
        raise CarBodyStudyError(f"local refit escaped its transition band: {escaped}")
    modified = {
        patch_id for patch_id, digest in output_patch_hashes.items()
        if digest != source_hashes[patch_id]
    }
    missing_changes = sorted(changed - modified)
    if missing_changes:
        raise CarBodyStudyError(f"requested reshape did not change patches: {missing_changes}")
    locked_changed = sorted(
        patch_id for patch_id in locked
        if output_patch_hashes[patch_id] != source_hashes[patch_id]
    )
    if locked_changed:
        raise CarBodyStudyError(f"locked patches changed: {locked_changed}")
    if set(silhouette_changes) != set(VIEWPOINTS):
        raise CarBodyStudyError("six-view silhouette deltas are required")
    material = {
        "schema": EXPERIMENT_SCHEMA,
        "experiment_id": experiment_id,
        "branch_of": "CarBodyBaseline",
        "baseline_sha256": baseline["baseline_sha256"],
        "parameters": deepcopy(parameters),
        "changed_patches": sorted(changed),
        "transition_patches": sorted(transition),
        "locked_patches": sorted(locked),
        "patch_hashes": dict(sorted(output_patch_hashes.items())),
        "continuity_failures": sorted(continuity_failures),
        "dimensions_mm": deepcopy(dimensions_mm),
        "silhouette_changes": deepcopy(silhouette_changes),
        "non_destructive": True,
        "created_utc": _utc(),
    }
    material["experiment_sha256"] = digest_value(material)
    return material


def baseline_filename(baseline: dict[str, Any]) -> str:
    if baseline.get("revision_name") != "CarBodyBaseline":
        raise CarBodyStudyError("baseline revision name is not immutable CarBodyBaseline")
    return f"CarBodyBaseline-{baseline['baseline_sha256'][:16]}.json"
