"""Workspace-local reference capture, revision history, and CAD handoff.

This module stays usable outside FreeCAD so path, digest, revision, visual-hull
and acceptance behavior can be contract-tested without a GUI installation.
FreeCAD remains authoritative for B-Rep construction.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from uuid import uuid4


SCHEMA = "design-studio.reference-form/1"
RESULT_SCHEMA = "design-studio.reference-form-result/1"
VIEWPOINTS = ("front", "rear", "left", "right", "top", "bottom")
CURVE_LABELS = (
    "preserve curve", "hard edge", "soft transition", "opening", "motif",
    "candidate seam", "ignore", "silhouette", "section",
)
FDM_PROTOTYPE = {
    "process": "fdm",
    "nominal_wall_mm": 2.0,
    "minimum_rib_mm": 1.2,
    "assembly_clearance_mm": 0.30,
    "nozzle_mm": 0.4,
    "surface_deviation_target_mm": 1.0,
}


class ReferenceFormError(ValueError):
    """Reference-form data is stale, unsafe, or below an acceptance gate."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReferenceFormError(f"{path.name} must contain a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical_bytes(value) + b"\n")
    temporary.replace(path)


def _finite_positive(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ReferenceFormError(f"{label} must be finite")
    number = float(value)
    if not 0 < number <= 10_000:
        raise ReferenceFormError(f"{label} must be in (0, 10000]")
    return number


def _image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ReferenceFormError("reference must be a PNG, JPEG, or WebP image")


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ReferenceFormError("asset path escapes the workspace")
    if ":" in path.parts[0]:
        raise ReferenceFormError("asset path must not be drive-qualified")
    return path


def extract_silhouette_curve(
    image_path: str | Path, view_id: str, *, maximum_points: int = 1024
) -> list[dict[str, Any]]:
    """Extract an editable, bounded 2D boundary from an approved binary mask."""
    from PIL import Image

    path = Path(image_path).expanduser().resolve()
    with Image.open(path) as image:
        mask = image.convert("L")
        width, height = mask.size
        pixels = mask.load()
        boundary: list[list[float]] = []
        for y in range(height):
            foreground = [x for x in range(width) if pixels[x, y] >= 128]
            if foreground:
                boundary.append([float(min(foreground)), float(y)])
        for y in range(height - 1, -1, -1):
            foreground = [x for x in range(width) if pixels[x, y] >= 128]
            if foreground:
                boundary.append([float(max(foreground)), float(y)])
    if len(boundary) < 4:
        raise ReferenceFormError(
            "approved silhouette must contain a visible foreground boundary"
        )
    stride = max(1, math.ceil(len(boundary) / maximum_points))
    sampled = boundary[::stride]
    if sampled[0] != sampled[-1]:
        sampled.append(sampled[0])
    digest = _digest(_canonical_bytes(sampled))
    return [{
        "curve_id": f"curve.silhouette.{digest[:16]}",
        "view_id": view_id,
        "label": "silhouette",
        "points": sampled,
        "approved": True,
    }]


def validate_submission(value: Any) -> dict[str, Any]:
    """Fail closed before a revision is sent to the generation gateway."""
    if not isinstance(value, dict):
        raise ReferenceFormError("reference form must be an object")
    required = {
        "schema", "reference_form_id", "revision", "document", "assets",
        "views", "target_bounds_mm", "known_dimensions_mm", "curves", "motifs",
        "engineering_controls", "seams", "candidate_selection", "provenance",
    }
    if set(value) != required:
        raise ReferenceFormError(
            f"reference-form fields differ; missing={sorted(required - set(value))}, "
            f"unknown={sorted(set(value) - required)}"
        )
    if value["schema"] != SCHEMA:
        raise ReferenceFormError(f"schema must be {SCHEMA}")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ReferenceFormError("revision must be positive")
    dimensions = value["known_dimensions_mm"]
    if not isinstance(dimensions, list) or not dimensions:
        raise ReferenceFormError(
            "at least one measured physical dimension is required; scale is never inferred from pixels"
        )
    for dimension in dimensions:
        if dimension.get("source") not in {
            "user_measurement", "engineering_requirement"
        }:
            raise ReferenceFormError("pixel-inferred manufacturing scale is forbidden")
        _finite_positive(dimension.get("value_mm"), "known dimension")
    bounds = value["target_bounds_mm"]
    if not isinstance(bounds, dict) or set(bounds) != {"x", "y", "z", "scale_source"}:
        raise ReferenceFormError("target bounds are incomplete")
    for axis in ("x", "y", "z"):
        _finite_positive(bounds.get(axis), f"target {axis}")
    if bounds["scale_source"] not in {
        "user_measurement", "engineering_requirement", "mixed_confirmed"
    }:
        raise ReferenceFormError("target bounds must be user confirmed")
    if not isinstance(value["views"], list) or not any(
        view.get("approved") for view in value["views"] if isinstance(view, dict)
    ):
        raise ReferenceFormError("at least one approved reference view is required")
    return deepcopy(value)


class ReferenceFormStore:
    """Digest-addressed assets and immutable accepted/preview revisions."""

    def __init__(self, workspace_root: str | Path, document: dict[str, Any]):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        if not self.workspace_root.is_dir():
            raise ReferenceFormError("workspace root does not exist")
        if set(document) != {"document_id", "revision", "sha256"}:
            raise ReferenceFormError("document binding is incomplete")
        self.document = deepcopy(document)
        self.root = self.workspace_root / "reference-form"
        self.assets_root = self.root / "assets" / "sha256"
        self.revisions_root = self.root / "revisions"
        self.previews_root = self.root / "previews"
        self.state_path = self.root / "state.json"
        for path in (self.assets_root, self.revisions_root, self.previews_root):
            path.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            _write_json(self.state_path, {
                "schema": "design-studio.reference-form-state/1",
                "active_revision": None,
                "next_revision": 1,
                "undo": [],
                "redo": [],
            })

    def _state(self) -> dict[str, Any]:
        return _read_json(self.state_path)

    def _revision_path(self, revision: int) -> Path:
        return self.revisions_root / f"revision-{revision:06d}.json"

    def active(self) -> dict[str, Any] | None:
        revision = self._state().get("active_revision")
        if revision is None:
            return None
        return _read_json(self._revision_path(int(revision)))

    def add_asset(self, source_path: str | Path, *, expected: str = "image") -> dict[str, Any]:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ReferenceFormError("asset source does not exist")
        if source.stat().st_size <= 0 or source.stat().st_size > 512 * 1024 * 1024:
            raise ReferenceFormError("asset is empty or exceeds 512 MiB")
        data = source.read_bytes()
        if expected == "image":
            media_type, suffix = _image_type(data)
        elif expected == "mesh":
            suffix = source.suffix.lower()
            media_type = {
                ".glb": "model/gltf-binary", ".stl": "model/stl"
            }.get(suffix, "application/octet-stream")
            if media_type == "application/octet-stream":
                raise ReferenceFormError("candidate mesh must be GLB or STL")
        else:
            raise ReferenceFormError("unsupported asset class")
        digest = _digest(data)
        destination = self.assets_root / digest[:2] / f"{digest}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
        elif _digest(destination.read_bytes()) != digest:
            raise ReferenceFormError("digest-addressed asset collision")
        relative = destination.relative_to(self.workspace_root).as_posix()
        return {
            "asset_id": f"asset.{expected}.{digest[:20]}",
            "path": relative,
            "sha256": digest,
            "media_type": media_type,
            "bytes": len(data),
        }

    def verify_assets(self, form: dict[str, Any] | None = None) -> None:
        form = self.active() if form is None else form
        if form is None:
            raise ReferenceFormError("no active reference form")
        for asset in form["assets"]:
            relative = _safe_relative(asset["path"])
            path = (self.workspace_root / Path(*relative.parts)).resolve()
            if not path.is_relative_to(self.workspace_root) or not path.is_file():
                raise ReferenceFormError(f"asset is missing or outside workspace: {relative}")
            data = path.read_bytes()
            if len(data) != asset["bytes"] or _digest(data) != asset["sha256"]:
                raise ReferenceFormError(f"asset digest mismatch: {relative}")

    def import_reference(
        self, source_path: str | Path, *, viewpoint: str, source: str,
        license_status: str, projection: str, known_axis: str,
        known_value_mm: float, target_bounds_mm: tuple[float, float, float],
        symmetry_planes: list[str] | None = None,
    ) -> dict[str, Any]:
        if viewpoint not in VIEWPOINTS:
            raise ReferenceFormError("viewpoint is invalid")
        if projection not in {"orthographic", "perspective", "unknown"}:
            raise ReferenceFormError("projection is invalid")
        if known_axis not in {"x", "y", "z", "feature"}:
            raise ReferenceFormError("known dimension axis is invalid")
        measured = _finite_positive(known_value_mm, "known dimension")
        bounds = tuple(
            _finite_positive(value, f"target {axis}")
            for axis, value in zip(("x", "y", "z"), target_bounds_mm)
        )
        asset = self.add_asset(source_path, expected="image")
        active = self.active()
        if active is None:
            form = {
                "schema": SCHEMA,
                "reference_form_id": str(uuid4()),
                "revision": 0,
                "document": deepcopy(self.document),
                "assets": [],
                "views": [],
                "target_bounds_mm": {
                    "x": bounds[0], "y": bounds[1], "z": bounds[2],
                    "scale_source": "user_measurement",
                },
                "known_dimensions_mm": [],
                "curves": [],
                "motifs": [],
                "engineering_controls": {
                    "section_curves": [],
                    "symmetry_planes": symmetry_planes or [],
                    "keep_points": [],
                    "avoid_points": [],
                    "protected_volumes": [],
                    "functional_regions": [],
                    "connector_access": [],
                    "function_over_style": True,
                },
                "seams": [],
                "candidate_selection": {
                    "selected_candidate_sha256": None,
                    "accepted": False,
                    "accepted_utc": None,
                },
                "provenance": {
                    "source": source,
                    "license_status": license_status,
                    "user_intent_text": "",
                },
            }
        else:
            form = deepcopy(active)
            form["target_bounds_mm"].update({
                "x": bounds[0], "y": bounds[1], "z": bounds[2],
            })
        if asset["asset_id"] not in {item["asset_id"] for item in form["assets"]}:
            form["assets"].append(asset)
        view_id = f"view.{viewpoint}.{asset['sha256'][:12]}"
        form["views"] = [
            view for view in form["views"]
            if view.get("viewpoint") != viewpoint
        ]
        form["views"].append({
            "view_id": view_id,
            "viewpoint": viewpoint,
            "image_asset_id": asset["asset_id"],
            "mask_asset_id": None,
            "silhouette_asset_id": None,
            "approved": True,
            "projection": projection,
            "camera": None,
        })
        dimension_id = f"dimension.{known_axis}.{len(form['known_dimensions_mm']) + 1}"
        form["known_dimensions_mm"].append({
            "dimension_id": dimension_id,
            "axis": known_axis,
            "value_mm": measured,
            "source": "user_measurement",
        })
        preview = self.create_preview(
            base_revision=active["revision"] if active else 0,
            operation="reference_import",
            replacement=form,
        )
        return self.accept_preview(preview["preview_id"])

    def create_preview(
        self, *, base_revision: int, operation: str,
        replacement: dict[str, Any],
    ) -> dict[str, Any]:
        active = self.active()
        actual = active["revision"] if active else 0
        if base_revision != actual:
            raise ReferenceFormError(
                f"stale preview base revision: input={base_revision}, active={actual}"
            )
        form = deepcopy(replacement)
        form["revision"] = actual
        preview_id = str(uuid4())
        preview = {
            "schema": "design-studio.reference-form-preview/1",
            "preview_id": preview_id,
            "base_revision": actual,
            "operation": operation,
            "created_utc": _utc(),
            "form": form,
        }
        _write_json(self.previews_root / f"{preview_id}.json", preview)
        return preview

    def accept_preview(self, preview_id: str) -> dict[str, Any]:
        preview_path = self.previews_root / f"{preview_id}.json"
        if not preview_path.is_file():
            raise ReferenceFormError("preview does not exist")
        preview = _read_json(preview_path)
        state = self._state()
        active_revision = state["active_revision"] or 0
        if preview["base_revision"] != active_revision:
            raise ReferenceFormError("preview is stale and cannot be accepted")
        revision = int(state["next_revision"])
        form = deepcopy(preview["form"])
        form["revision"] = revision
        validate_submission(form)
        self.verify_assets(form)
        _write_json(self._revision_path(revision), form)
        undo = list(state["undo"])
        if state["active_revision"] is not None:
            undo.append(state["active_revision"])
        state.update({
            "active_revision": revision,
            "next_revision": revision + 1,
            "undo": undo,
            "redo": [],
        })
        _write_json(self.state_path, state)
        preview_path.unlink()
        return form

    def discard_preview(self, preview_id: str) -> None:
        path = self.previews_root / f"{preview_id}.json"
        if path.exists():
            path.unlink()

    def undo(self) -> dict[str, Any]:
        state = self._state()
        if not state["undo"]:
            raise ReferenceFormError("nothing to undo")
        previous = state["undo"].pop()
        state["redo"].append(state["active_revision"])
        state["active_revision"] = previous
        _write_json(self.state_path, state)
        return self.active()

    def redo(self) -> dict[str, Any]:
        state = self._state()
        if not state["redo"]:
            raise ReferenceFormError("nothing to redo")
        following = state["redo"].pop()
        state["undo"].append(state["active_revision"])
        state["active_revision"] = following
        _write_json(self.state_path, state)
        return self.active()

    def attach_corrected_mask(
        self, view_id: str, mask_path: str | Path, silhouette_path: str | Path,
        curves: list[dict[str, Any]], *, base_revision: int,
        suggestion_model: str = "SAM 2.1",
    ) -> dict[str, Any]:
        active = self.active()
        if active is None or active["revision"] != base_revision:
            raise ReferenceFormError("mask correction targets a stale revision")
        mask = self.add_asset(mask_path, expected="image")
        silhouette = self.add_asset(silhouette_path, expected="image")
        form = deepcopy(active)
        form["assets"].extend(
            asset for asset in (mask, silhouette)
            if asset["asset_id"] not in {item["asset_id"] for item in form["assets"]}
        )
        target = next((view for view in form["views"] if view["view_id"] == view_id), None)
        if target is None:
            raise ReferenceFormError("view does not exist")
        target["mask_asset_id"] = mask["asset_id"]
        target["silhouette_asset_id"] = silhouette["asset_id"]
        target["approved"] = True
        for curve in curves:
            if curve.get("label") not in CURVE_LABELS:
                raise ReferenceFormError("curve label is invalid")
        form["curves"] = [
            curve for curve in form["curves"] if curve.get("view_id") != view_id
        ] + deepcopy(curves)
        form["provenance"]["mask_suggestion_model"] = suggestion_model
        # The schema deliberately does not allow undocumented provenance
        # members. Record the model in user intent until a dedicated provenance
        # event contract is introduced.
        form["provenance"].pop("mask_suggestion_model")
        form["provenance"]["user_intent_text"] = (
            form["provenance"].get("user_intent_text", "")
            + f"\nMask suggested by {suggestion_model}; brush-corrected and approved by user."
        ).strip()
        return self.create_preview(
            base_revision=base_revision,
            operation="sam_mask_brush_correction",
            replacement=form,
        )

    def visual_hull_control(self) -> dict[str, Any]:
        form = validate_submission(self.active())
        approved = {
            view["viewpoint"]: view["silhouette_asset_id"]
            for view in form["views"]
            if view.get("approved") and view.get("silhouette_asset_id")
        }
        missing = sorted(set(VIEWPOINTS) - set(approved))
        if missing:
            raise ReferenceFormError(
                f"six-view visual hull requires approved silhouettes: missing={missing}"
            )
        control = {
            "schema": "design-studio.visual-hull-control/1",
            "reference_form_id": form["reference_form_id"],
            "reference_form_revision": form["revision"],
            "views": {name: approved[name] for name in VIEWPOINTS},
            "target_bounds_mm": form["target_bounds_mm"],
            "symmetry_planes": form["engineering_controls"]["symmetry_planes"],
            "keep_points": form["engineering_controls"]["keep_points"],
            "avoid_points": form["engineering_controls"]["avoid_points"],
            "protected_volumes": form["engineering_controls"]["protected_volumes"],
        }
        control["sha256"] = _digest(_canonical_bytes(control))
        return control

    def candidate_acceptance_preview(
        self, result: dict[str, Any], candidate_index: int,
        mesh_path: str | Path,
    ) -> dict[str, Any]:
        active = validate_submission(self.active())
        if result.get("schema") != RESULT_SCHEMA \
                or result.get("reference_form_id") != active["reference_form_id"] \
                or result.get("reference_form_revision") != active["revision"]:
            raise ReferenceFormError("generation result has a stale revision binding")
        material = dict(result)
        claimed = material.pop("result_sha256", None)
        if claimed != _digest(_canonical_bytes(material)):
            raise ReferenceFormError("generation result digest is invalid")
        candidates = result.get("candidates", [])
        candidate = next(
            (item for item in candidates if item.get("index") == candidate_index), None
        )
        if candidate is None:
            raise ReferenceFormError("candidate does not exist")
        if not candidate.get("scores", {}).get("mesh_valid"):
            raise ReferenceFormError("candidate mesh is invalid")
        if any(float(error) > 0.1 for error in candidate["bounding_box_error_mm"]):
            raise ReferenceFormError("candidate bounds exceed the 0.1 mm tolerance")
        approved = {
            view["viewpoint"] for view in active["views"]
            if view.get("approved") and view.get("silhouette_asset_id")
        }
        scores = candidate["scores"].get("silhouette_iou", {})
        below = sorted(
            viewpoint for viewpoint in approved
            if float(scores.get(viewpoint, 0.0)) < 0.90
        )
        if below:
            raise ReferenceFormError(
                f"candidate silhouette IoU is below 0.90 for {below}"
            )
        mesh = self.add_asset(mesh_path, expected="mesh")
        if mesh["sha256"] != candidate["sha256"]:
            raise ReferenceFormError("downloaded candidate digest does not match result")
        form = deepcopy(active)
        if mesh["asset_id"] not in {item["asset_id"] for item in form["assets"]}:
            form["assets"].append(mesh)
        form["candidate_selection"] = {
            "selected_candidate_sha256": mesh["sha256"],
            "accepted": True,
            "accepted_utc": _utc(),
        }
        form["provenance"]["user_intent_text"] = (
            form["provenance"].get("user_intent_text", "")
            + f"\nExplicitly accepted generation candidate {candidate_index}."
        ).strip()
        return self.create_preview(
            base_revision=active["revision"],
            operation="candidate_selection",
            replacement=form,
        )


def workspace_from_mechanical_path(mechanical_path: str | Path) -> Path:
    path = Path(mechanical_path).expanduser().resolve()
    if path.suffix.lower() != ".fcstd" or path.parent.name != "mechanical":
        raise ReferenceFormError(
            "the active FreeCAD document must be inside a DesignStudio workspace"
        )
    root = path.parent.parent
    if not (root / "manifest.json").is_file():
        raise ReferenceFormError("workspace manifest is missing")
    return root


class ReferenceFormGatewayClient:
    """Small bounded HTTP client used by the native FreeCAD task panel."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url
            or os.environ.get("DESIGNSTUDIO_AI_GATEWAY_URL")
            or "http://127.0.0.1:8000"
        ).rstrip("/") + "/"
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ReferenceFormError("AI gateway URL is invalid")
        allow_remote = os.environ.get("DESIGNSTUDIO_AI_ALLOW_REMOTE", "0") == "1"
        if not allow_remote and parsed.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise ReferenceFormError(
                "remote AI gateway is disabled; use an SSH loopback tunnel or explicitly allow it"
            )

    def _json(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        encoded = None if body is None else _canonical_bytes(body)
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=encoded,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                data = response.read(16 * 1024 * 1024 + 1)
        except HTTPError as error:
            detail = error.read(16 * 1024).decode("utf-8", "replace")
            raise ReferenceFormError(
                f"gateway {method} {path} failed ({error.code}): {detail}"
            ) from error
        except URLError as error:
            raise ReferenceFormError(f"gateway is unavailable: {error.reason}") from error
        if len(data) > 16 * 1024 * 1024:
            raise ReferenceFormError("gateway JSON response exceeds 16 MiB")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ReferenceFormError("gateway response is not a JSON object")
        return value

    def submit(self, form: dict[str, Any]) -> dict[str, Any]:
        validate_submission(form)
        return self._json("POST", "/v1/reference-form-jobs", {
            "spec": form,
            "active_revision": form["revision"],
            "idempotency_key": (
                f"{form['reference_form_id']}:revision:{form['revision']}"
            ),
        })

    def status(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/v1/reference-form-jobs/{job_id}")

    def result(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/v1/reference-form-jobs/{job_id}/result")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/reference-form-jobs/{job_id}/cancel", {})

    def download(self, artifact_url: str, maximum_bytes: int = 512 * 1024 * 1024) -> bytes:
        target = urljoin(self.base_url, artifact_url)
        parsed_base = urlparse(self.base_url)
        parsed_target = urlparse(target)
        if (parsed_target.scheme, parsed_target.hostname, parsed_target.port) != (
            parsed_base.scheme, parsed_base.hostname, parsed_base.port
        ):
            raise ReferenceFormError("artifact URL changes the trusted gateway origin")
        try:
            with urlopen(target, timeout=60) as response:
                data = response.read(maximum_bytes + 1)
        except (HTTPError, URLError) as error:
            raise ReferenceFormError(f"candidate download failed: {error}") from error
        if len(data) > maximum_bytes:
            raise ReferenceFormError("candidate artifact exceeds 512 MiB")
        return data
