"""Revision-bound reference-to-shape generation jobs.

The gateway is intentionally not a CAD authority.  It produces immutable mesh
and orthographic preview artifacts, scores them against approved evidence, and
returns the host checks FreeCAD must perform before an explicit user acceptance
can advance the active reference-form revision.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import PurePosixPath
import struct
from threading import RLock
from typing import Any
from uuid import uuid4


REFERENCE_FORM_SCHEMA = "design-studio.reference-form/1"
RESULT_SCHEMA = "design-studio.reference-form-result/1"
VIEWPOINTS = ("front", "rear", "left", "right", "top", "bottom")
TERMINAL = {"completed", "cancelled", "failed"}


class ReferenceFormJobError(ValueError):
    """Untrusted reference-form input violated the generation boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _relative_asset_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ReferenceFormJobError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ReferenceFormJobError(f"{label} must remain inside the workspace")
    if ":" in path.parts[0]:
        raise ReferenceFormJobError(f"{label} must not be a drive path")
    return str(path)


def _positive_number(value: Any, label: str, maximum: float = 10_000.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ReferenceFormJobError(f"{label} must be a finite number")
    result = float(value)
    if not 0 < result <= maximum:
        raise ReferenceFormJobError(f"{label} must be in (0, {maximum:g}]")
    return result


def validate_reference_form(value: Any, active_revision: int) -> dict[str, Any]:
    """Strictly validate the revision and digest-only asset boundary."""
    if not isinstance(value, dict):
        raise ReferenceFormJobError("reference form must be an object")
    required = {
        "schema", "reference_form_id", "revision", "document", "assets",
        "views", "target_bounds_mm", "known_dimensions_mm", "curves", "motifs",
        "engineering_controls", "seams", "candidate_selection", "provenance",
    }
    if set(value) != required:
        raise ReferenceFormJobError(
            f"reference-form fields differ; missing={sorted(required - set(value))}, "
            f"unknown={sorted(set(value) - required)}"
        )
    if value["schema"] != REFERENCE_FORM_SCHEMA:
        raise ReferenceFormJobError(f"schema must be {REFERENCE_FORM_SCHEMA}")
    if not isinstance(value["reference_form_id"], str) or not value["reference_form_id"]:
        raise ReferenceFormJobError("reference_form_id is required")
    revision = value["revision"]
    if type(revision) is not int or revision < 1:
        raise ReferenceFormJobError("revision must be a positive integer")
    if type(active_revision) is not int or active_revision != revision:
        raise ReferenceFormJobError(
            f"stale reference-form revision: input={revision}, active={active_revision}"
        )

    document = value["document"]
    if not isinstance(document, dict) or set(document) != {
        "document_id", "revision", "sha256"
    }:
        raise ReferenceFormJobError("document binding is invalid")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise ReferenceFormJobError("document_id is required")
    if type(document["revision"]) is not int or document["revision"] < 0:
        raise ReferenceFormJobError("document revision must be non-negative")
    if not _is_digest(document["sha256"]):
        raise ReferenceFormJobError("document sha256 is invalid")

    assets = value["assets"]
    if not isinstance(assets, list) or not 1 <= len(assets) <= 4096:
        raise ReferenceFormJobError("assets must contain 1-4096 digest records")
    asset_ids: set[str] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or set(asset) != {
            "asset_id", "path", "sha256", "media_type", "bytes"
        }:
            raise ReferenceFormJobError(f"assets[{index}] fields are invalid")
        asset_id = asset["asset_id"]
        if not isinstance(asset_id, str) or not asset_id or asset_id in asset_ids:
            raise ReferenceFormJobError("asset IDs must be non-empty and unique")
        _relative_asset_path(asset["path"], f"assets[{index}].path")
        if not _is_digest(asset["sha256"]):
            raise ReferenceFormJobError(f"assets[{index}].sha256 is invalid")
        if asset["media_type"] not in {
            "image/png", "image/jpeg", "image/webp", "image/svg+xml",
            "application/octet-stream", "model/gltf-binary", "model/stl",
        }:
            raise ReferenceFormJobError(f"assets[{index}].media_type is unsupported")
        if type(asset["bytes"]) is not int or not 0 < asset["bytes"] <= 512 * 1024 * 1024:
            raise ReferenceFormJobError(f"assets[{index}].bytes is invalid")
        asset_ids.add(asset_id)

    views = value["views"]
    if not isinstance(views, list) or not 1 <= len(views) <= 24:
        raise ReferenceFormJobError("views must contain 1-24 calibrated records")
    approved_views: set[str] = set()
    approved_silhouettes: set[str] = set()
    view_ids: set[str] = set()
    for index, view in enumerate(views):
        allowed = {
            "view_id", "viewpoint", "image_asset_id", "mask_asset_id",
            "silhouette_asset_id", "approved", "projection", "camera",
        }
        required_view = {
            "view_id", "viewpoint", "image_asset_id", "approved", "projection",
        }
        if not isinstance(view, dict) or not required_view.issubset(view) \
                or set(view) - allowed:
            raise ReferenceFormJobError(f"views[{index}] fields are invalid")
        if view["viewpoint"] not in VIEWPOINTS:
            raise ReferenceFormJobError(f"views[{index}].viewpoint is invalid")
        view_id = view["view_id"]
        if not isinstance(view_id, str) or not view_id or view_id in view_ids:
            raise ReferenceFormJobError("view IDs must be non-empty and unique")
        view_ids.add(view_id)
        if view["projection"] not in {"orthographic", "perspective", "unknown"}:
            raise ReferenceFormJobError(f"views[{index}].projection is invalid")
        if type(view["approved"]) is not bool:
            raise ReferenceFormJobError(f"views[{index}].approved must be boolean")
        for field in ("image_asset_id", "mask_asset_id", "silhouette_asset_id"):
            if field in view and view[field] is not None and view[field] not in asset_ids:
                raise ReferenceFormJobError(f"views[{index}].{field} references a missing asset")
        if view["approved"]:
            approved_views.add(view["viewpoint"])
            if view.get("silhouette_asset_id"):
                approved_silhouettes.add(view["viewpoint"])
    if not approved_views:
        raise ReferenceFormJobError("at least one user-approved reference view is required")

    dimensions = value["known_dimensions_mm"]
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 64:
        raise ReferenceFormJobError(
            "at least one user-measured physical dimension is required"
        )
    dimension_ids: set[str] = set()
    for index, dimension in enumerate(dimensions):
        if not isinstance(dimension, dict) or set(dimension) != {
            "dimension_id", "axis", "value_mm", "source"
        }:
            raise ReferenceFormJobError(f"known_dimensions_mm[{index}] is invalid")
        if dimension["axis"] not in {"x", "y", "z", "feature"}:
            raise ReferenceFormJobError(f"known_dimensions_mm[{index}].axis is invalid")
        _positive_number(dimension["value_mm"], f"known_dimensions_mm[{index}].value_mm")
        if dimension["source"] not in {"user_measurement", "engineering_requirement"}:
            raise ReferenceFormJobError(
                "manufacturing scale must come from a measurement or engineering requirement"
            )
        identifier = dimension["dimension_id"]
        if not isinstance(identifier, str) or not identifier or identifier in dimension_ids:
            raise ReferenceFormJobError("dimension IDs must be non-empty and unique")
        dimension_ids.add(identifier)

    bounds = value["target_bounds_mm"]
    if not isinstance(bounds, dict) or set(bounds) != {"x", "y", "z", "scale_source"}:
        raise ReferenceFormJobError("target_bounds_mm must contain x, y, z and scale_source")
    for axis in ("x", "y", "z"):
        _positive_number(bounds[axis], f"target_bounds_mm.{axis}")
    if bounds["scale_source"] not in {
        "user_measurement", "engineering_requirement", "mixed_confirmed"
    }:
        raise ReferenceFormJobError("pixel-inferred target scale is forbidden")

    curves = value["curves"]
    if not isinstance(curves, list) or len(curves) > 16_384:
        raise ReferenceFormJobError("curves must be a bounded array")
    curve_ids: set[str] = set()
    curve_labels = {
        "preserve curve", "hard edge", "soft transition", "opening",
        "motif", "candidate seam", "ignore", "silhouette", "section",
    }
    for index, curve in enumerate(curves):
        if not isinstance(curve, dict) or set(curve) != {
            "curve_id", "view_id", "label", "points", "approved"
        }:
            raise ReferenceFormJobError(f"curves[{index}] fields are invalid")
        identifier = curve["curve_id"]
        if not isinstance(identifier, str) or not identifier or identifier in curve_ids:
            raise ReferenceFormJobError("curve IDs must be non-empty and unique")
        if curve["view_id"] not in view_ids or curve["label"] not in curve_labels:
            raise ReferenceFormJobError(f"curves[{index}] binding or label is invalid")
        if type(curve["approved"]) is not bool:
            raise ReferenceFormJobError(f"curves[{index}].approved must be boolean")
        points = curve["points"]
        if not isinstance(points, list) or not 2 <= len(points) <= 65_536:
            raise ReferenceFormJobError(f"curves[{index}].points is invalid")
        for point in points:
            if not isinstance(point, list) or len(point) not in (2, 3) or any(
                type(coordinate) not in (int, float)
                or not math.isfinite(float(coordinate))
                for coordinate in point
            ):
                raise ReferenceFormJobError(f"curves[{index}].points is invalid")
        curve_ids.add(identifier)

    motifs = value["motifs"]
    if not isinstance(motifs, list) or len(motifs) > 4096:
        raise ReferenceFormJobError("motifs must be a bounded array")
    for index, motif in enumerate(motifs):
        required_motif = {
            "motif_id", "source_curve_ids", "form", "application", "intensity",
            "scale", "orientation_deg", "symmetric", "blend_falloff_mm", "approved",
        }
        if not isinstance(motif, dict) or set(motif) != required_motif:
            raise ReferenceFormJobError(f"motifs[{index}] fields are invalid")
        if not isinstance(motif["source_curve_ids"], list) or not motif["source_curve_ids"] \
                or any(identifier not in curve_ids for identifier in motif["source_curve_ids"]):
            raise ReferenceFormJobError(f"motifs[{index}] references a missing curve")
        if motif["form"] not in {
            "roofline", "fender", "intake", "spoiler", "window edge", "custom"
        } or motif["application"] not in {
            "blended surface curve", "raised feature", "debossed feature",
            "separate insert", "opening", "seam",
        }:
            raise ReferenceFormJobError(f"motifs[{index}] type is invalid")
        numeric_limits = {
            "intensity": (0.0, 1.0),
            "scale": (0.000001, 100.0),
            "orientation_deg": (-360.0, 360.0),
            "blend_falloff_mm": (0.0, 1000.0),
        }
        for field, (minimum, maximum) in numeric_limits.items():
            number = motif[field]
            if type(number) not in (int, float) or not math.isfinite(float(number)) \
                    or not minimum <= float(number) <= maximum:
                raise ReferenceFormJobError(f"motifs[{index}].{field} is invalid")
        if type(motif["symmetric"]) is not bool or type(motif["approved"]) is not bool:
            raise ReferenceFormJobError(f"motifs[{index}] approval flags are invalid")

    engineering = value["engineering_controls"]
    engineering_fields = {
        "section_curves", "symmetry_planes", "keep_points", "avoid_points",
        "protected_volumes", "functional_regions", "connector_access",
        "function_over_style",
    }
    if not isinstance(engineering, dict) or set(engineering) != engineering_fields \
            or engineering["function_over_style"] is not True:
        raise ReferenceFormJobError(
            "engineering_controls must be complete and function_over_style must be true"
        )
    if any(not isinstance(engineering[field], list)
           for field in engineering_fields - {"function_over_style"}):
        raise ReferenceFormJobError("engineering control collections must be arrays")

    seams = value["seams"]
    if not isinstance(seams, list) or len(seams) > 4096:
        raise ReferenceFormJobError("seams must be a bounded array")
    for index, seam in enumerate(seams):
        if not isinstance(seam, dict) or set(seam) != {
            "seam_id", "curve_id", "source", "approved"
        } or seam["curve_id"] not in curve_ids \
                or seam["source"] not in {"user", "ai_suggestion"} \
                or type(seam["approved"]) is not bool:
            raise ReferenceFormJobError(f"seams[{index}] is invalid")

    selection = value["candidate_selection"]
    if not isinstance(selection, dict) or not {
        "selected_candidate_sha256", "accepted"
    }.issubset(selection) or set(selection) - {
        "selected_candidate_sha256", "accepted", "accepted_utc"
    } or type(selection["accepted"]) is not bool:
        raise ReferenceFormJobError("candidate_selection is invalid")
    selected_digest = selection["selected_candidate_sha256"]
    if selected_digest is not None and not _is_digest(selected_digest):
        raise ReferenceFormJobError("selected candidate digest is invalid")

    provenance = value["provenance"]
    if not isinstance(provenance, dict) or not {
        "source", "license_status", "user_intent_text"
    }.issubset(provenance) or set(provenance) - {
        "source", "license_status", "user_intent_text", "depth_guidance"
    }:
        raise ReferenceFormJobError("provenance is invalid")
    if provenance["license_status"] not in {
        "unknown", "review_required", "approved_for_project"
    }:
        raise ReferenceFormJobError("provenance license status is invalid")

    normalized = deepcopy(value)
    normalized["_routing"] = {
        "approved_views": sorted(approved_views),
        "approved_silhouettes": sorted(approved_silhouettes),
        "backend": (
            "hunyuan3d-omni"
            if approved_silhouettes == set(VIEWPOINTS)
            else "hunyuan3d-2.1"
        ),
    }
    return normalized


def _mock_box_stl(dimensions: tuple[float, float, float], seed: int) -> bytes:
    """Deterministic, exactly bounded binary STL used by contract tests."""
    hx, hy, hz = (value / 2.0 for value in dimensions)
    vertices = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    output = io.BytesIO()
    output.write(f"DesignStudio reference-form seed {seed}".encode().ljust(80, b"\0")[:80])
    output.write(struct.pack("<I", len(faces)))
    for face in faces:
        output.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        for vertex in face:
            output.write(struct.pack("<3f", *vertices[vertex]))
        output.write(struct.pack("<H", 0))
    return output.getvalue()


def _preview_svg(viewpoint: str, dimensions: tuple[float, float, float],
                 seed: int) -> bytes:
    x, y, z = dimensions
    if viewpoint in {"front", "rear"}:
        width, height = x, z
    elif viewpoint in {"left", "right"}:
        width, height = y, z
    else:
        width, height = x, y
    label = f"{viewpoint} · seed {seed} · {width:g}×{height:g} mm"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" '
        'viewBox="0 0 640 480"><rect width="640" height="480" fill="#111827"/>'
        '<rect x="70" y="70" width="500" height="340" rx="35" '
        'fill="#60a5fa" fill-opacity=".20" stroke="#93c5fd" stroke-width="4"/>'
        f'<text x="320" y="445" text-anchor="middle" fill="#e5e7eb">{label}</text></svg>'
    ).encode("utf-8")


class ReferenceFormJobManager:
    """In-memory supervisor; production artifacts may be mirrored by a worker."""

    def __init__(self, mock: bool):
        self.mock = mock
        self._lock = RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._artifacts: dict[tuple[str, str], tuple[bytes, str]] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}

    def create(self, spec: Any, active_revision: int,
               idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 256
        ):
            raise ReferenceFormJobError(
                "idempotency_key must contain 1-256 characters"
            )
        normalized = validate_reference_form(spec, active_revision)
        routing = normalized.pop("_routing")
        input_digest = _sha256(_canonical_bytes(normalized))
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotency:
                previous_digest, job_id = self._idempotency[idempotency_key]
                if previous_digest != input_digest:
                    raise ReferenceFormJobError(
                        "idempotency key was already used with different input"
                    )
                return deepcopy(self._jobs[job_id]), False
            job_id = str(uuid4())
            base_seed = int(hashlib.sha256(input_digest.encode()).hexdigest()[:8], 16)
            seeds = [base_seed + offset for offset in range(4)]
            job = {
                "schema": "design-studio.reference-form-job/1",
                "job_id": job_id,
                "reference_form_id": normalized["reference_form_id"],
                "reference_form_revision": normalized["revision"],
                "document": normalized["document"],
                "input_sha256": input_digest,
                "backend": routing["backend"],
                "model": (
                    os.environ.get("DS_OMNI_MODEL", "Tencent-Hunyuan/Hunyuan3D-Omni")
                    if routing["backend"] == "hunyuan3d-omni"
                    else os.environ.get("DS_3D_MODEL", "tencent/Hunyuan3D-2.1")
                ),
                "status": "queued",
                "candidate_count": 4,
                "seeds": seeds,
                "created_utc": _utc_now(),
                "updated_utc": _utc_now(),
                "failure": None,
            }
            self._jobs[job_id] = job
            if idempotency_key:
                self._idempotency[idempotency_key] = (input_digest, job_id)
        if self.mock:
            self._run_mock(job_id, normalized, routing)
        return deepcopy(self._jobs[job_id]), True

    def _run_mock(self, job_id: str, spec: dict[str, Any],
                  routing: dict[str, Any]) -> None:
        with self._lock:
            if self._jobs[job_id]["status"] == "cancelled":
                return
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["updated_utc"] = _utc_now()
        dimensions = tuple(
            float(spec["target_bounds_mm"][axis]) for axis in ("x", "y", "z")
        )
        real_views = set(routing["approved_views"])
        approved_silhouettes = set(routing["approved_silhouettes"])
        candidates = []
        for index, seed in enumerate(self._jobs[job_id]["seeds"]):
            mesh_name = f"candidate-{index:02d}.stl"
            mesh = _mock_box_stl(dimensions, seed)
            self._artifacts[(job_id, mesh_name)] = (mesh, "model/stl")
            previews = []
            for viewpoint in VIEWPOINTS:
                name = f"candidate-{index:02d}-{viewpoint}.svg"
                data = _preview_svg(viewpoint, dimensions, seed)
                self._artifacts[(job_id, name)] = (data, "image/svg+xml")
                previews.append({
                    "viewpoint": viewpoint,
                    "artifact": name,
                    "artifact_url": (
                        f"/v1/reference-form-jobs/{job_id}/artifacts/{name}"
                    ),
                    "sha256": _sha256(data),
                    "provisional": viewpoint not in real_views,
                })
            silhouette_scores = {
                viewpoint: (0.995 - index * 0.001)
                for viewpoint in sorted(approved_silhouettes)
            }
            candidates.append({
                "index": index,
                "seed": seed,
                "artifact": mesh_name,
                "artifact_url": (
                    f"/v1/reference-form-jobs/{job_id}/artifacts/{mesh_name}"
                ),
                "sha256": _sha256(mesh),
                "media_type": "model/stl",
                "dimensions_mm": list(dimensions),
                "bounding_box_error_mm": [0.0, 0.0, 0.0],
                "orthographic_previews": previews,
                "scores": {
                    "silhouette_iou": silhouette_scores,
                    "landmark_error_mm": 0.0,
                    "protected_volume_collisions": 0,
                    "symmetry_error_mm": 0.0,
                    "mesh_valid": True,
                    "preserved_features": 1.0,
                    "rank_score": round(0.995 - index * 0.001, 6),
                },
                "control_adherence": {
                    "bounds_exact": True,
                    "symmetry": True,
                    "protected_volumes_clear": True,
                },
                "uncertainty": {
                    "hidden_surfaces": "high" if len(real_views) < 6 else "low",
                    "depth_preview_used_as_geometry": False,
                },
                "release_eligible": False,
            })
        result_without_digest = {
            "schema": RESULT_SCHEMA,
            "job_id": job_id,
            "reference_form_id": spec["reference_form_id"],
            "reference_form_revision": spec["revision"],
            "document": spec["document"],
            "input_sha256": self._jobs[job_id]["input_sha256"],
            "generator": {
                "backend": self._jobs[job_id]["backend"],
                "model": self._jobs[job_id]["model"],
                "model_digest": _sha256(self._jobs[job_id]["model"].encode()),
                "container_digest": os.environ.get(
                    "DS_GENERATION_CONTAINER_DIGEST",
                    _sha256(b"mock-reference-form-container"),
                ),
                "text_conditioning": "recorded_not_conditioned",
                "sequential_worker": True,
                "authoritative_cad": False,
            },
            "seeds": list(self._jobs[job_id]["seeds"]),
            "target_bounds_mm": spec["target_bounds_mm"],
            "candidates": candidates,
            "ranking": [candidate["index"] for candidate in candidates],
            "required_host_checks": [
                "explicit_candidate_acceptance",
                "all_approved_silhouette_iou_at_least_0.90",
                "final_xyz_bounds_within_0.1_mm",
                "closed_sewn_positive_volume_brep",
                "no_self_intersections",
                "wall_and_clearance_compliance",
                "protected_volume_clearance",
                "editable_parameter_recompute",
            ],
            "created_utc": _utc_now(),
        }
        result = dict(result_without_digest)
        result["result_sha256"] = _sha256(_canonical_bytes(result_without_digest))
        with self._lock:
            if self._jobs[job_id]["status"] == "cancelled":
                return
            self._results[job_id] = result
            self._jobs[job_id]["status"] = "completed"
            self._jobs[job_id]["updated_utc"] = _utc_now()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] not in TERMINAL:
                job["status"] = "cancelled"
                job["updated_utc"] = _utc_now()
            return deepcopy(job)

    def result(self, job_id: str) -> tuple[str, dict[str, Any] | None]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "missing", None
            result = self._results.get(job_id)
            return job["status"], deepcopy(result) if result else None

    def artifact(self, job_id: str, name: str) -> tuple[bytes, str] | None:
        if _relative_asset_path(name, "artifact") != name or "/" in name:
            return None
        with self._lock:
            return self._artifacts.get((job_id, name))

    def enforce_production_gate(self, job_id: str) -> None:
        """Fail visibly until the separately recorded MI300X parity gate passes.

        A real worker adapter is deliberately not enabled merely because the
        gateway runs with mock mode off. This prevents an unqualified cloud
        deployment from leaving jobs queued forever or silently accepting
        unverified ROCm output.
        """
        if self.mock:
            return
        parity = os.environ.get("DS_REFERENCE_FORM_ROCM_PARITY", "")
        worker = os.environ.get("DS_REFERENCE_FORM_WORKER_URL", "")
        if parity == "pass" and worker:
            message = (
                "ROCm parity is marked pass, but the digest-addressed worker "
                "transfer adapter is not enabled in this build"
            )
        else:
            message = (
                "cloud reference-form generation is gated: require "
                "DS_REFERENCE_FORM_ROCM_PARITY=pass and "
                "DS_REFERENCE_FORM_WORKER_URL after the real MI300X parity run"
            )
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] == "cancelled":
                return
            job["status"] = "failed"
            job["failure"] = message
            job["updated_utc"] = _utc_now()
