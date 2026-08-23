"""Revision-bound support optimization jobs.

The gateway produces a neutral suggestion graph only.  FreeCAD remains the
geometry authority and must reconstruct the graph and run all required checks
before a result can modify an enclosure.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from threading import RLock
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx


SUPPORT_SCHEMA = "design-studio.support-generation/1"
RESULT_SCHEMA = "design-studio.support-result/1"
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


class SupportJobError(ValueError):
    """A client-supplied support job violates the neutral contract."""


def _canonical_sha256(value: Any) -> str:
    material = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _finite_point(value: Any, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise SupportJobError(f"{path} must contain exactly three coordinates")
    result = []
    for coordinate in value:
        if type(coordinate) not in (int, float) or not math.isfinite(float(coordinate)):
            raise SupportJobError(f"{path} coordinates must be finite numbers")
        number = float(coordinate)
        if abs(number) > 1_000_000.0:
            raise SupportJobError(f"{path} exceeds the coordinate limit")
        result.append(number)
    return result


def _bounds(value: Any, path: str) -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise SupportJobError(f"{path} must contain exactly min and max")
    minimum = _finite_point(value["min"], f"{path}.min")
    maximum = _finite_point(value["max"], f"{path}.max")
    if any(maximum[index] <= minimum[index] for index in range(3)):
        raise SupportJobError(f"{path} must have positive extent")
    return {"min": minimum, "max": maximum}


def validate_support_spec(value: Any) -> dict[str, Any]:
    """Fail closed on the fields that bind optimization to exact host state."""
    if not isinstance(value, dict):
        raise SupportJobError("support specification must be an object")
    required = {
        "schema", "document", "placements", "design_volume_mm", "anchors",
        "forbidden_bounds_mm", "load_cases", "manufacturing", "seed",
    }
    if set(value) != required:
        raise SupportJobError(
            f"support fields differ; missing={sorted(required - set(value))}, "
            f"unknown={sorted(set(value) - required)}"
        )
    if value["schema"] != SUPPORT_SCHEMA:
        raise SupportJobError(f"schema must be {SUPPORT_SCHEMA}")
    document = value["document"]
    if not isinstance(document, dict) or set(document) != {
        "document_id", "revision", "sha256"
    }:
        raise SupportJobError("document binding is invalid")
    if not isinstance(document["document_id"], str) or not document["document_id"]:
        raise SupportJobError("document_id is required")
    if type(document["revision"]) is not int or document["revision"] < 0:
        raise SupportJobError("document revision must be a non-negative integer")
    digest = document["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise SupportJobError("document sha256 is invalid")

    design_volume = _bounds(value["design_volume_mm"], "design_volume_mm")
    placements = value["placements"]
    if not isinstance(placements, list) or not 1 <= len(placements) <= 4096:
        raise SupportJobError("placements must contain 1–4096 items")
    placement_ids: set[str] = set()
    for index, placement in enumerate(placements):
        required_placement = {
            "schema", "placement_id", "component_id", "component_sha256",
            "document", "transform", "coordinate_system", "snap", "locks",
            "clearance_mm",
        }
        if not isinstance(placement, dict) \
                or not required_placement.issubset(placement) \
                or set(placement) - required_placement - {"surface_anchor"} \
                or placement.get("schema") != "design-studio.component-placement/1":
            raise SupportJobError(f"placements[{index}] is not component-placement/1")
        if placement.get("document") != document:
            raise SupportJobError(f"placements[{index}] has a stale document binding")
        placement_id = placement.get("placement_id")
        if not isinstance(placement_id, str) or not placement_id or placement_id in placement_ids:
            raise SupportJobError("placement IDs must be non-empty and unique")
        component_sha = placement.get("component_sha256")
        if not isinstance(component_sha, str) or len(component_sha) != 64 or any(
            character not in "0123456789abcdef" for character in component_sha
        ):
            raise SupportJobError(f"placements[{index}] component digest is invalid")
        transform = placement.get("transform")
        if not isinstance(transform, dict) or set(transform) != {
            "translation_mm", "rotation_xyzw"
        }:
            raise SupportJobError(f"placements[{index}] transform is invalid")
        _finite_point(
            transform["translation_mm"],
            f"placements[{index}].transform.translation_mm",
        )
        quaternion = transform["rotation_xyzw"]
        if not isinstance(quaternion, list) or len(quaternion) != 4:
            raise SupportJobError(f"placements[{index}] quaternion is invalid")
        normalized_quaternion = []
        for component in quaternion:
            if type(component) not in (int, float) or not math.isfinite(float(component)):
                raise SupportJobError(f"placements[{index}] quaternion is invalid")
            normalized_quaternion.append(float(component))
        if sum(component * component for component in normalized_quaternion) < 1.0e-12:
            raise SupportJobError(f"placements[{index}] quaternion is zero")
        if placement.get("coordinate_system") not in {
            "world", "enclosure_local", "pcb_local", "surface_local"
        }:
            raise SupportJobError(f"placements[{index}] coordinate system is invalid")
        snap = placement.get("snap")
        snap_fields = {"grid", "surface", "axis", "symmetry", "clearance"}
        if not isinstance(snap, dict) or set(snap) != snap_fields or any(
            type(snap[field]) is not bool for field in snap_fields
        ):
            raise SupportJobError(f"placements[{index}] snap state is invalid")
        locks = placement.get("locks")
        lock_fields = {"position", "orientation", "surface_anchor"}
        if not isinstance(locks, dict) or set(locks) != lock_fields or any(
            type(locks[field]) is not bool for field in lock_fields
        ) or not (
            locks.get("position") and locks.get("orientation")
        ):
            raise SupportJobError(
                f"placements[{index}] must lock position and orientation before optimization"
            )
        clearance = placement.get("clearance_mm")
        if type(clearance) not in (int, float) or not math.isfinite(float(clearance)) \
                or not 0 <= float(clearance) <= 1000:
            raise SupportJobError(f"placements[{index}] clearance is invalid")
        placement_ids.add(placement_id)

    anchors = value["anchors"]
    if not isinstance(anchors, list) or len(anchors) > 16_384:
        raise SupportJobError("anchors must be a bounded array")
    normalized_anchors = []
    anchor_ids: set[str] = set()
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict) or set(anchor) != {"id", "position_mm", "kind"}:
            raise SupportJobError(f"anchors[{index}] fields are invalid")
        anchor_id = anchor["id"]
        if not isinstance(anchor_id, str) or not anchor_id or anchor_id in anchor_ids:
            raise SupportJobError("anchor IDs must be non-empty and unique")
        if anchor["kind"] not in {"mount", "load", "shell", "connector", "pcb"}:
            raise SupportJobError(f"anchors[{index}].kind is invalid")
        position = _finite_point(anchor["position_mm"], f"anchors[{index}].position_mm")
        if any(
            position[axis] < design_volume["min"][axis]
            or position[axis] > design_volume["max"][axis]
            for axis in range(3)
        ):
            raise SupportJobError(f"anchor {anchor_id!r} is outside the design volume")
        anchor_ids.add(anchor_id)
        normalized_anchors.append({**anchor, "position_mm": position})

    forbidden = value["forbidden_bounds_mm"]
    if not isinstance(forbidden, list) or len(forbidden) > 16_384:
        raise SupportJobError("forbidden_bounds_mm must be a bounded array")
    normalized_forbidden = [
        _bounds(item, f"forbidden_bounds_mm[{index}]")
        for index, item in enumerate(forbidden)
    ]
    loads = value["load_cases"]
    if not isinstance(loads, list) or len(loads) > 4096:
        raise SupportJobError("load_cases must be a bounded array")
    normalized_loads = []
    for index, load in enumerate(loads):
        if not isinstance(load, dict) or set(load) != {"anchor_id", "force_n"}:
            raise SupportJobError(f"load_cases[{index}] fields are invalid")
        if load["anchor_id"] not in anchor_ids:
            raise SupportJobError(f"load_cases[{index}] references an unknown anchor")
        normalized_loads.append({
            "anchor_id": load["anchor_id"],
            "force_n": _finite_point(load["force_n"], f"load_cases[{index}].force_n"),
        })
    manufacturing = value["manufacturing"]
    expected_manufacturing = {
        "process", "minimum_wall_mm", "minimum_rib_mm", "clearance_mm"
    }
    if not isinstance(manufacturing, dict) or set(manufacturing) != expected_manufacturing:
        raise SupportJobError("manufacturing rules are invalid")
    if manufacturing["process"] not in {"fdm", "sls", "cnc", "injection_molding"}:
        raise SupportJobError("manufacturing process is invalid")
    for field in ("minimum_wall_mm", "minimum_rib_mm", "clearance_mm"):
        number = manufacturing[field]
        if type(number) not in (int, float) or not math.isfinite(float(number)):
            raise SupportJobError(f"manufacturing.{field} must be finite")
        if float(number) < 0 or (field != "clearance_mm" and float(number) == 0):
            raise SupportJobError(f"manufacturing.{field} is outside its allowed range")
    if type(value["seed"]) is not int or not 0 <= value["seed"] <= 2_147_483_647:
        raise SupportJobError("seed is invalid")
    return {
        **deepcopy(value),
        "design_volume_mm": design_volume,
        "anchors": normalized_anchors,
        "forbidden_bounds_mm": normalized_forbidden,
        "load_cases": normalized_loads,
    }


def _nearest_shell(point: list[float], bounds: dict[str, list[float]]) -> list[float]:
    candidates = []
    for axis in range(3):
        for side in ("min", "max"):
            target = list(point)
            target[axis] = bounds[side][axis]
            candidates.append((abs(point[axis] - target[axis]), axis, side, target))
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def mock_support_result(spec: dict[str, Any]) -> dict[str, Any]:
    """Create a stable suggestion for contract testing without pretending to run FEA."""
    minimum_rib = float(spec["manufacturing"]["minimum_rib_mm"])
    clearance = float(spec["manufacturing"]["clearance_mm"])
    loads: dict[str, float] = {}
    for load in spec["load_cases"]:
        loads[load["anchor_id"]] = loads.get(load["anchor_id"], 0.0) + math.dist(
            [0.0, 0.0, 0.0], load["force_n"]
        )
    ribs = []
    for anchor in sorted(spec["anchors"], key=lambda item: item["id"]):
        if anchor["kind"] == "shell":
            continue
        load = loads.get(anchor["id"], 0.0)
        thickness = minimum_rib + math.sqrt(load) * 0.05
        ribs.append({
            "id": f"rib:{anchor['id']}:0",
            "anchor_id": anchor["id"],
            "start_mm": anchor["position_mm"],
            "end_mm": _nearest_shell(anchor["position_mm"], spec["design_volume_mm"]),
            "thickness_mm": round(max(minimum_rib, thickness), 6),
            "height_mm": round(max(
                minimum_rib * 1.5,
                float(spec["manufacturing"]["minimum_wall_mm"]) * 1.5,
            ), 6),
            "generator": "mock-cloud-load-path-v1",
        })
    cavities = [{
        "id": f"cavity:{index}",
        "min": [number - clearance for number in bounds["min"]],
        "max": [number + clearance for number in bounds["max"]],
        "clearance_mm": clearance,
    } for index, bounds in enumerate(spec["forbidden_bounds_mm"])]
    result = {
        "schema": RESULT_SCHEMA,
        "document": spec["document"],
        "input_sha256": _canonical_sha256(spec),
        "generator": {
            "name": "designstudio-mock-cloud-support",
            "version": 1,
            "seed": spec["seed"],
            "authoritative_geometry": False,
        },
        "status": "pass",
        "cavities": cavities,
        "ribs": ribs,
        "findings": [{
            "severity": "info",
            "code": "MOCK_OPTIMIZER_NO_FEA",
            "message": "Contract-only result; no GPU optimization or structural solve was run.",
        }],
        "required_host_checks": [
            "brep_valid",
            "exact_collision_free",
            "manufacturing_rules_pass",
            "structural_screening_pass",
            "assembly_order_pass",
        ],
    }
    result["result_sha256"] = _canonical_sha256(result)
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Record:
    job: dict[str, Any]
    spec: dict[str, Any]
    idempotency_key: str


class SupportJobManager:
    def __init__(self, mock: bool):
        self.mock = mock
        self._lock = RLock()
        self._jobs: dict[str, _Record] = {}
        self._idempotency: dict[str, str] = {}

    def create(self, raw_spec: Any, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        spec = validate_support_spec(raw_spec)
        input_sha = _canonical_sha256(spec)
        key = idempotency_key or input_sha
        if not isinstance(key, str) or not 1 <= len(key) <= 256:
            raise SupportJobError("idempotency_key must contain 1–256 characters")
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                record = self._jobs[existing_id]
                if record.job["input_sha256"] != input_sha:
                    raise SupportJobError(
                        "idempotency_key was already used for different input"
                    )
                return deepcopy(record.job), False
            job_id = str(uuid4())
            now = _utc_now()
            job = {
                "schema": "design-studio.support-job/1",
                "job_id": job_id,
                "status": "completed" if self.mock else "queued",
                "document": spec["document"],
                "input_sha256": input_sha,
                "created_at": now,
                "updated_at": now,
                "result": mock_support_result(spec) if self.mock else None,
                "error": None,
            }
            self._jobs[job_id] = _Record(job=job, spec=spec, idempotency_key=key)
            self._idempotency[key] = job_id
            return deepcopy(job), True

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return deepcopy(record.job) if record else None

    def result(self, job_id: str) -> tuple[str, dict[str, Any] | None]:
        job = self.get(job_id)
        if job is None:
            return "missing", None
        return job["status"], deepcopy(job["result"])

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            if record.job["status"] not in TERMINAL_STATUSES:
                record.job["status"] = "cancelled"
                record.job["updated_at"] = _utc_now()
            return deepcopy(record.job)

    async def run_remote(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.job["status"] != "queued":
                return
            record.job["status"] = "running"
            record.job["updated_at"] = _utc_now()
            spec = deepcopy(record.spec)
        worker_url = os.environ.get("DS_SUPPORT_WORKER_URL", "").rstrip("/")
        parsed = urlparse(worker_url)
        allow_remote = os.environ.get("DS_SUPPORT_ALLOW_REMOTE", "0") == "1"
        if not worker_url or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            await self._fail(job_id, "DS_SUPPORT_WORKER_URL is not configured")
            return
        if not allow_remote and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            await self._fail(
                job_id,
                "remote support worker is disabled; use a loopback URL or explicitly allow it",
            )
            return
        try:
            timeout = float(os.environ.get("DS_SUPPORT_TIMEOUT_SECONDS", "300"))
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{worker_url}/v1/optimize-support", json={"spec": spec}
                )
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
                raise SupportJobError("worker returned an invalid support-result schema")
            if result.get("document") != spec["document"]:
                raise SupportJobError("worker returned a stale document binding")
            if result.get("input_sha256") != _canonical_sha256(spec):
                raise SupportJobError("worker returned an input digest mismatch")
            if result.get("generator", {}).get("authoritative_geometry") is not False:
                raise SupportJobError("worker must mark cloud geometry non-authoritative")
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None or current.job["status"] == "cancelled":
                    return
                current.job["status"] = "completed"
                current.job["result"] = result
                current.job["updated_at"] = _utc_now()
        except (httpx.HTTPError, ValueError, SupportJobError) as error:
            await self._fail(job_id, f"{type(error).__name__}: {error}")

    async def _fail(self, job_id: str, message: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.job["status"] == "cancelled":
                return
            record.job["status"] = "failed"
            record.job["error"] = message[:2000]
            record.job["updated_at"] = _utc_now()
