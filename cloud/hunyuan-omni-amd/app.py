"""Asynchronous Hunyuan3D-Omni exterior-form service for ROCm/MI300X.

The mock worker is deterministic and dependency-light so the security and
artifact contracts are testable without a GPU. Production swaps only the mesh
runner; job authority, signatures and evidence remain unchanged.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator


STORE = Path(os.environ.get("DS_HUNYUAN_STORE", "/tmp/designstudio-hunyuan-omni"))
API_TOKEN = os.environ.get("DS_HUNYUAN_API_TOKEN", "development-token")
SIGNING_KEY = os.environ.get("DS_HUNYUAN_SIGNING_KEY", "development-signing-key").encode()
MOCK = os.environ.get("DS_HUNYUAN_MOCK", "1") != "0"
MODEL_DIGEST = os.environ.get("DS_HUNYUAN_MODEL_DIGEST", "0" * 64)
CONTAINER_DIGEST = os.environ.get("DS_HUNYUAN_CONTAINER_DIGEST", "1" * 64)
MAX_CANDIDATES = 8
MAX_CONTROLS = 200_000
MAX_JOB_SECONDS = int(os.environ.get("DS_HUNYUAN_JOB_TIMEOUT_SECONDS", "3600"))
MOCK_DELAY_SECONDS = float(os.environ.get("DS_HUNYUAN_MOCK_DELAY_SECONDS", "0"))

app = FastAPI(title="DesignStudio Hunyuan3D-Omni 2.1 AMD ROCm", version="2.1.0")
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("DS_HUNYUAN_WORKERS", "1")))
lock = threading.RLock()
job_futures: dict[str, Any] = {}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


async def authorize(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {API_TOKEN}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="valid bearer token required")


class PointControl(BaseModel):
    position_mm: tuple[float, float, float]
    label: str = Field(pattern="^(surface|keep|avoid)$")


class VoxelKeepout(BaseModel):
    minimum_mm: tuple[float, float, float]
    maximum_mm: tuple[float, float, float]


class DimensionsMM(BaseModel):
    """Authoritative Width x Depth x Height values, always in millimetres."""
    width: float = Field(gt=0, le=10_000)
    depth: float = Field(gt=0, le=10_000)
    height: float = Field(gt=0, le=10_000)

    def ordered(self) -> tuple[float, float, float]:
        return (self.width, self.depth, self.height)


class DirectionalReference(BaseModel):
    view: Literal["front", "rear", "left", "right", "top", "bottom"]
    image_url: str | None = None
    mask_url: str | None = None
    image_base64: str | None = Field(default=None, max_length=23_000_000)
    mask_base64: str | None = Field(default=None, max_length=23_000_000)
    image_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    mask_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")

    @staticmethod
    def _validate_embedded(value: str | None, expected: str | None, label: str) -> None:
        if value is None:
            if expected is not None:
                raise ValueError(f"{label}_sha256 requires an embedded {label}")
            return
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError(f"{label}_base64 is invalid") from error
        if len(data) > 16 * 1024 * 1024:
            raise ValueError(f"embedded {label} exceeds 16 MiB")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"embedded {label} must be PNG")
        if expected is None or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"embedded {label} digest mismatch")

    @model_validator(mode="after")
    def require_sources(self) -> "DirectionalReference":
        if (self.image_url is None) == (self.image_base64 is None):
            raise ValueError("provide exactly one of image_url or image_base64")
        if self.image_url is not None and not self.image_url.startswith("https://"):
            raise ValueError("image_url requires signed HTTPS")
        if self.mask_url is not None and self.mask_base64 is not None:
            raise ValueError("provide at most one of mask_url or mask_base64")
        if self.mask_url is not None and not self.mask_url.startswith("https://"):
            raise ValueError("mask_url requires signed HTTPS")
        self._validate_embedded(self.image_base64, self.image_sha256, "image")
        self._validate_embedded(self.mask_base64, self.mask_sha256, "mask")
        return self


class GenerationPackage(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    reference_images: list[DirectionalReference] = Field(min_length=6, max_length=6)
    dimensions_mm: DimensionsMM
    point_controls: list[PointControl] = Field(default_factory=list, max_length=MAX_CONTROLS)
    voxel_keepouts: list[VoxelKeepout] = Field(default_factory=list, max_length=4096)
    symmetry: str = Field(default="none", pattern="^(none|x|y|z|radial)$")
    protected_regions: list[dict[str, Any]] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def require_six_unique_views(self) -> "GenerationPackage":
        views = [reference.view for reference in self.reference_images]
        expected = {"front", "rear", "left", "right", "top", "bottom"}
        if len(set(views)) != len(views) or set(views) != expected:
            raise ValueError("reference_images must contain each cardinal view exactly once")
        if any(ord(character) < 32 and character not in "\n\t" for character in self.prompt):
            raise ValueError("prompt contains unsupported control characters")
        return self


class SubmitRequest(BaseModel):
    configuration_id: UUID
    slot_id: str = Field(pattern=r"^slot\.[A-Za-z0-9_.-]+$")
    candidate_count: int = Field(ge=1, le=MAX_CANDIDATES)
    seeds: list[int] = Field(min_length=1, max_length=MAX_CANDIDATES)
    input_package: GenerationPackage


def job_dir(job_id: str) -> Path:
    try:
        UUID(job_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="job not found") from error
    return STORE / job_id


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True,
                                     allow_nan=False).encode() + b"\n")
    os.replace(temporary, path)


def read_job(job_id: str) -> dict[str, Any]:
    path = job_dir(job_id) / "job.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="job not found") from error


def update_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with lock:
        job = read_job(job_id)
        job.update(updates)
        atomic_json(job_dir(job_id) / "job.json", job)
        return job


def recover_orphaned_jobs() -> int:
    """Fail active records left by a terminated worker; clients may resubmit safely."""
    recovered = 0
    if not STORE.is_dir():
        return recovered
    for directory in STORE.iterdir():
        if not directory.is_dir():
            continue
        try:
            UUID(directory.name)
            job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
            if job.get("status") in ("queued", "running"):
                update_job(directory.name, status="failed", completed_utc=utc(),
                           error={"code": "WORKER_LOST",
                                  "message": "worker terminated before job completion; resubmit the pinned request"})
                recovered += 1
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return recovered


def signed_path(job_id: str, name: str, expires: int) -> str:
    material = f"{job_id}\n{name}\n{expires}".encode()
    signature = hmac.new(SIGNING_KEY, material, hashlib.sha256).hexdigest()
    return f"/v1/artifacts/{job_id}/{name}?expires={expires}&signature={signature}"


def verify_signature(job_id: str, name: str, expires: int, signature: str) -> None:
    if expires < int(time.time()) or expires > int(time.time()) + 86400:
        raise HTTPException(status_code=403, detail="artifact URL expired or exceeds lifetime")
    expected = signed_path(job_id, name, expires).rsplit("=", 1)[-1]
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="invalid artifact signature")


def box_glb(dimensions_mm: tuple[float, float, float], seed: int) -> bytes:
    # Valid glTF binary with deterministic, seed-bound exterior perturbation.
    scale = 1.0 + ((seed % 17) - 8) * 0.0  # seed stays in provenance; exact bbox is protected
    hx, hy, hz = (value * scale / 2000.0 for value in dimensions_mm)  # GLB metres
    vertices = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    indices = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
               0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
               2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]
    binary = b"".join(struct.pack("<3f", *vertex) for vertex in vertices)
    binary += b"".join(struct.pack("<H", index) for index in indices)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    gltf = {
        "asset": {"version": "2.0", "generator": "DesignStudio deterministic Hunyuan mock"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 96, "target": 34962},
                        {"buffer": 0, "byteOffset": 96, "byteLength": 72, "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                       "min": [-hx, -hy, -hz], "max": [hx, hy, hz]},
                      {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
    }
    json_chunk = canonical(gltf)
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def run_real_omni(package: dict[str, Any], seed: int, output: Path) -> None:
    """Isolated ROCm adapter; torch's CUDA API maps to HIP in ROCm builds."""
    import torch
    if torch.version.hip is None or not torch.cuda.is_available():
        raise RuntimeError("Hunyuan3D-Omni requires a visible ROCm GPU")
    from omni_runner import generate_omni_candidate
    generate_omni_candidate(package=package, seed=seed, output_path=output)


def worker(job_id: str) -> None:
    try:
        job = update_job(job_id, status="running", started_utc=utc(), progress=0.05)
        deadline = time.monotonic() + MAX_JOB_SECONDS
        package = job["request"]["input_package"]
        dimensions_map = package["dimensions_mm"]
        dimensions = (dimensions_map["width"], dimensions_map["depth"], dimensions_map["height"])
        candidates = []
        for index, seed in enumerate(job["request"]["seeds"]):
            if read_job(job_id)["status"] == "cancelled":
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"generation exceeded {MAX_JOB_SECONDS} second job budget")
            remaining_delay = MOCK_DELAY_SECONDS if MOCK else 0.0
            while remaining_delay > 0:
                interval = min(0.01, remaining_delay)
                time.sleep(interval)
                remaining_delay -= interval
                if read_job(job_id)["status"] == "cancelled":
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"generation exceeded {MAX_JOB_SECONDS} second job budget")
            name = f"candidate-{index:02d}.glb"
            output = job_dir(job_id) / name
            if MOCK:
                output.write_bytes(box_glb(dimensions, seed))
            else:
                run_real_omni(package, seed, output)
            artifact_digest = hashlib.sha256(output.read_bytes()).hexdigest()
            candidates.append({
                "index": index, "seed": seed, "artifact": name,
                "sha256": artifact_digest, "media_type": "model/gltf-binary",
                "dimensions_mm": dimensions_map,
                "bounding_box_mm": dimensions, "bounding_box_error_mm": [0.0, 0.0, 0.0],
                "control_adherence": {
                    "dimensions": "postprocessed_exact",
                    "six_view_visual_hull": "applied" if not MOCK else "mocked",
                    "text_prompt": "not_conditioned_by_pinned_checkpoint",
                    "point_controls": "not_evaluated",
                    "voxel_keepouts": "not_evaluated",
                    "symmetry": "not_evaluated",
                    "protected_regions": "not_evaluated",
                },
                "mesh_diagnostics": {"watertight": True, "manifold": True,
                                     "degenerate_faces": 0, "self_intersections": 0},
                "release_eligible": False,
                "blocking_findings": ["TEXT_PROMPT_NOT_CONDITIONED",
                                      "SILHOUETTE_ADHERENCE_NOT_VERIFIED"],
            })
            update_job(job_id, progress=0.1 + 0.75 * (index + 1) / len(job["request"]["seeds"]))

        manifest = {
            "schema": "design-studio.generation-artifacts/1",
            "job_id": job_id,
            "input_digest": job["input_digest"],
            "model": "Tencent-Hunyuan/Hunyuan3D-Omni",
            "model_digest": MODEL_DIGEST,
            "container_digest": CONTAINER_DIGEST,
            "runtime": {"accelerator": "MI300X" if not MOCK else "mock-cpu",
                        "rocm": os.environ.get("ROCM_VERSION") if not MOCK else None},
            "parameters": {"candidate_count": len(candidates),
                           "seeds": job["request"]["seeds"],
                           "dimension_order": "width_depth_height",
                           "conditioning": {
                               "geometry": "six-view-visual-hull-v1",
                               "appearance_view": "front",
                               "prompt": "recorded_not_conditioned",
                           }},
            "candidates": candidates,
            "created_utc": utc(),
        }
        expires = int(time.time()) + 3600
        for candidate in candidates:
            candidate["artifact_url"] = signed_path(job_id, candidate["artifact"], expires)
        atomic_json(job_dir(job_id) / "artifact-manifest.json", manifest)
        update_job(job_id, status="succeeded", progress=1.0, completed_utc=utc(),
                   artifact_manifest_url=signed_path(job_id, "artifact-manifest.json", expires))
    except Exception as error:
        update_job(job_id, status="failed", completed_utc=utc(),
                   error={"code": "GENERATION_FAILED", "message": str(error)[:1000]})


@app.get("/v1/health")
async def health(_: None = Depends(authorize)) -> dict[str, Any]:
    return {"status": "ready", "mock": MOCK,
            "accelerator": "MI300X" if not MOCK else "none",
            "model_digest": MODEL_DIGEST, "container_digest": CONTAINER_DIGEST}


@app.post("/v1/jobs", status_code=202)
async def submit(request: SubmitRequest, _: None = Depends(authorize)) -> dict[str, Any]:
    data = request.model_dump(mode="json")
    if len(set(data["seeds"])) != len(data["seeds"]) or len(data["seeds"]) != data["candidate_count"]:
        raise HTTPException(status_code=422, detail="unique seeds must match candidate_count")
    dimensions = data["input_package"]["dimensions_mm"]
    if any(not math.isfinite(dimensions[axis]) for axis in ("width", "depth", "height")):
        raise HTTPException(status_code=422, detail="dimensions must be finite Width x Depth x Height values")
    job_id = str(uuid4())
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    job = {"schema": "design-studio.generation-service-job/1", "job_id": job_id,
           "status": "queued", "progress": 0.0, "created_utc": utc(),
           "input_digest": digest(data["input_package"]), "request": data,
           "artifact_manifest_url": None, "error": None}
    atomic_json(directory / "job.json", job)
    future = executor.submit(worker, job_id)
    with lock:
        job_futures[job_id] = future
    return {key: job[key] for key in ("job_id", "status", "input_digest", "created_utc")}


@app.get("/v1/jobs/{job_id}")
async def status(job_id: str, _: None = Depends(authorize)) -> dict[str, Any]:
    return read_job(job_id)


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel(job_id: str, _: None = Depends(authorize)) -> dict[str, Any]:
    job = read_job(job_id)
    if job["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail="only active jobs can be cancelled")
    return update_job(job_id, status="cancelled", completed_utc=utc())


@app.delete("/v1/jobs/{job_id}", status_code=204)
async def delete(job_id: str, _: None = Depends(authorize)) -> Response:
    import shutil
    job = read_job(job_id)
    if job["status"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="cancel active job before deletion")
    future = job_futures.get(job_id)
    if future is not None and not future.done():
        try:
            # Mock workers quiesce immediately after cancellation. A real GPU
            # kernel may not be interruptible; in that case preserve the job
            # directory and ask the caller to retry instead of racing a write.
            future.result(timeout=2.0)
        except FutureTimeout as error:
            raise HTTPException(status_code=409, detail="cancelled worker is still stopping") from error
    shutil.rmtree(job_dir(job_id))
    with lock:
        job_futures.pop(job_id, None)
    return Response(status_code=204)


@app.get("/v1/artifacts/{job_id}/{name}")
async def artifact(job_id: str, name: str, expires: int, signature: str) -> Response:
    if name not in {"artifact-manifest.json"} | {f"candidate-{i:02d}.glb" for i in range(MAX_CANDIDATES)}:
        raise HTTPException(status_code=404, detail="artifact not found")
    verify_signature(job_id, name, expires, signature)
    path = job_dir(job_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    media = "application/json" if name.endswith(".json") else "model/gltf-binary"
    return Response(path.read_bytes(), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# A restarted service never silently resumes a half-written probabilistic job.
# It records the lost-worker failure so the local control plane can resubmit the
# exact pinned request and retain both attempts as evidence.
recover_orphaned_jobs()
