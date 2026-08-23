"""ROCm candidate optimizer for neutral cavity/rib graphs.

This service never creates authoritative CAD.  It uses PyTorch tensor
optimization to suggest load paths; the desktop must rebuild those paths in
FreeCAD and run exact collision/manufacturing/structural checks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from support_jobs import (
    RESULT_SCHEMA,
    SupportJobError,
    mock_support_result,
    validate_support_spec,
)


MOCK = os.environ.get("DS_SUPPORT_MOCK", "1") != "0"
app = FastAPI(title="DesignStudio ROCm Support Optimizer", version="1.0.0")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _shell_targets(point: list[float], bounds: dict[str, list[float]]) -> list[list[float]]:
    targets = []
    for axis in range(3):
        for side in ("min", "max"):
            target = list(point)
            target[axis] = bounds[side][axis]
            targets.append(target)
    return targets


def _deduplicate_path(points: list[list[float]]) -> list[list[float]]:
    """Remove optimizer-collapsed consecutive segments before host validation."""
    if not points:
        return []
    cleaned = [points[0]]
    for point in points[1:]:
        if math.dist(cleaned[-1], point) >= 1.0e-4:
            cleaned.append(point)
    return cleaned


def _optimize_anchor(torch, anchor: dict[str, Any], spec: dict[str, Any]) -> list[list[float]]:
    """Optimize one dogleg against AABB penalties in a stable six-face batch."""
    device = torch.device("cuda")
    dtype = torch.float32
    start = torch.tensor(anchor["position_mm"], device=device, dtype=dtype)
    targets = torch.tensor(
        _shell_targets(anchor["position_mm"], spec["design_volume_mm"]),
        device=device, dtype=dtype,
    )
    starts = start.expand_as(targets)
    midpoint = ((starts + targets) * 0.5).clone().detach().requires_grad_(True)
    optimizer = torch.optim.Adam([midpoint], lr=0.15)
    minimum = torch.tensor(
        spec["design_volume_mm"]["min"], device=device, dtype=dtype
    )
    maximum = torch.tensor(
        spec["design_volume_mm"]["max"], device=device, dtype=dtype
    )
    clearance = max(0.05, float(spec["manufacturing"]["clearance_mm"]))
    obstacles = [(
        torch.tensor(item["min"], device=device, dtype=dtype),
        torch.tensor(item["max"], device=device, dtype=dtype),
    ) for item in spec["forbidden_bounds_mm"]]
    interpolation = torch.linspace(0.04, 0.96, 25, device=device, dtype=dtype)
    for _ in range(160):
        optimizer.zero_grad(set_to_none=True)
        first = starts[:, None, :] + (
            midpoint[:, None, :] - starts[:, None, :]
        ) * interpolation[None, :, None]
        second = midpoint[:, None, :] + (
            targets[:, None, :] - midpoint[:, None, :]
        ) * interpolation[None, :, None]
        samples = torch.cat((first, second), dim=1)
        length = torch.linalg.vector_norm(midpoint - starts, dim=1) \
            + torch.linalg.vector_norm(targets - midpoint, dim=1)
        collision = torch.zeros_like(length)
        for obstacle_min, obstacle_max in obstacles:
            # Positive only inside, or within clearance of, every AABB plane.
            plane_clearance = torch.minimum(
                samples - obstacle_min, obstacle_max - samples
            ).amin(dim=2)
            collision = collision + torch.relu(
                plane_clearance + clearance
            ).pow(2).sum(dim=1)
        outside = torch.relu(minimum - midpoint).pow(2).sum(dim=1) \
            + torch.relu(midpoint - maximum).pow(2).sum(dim=1)
        bending = torch.linalg.vector_norm(
            (midpoint - starts) - (targets - midpoint), dim=1
        )
        loss_per_candidate = length + 0.02 * bending + 2500.0 * collision \
            + 2500.0 * outside
        loss_per_candidate.sum().backward()
        optimizer.step()
        with torch.no_grad():
            midpoint.clamp_(minimum, maximum)
    with torch.no_grad():
        chosen = int(torch.argmin(loss_per_candidate).item())
        points = [
            starts[chosen].cpu().tolist(),
            midpoint[chosen].cpu().tolist(),
            targets[chosen].cpu().tolist(),
        ]
    points = _deduplicate_path(points)
    return [[round(float(number), 6) for number in point] for point in points]


def optimize_support_result(raw_spec: Any) -> dict[str, Any]:
    spec = validate_support_spec(raw_spec)
    if MOCK:
        return mock_support_result(spec)
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("PyTorch is required for the ROCm optimizer") from error
    if not torch.cuda.is_available() or not getattr(torch.version, "hip", None):
        raise RuntimeError("an AMD ROCm device is required when DS_SUPPORT_MOCK=0")

    loads: dict[str, float] = {}
    for load in spec["load_cases"]:
        magnitude = math.sqrt(sum(number * number for number in load["force_n"]))
        loads[load["anchor_id"]] = loads.get(load["anchor_id"], 0.0) + magnitude
    minimum_rib = float(spec["manufacturing"]["minimum_rib_mm"])
    minimum_wall = float(spec["manufacturing"]["minimum_wall_mm"])
    ribs = []
    for anchor in sorted(spec["anchors"], key=lambda item: item["id"]):
        if anchor["kind"] == "shell":
            continue
        path = _optimize_anchor(torch, anchor, spec)
        thickness = max(
            minimum_rib,
            minimum_rib + math.sqrt(loads.get(anchor["id"], 0.0)) * 0.05,
        )
        for index in range(len(path) - 1):
            ribs.append({
                "id": f"rib:{anchor['id']}:{index}",
                "anchor_id": anchor["id"],
                "start_mm": path[index],
                "end_mm": path[index + 1],
                "thickness_mm": round(thickness, 6),
                "height_mm": round(max(thickness * 1.5, minimum_wall * 1.5), 6),
                "generator": "rocm-aabb-load-path-v1",
            })
    clearance = float(spec["manufacturing"]["clearance_mm"])
    cavities = [{
        "id": f"cavity:{index}",
        "min": [number - clearance for number in bounds["min"]],
        "max": [number + clearance for number in bounds["max"]],
        "clearance_mm": clearance,
    } for index, bounds in enumerate(spec["forbidden_bounds_mm"])]
    result = {
        "schema": RESULT_SCHEMA,
        "document": deepcopy(spec["document"]),
        "input_sha256": _digest(spec),
        "generator": {
            "name": "designstudio-rocm-support",
            "version": 1,
            "seed": spec["seed"],
            "authoritative_geometry": False,
            "device": torch.cuda.get_device_name(0),
            "rocm": str(torch.version.hip),
        },
        "status": "pass",
        "cavities": cavities,
        "ribs": ribs,
        "findings": [{
            "severity": "info",
            "code": "APPROXIMATE_AABB_OPTIMIZATION",
            "message": "ROCm paths require exact host collision and structural checks.",
        }],
        "required_host_checks": [
            "brep_valid",
            "exact_collision_free",
            "manufacturing_rules_pass",
            "structural_screening_pass",
            "assembly_order_pass",
        ],
    }
    result["result_sha256"] = _digest(result)
    return result


class OptimizeReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    if MOCK:
        return {"ok": True, "mock": True, "device": "cpu-contract"}
    try:
        import torch
        return {
            "ok": bool(torch.cuda.is_available() and torch.version.hip),
            "mock": False,
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "rocm": str(torch.version.hip),
        }
    except ImportError:
        return {"ok": False, "mock": False, "device": None, "rocm": None}


@app.post("/v1/optimize-support")
async def optimize_support(req: OptimizeReq) -> dict[str, Any]:
    try:
        return optimize_support_result(req.spec)
    except SupportJobError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
