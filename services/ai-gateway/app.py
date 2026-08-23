"""Design Studio AI Gateway (FastAPI) — served on AMD MI300X (ROCm/HIP).

In production this fronts a self-hosted 70B-class copilot via vLLM-on-ROCm and a
HIP solver/surrogate service (192 GB HBM3 fits a 70B model on one GPU). The
desktop app is a thin client and never links ROCm — it calls these endpoints.

There is no MI300X in CI, so the service runs in MOCK mode by default
(DS_AI_MOCK=1): endpoints answer deterministically with no GPU, so the contract
and the client integration are testable anywhere. Set DS_AI_MOCK=0 in the ROCm
container to attach the real vLLM engine.

Implements docs/schemas/ai-gateway.openapi.yaml.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
import httpx
from urllib.parse import urlparse

from proposal import (
    ProposalBuildError,
    build_replace_line_constraints_proposal,
    validate_proposal_structure,
)
from shape_backend import HunyuanShapeBackend
from reference_form_jobs import ReferenceFormJobError, ReferenceFormJobManager
from support_jobs import SupportJobError, SupportJobManager
from mechanical_feedback import build_mechanical_feedback
from solver_workers import SolverRegistry, WorkerContractError
from token_accounting import normalize_usage, unavailable_usage

MOCK = os.environ.get("DS_AI_MOCK", "1") != "0"
MODEL = os.environ.get("DS_AI_MODEL", "Qwen/Qwen3.5-35B-A3B")
SHAPE_MODEL = os.environ.get("DS_3D_MODEL", "tencent/Hunyuan3D-2.1")
shape_backend = HunyuanShapeBackend(MOCK)
support_jobs = SupportJobManager(MOCK)
reference_form_jobs = ReferenceFormJobManager(MOCK)
mechanical_workers = SolverRegistry.default()

app = FastAPI(title="Design Studio AI Gateway", version="1.0.0")


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    rocm_version = None
    services: dict[str, Any] = {
        "vllm": {"status": "mock" if MOCK else "unavailable"},
        "support_optimizer": {"status": "mock" if MOCK else "unavailable"},
        "mechanical_workers": {
            kind: {"status": "registered", "worker": worker.name}
            for kind, worker in mechanical_workers.workers.items()
        },
        "reference_form_generation": {
            "status": "mock" if MOCK else "rocm-parity-gated",
            "workers": 1,
        },
    }
    loaded_models: list[str] = []
    if not MOCK:
        try:
            import torch

            rocm_version = torch.version.hip
        except ImportError:
            rocm_version = os.environ.get("ROCM_VERSION", "unknown")
        vllm_url = os.environ.get("DS_VLLM_URL", "http://127.0.0.1:8001").rstrip("/")
        support_url = os.environ.get("DS_SUPPORT_WORKER_URL", "").rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(f"{vllm_url}/v1/models")
                response.raise_for_status()
                loaded_models = [
                    item["id"] for item in response.json().get("data", [])
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                services["vllm"] = {
                    "status": "ready" if MODEL in loaded_models else "model-mismatch",
                    "models": loaded_models,
                }
            except (httpx.HTTPError, ValueError, TypeError):
                services["vllm"] = {"status": "unavailable"}
            if support_url:
                try:
                    response = await client.get(f"{support_url}/v1/health")
                    response.raise_for_status()
                    worker = response.json()
                    services["support_optimizer"] = {
                        "status": "ready" if worker.get("ok") else "unavailable",
                        "device": worker.get("device"),
                        "rocm": worker.get("rocm"),
                    }
                    if rocm_version in (None, "unknown") and worker.get("rocm"):
                        rocm_version = str(worker["rocm"])
                except (httpx.HTTPError, ValueError, TypeError):
                    services["support_optimizer"] = {"status": "unavailable"}
    return {
        "gpu": "MI300X" if not MOCK else "none",
        "rocm": rocm_version,
        "models_loaded": loaded_models,
        "services": services,
        "mock": MOCK,
    }


class MechanicalAnalysisReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job: dict[str, Any]


class MechanicalFeedbackReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    study: dict[str, Any]


@app.post("/v1/mechanical-analysis")
async def submit_mechanical_analysis(req: MechanicalAnalysisReq) -> dict[str, Any]:
    """Run an exact registered worker on explicitly approved geometry."""
    try:
        return mechanical_workers.submit(req.job)
    except WorkerContractError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/mechanical-feedback")
async def mechanical_feedback(req: MechanicalFeedbackReq) -> dict[str, Any]:
    """Return advisory ranking; this endpoint cannot approve or release CAD."""
    try:
        return build_mechanical_feedback(req.study)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class ChatCacheDescriptor(BaseModel):
    """Caller-verifiable, project/session-isolated prefix-cache scope."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["disabled", "project", "session"] = "disabled"
    scope_id: str | None = Field(default=None, min_length=1, max_length=256)
    prefix_message_count: int = Field(default=0, ge=0, le=4096)
    prefix_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ChatReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    enable_thinking: bool = False
    cache: ChatCacheDescriptor = Field(default_factory=ChatCacheDescriptor)


def _canonical_prefix_sha256(
    messages: list[dict[str, Any]], prefix_message_count: int
) -> str:
    canonical = json.dumps(
        messages[:prefix_message_count],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cache_parameters(
    messages: list[dict[str, Any]], descriptor: ChatCacheDescriptor
) -> tuple[str | None, dict[str, Any]]:
    if descriptor.prefix_message_count > len(messages):
        raise HTTPException(
            status_code=422,
            detail="cache prefix_message_count exceeds the message count",
        )
    digest = _canonical_prefix_sha256(messages, descriptor.prefix_message_count)
    if descriptor.mode == "disabled":
        if (
            descriptor.scope_id is not None
            or descriptor.prefix_sha256 is not None
            or descriptor.prefix_message_count != 0
        ):
            raise HTTPException(
                status_code=422,
                detail="disabled cache mode cannot include scope or prefix fields",
            )
        return None, {
            "schema": "design-studio.inference-evidence/1",
            "cache_mode": "disabled",
            "cache_applied": False,
            "prefix_message_count": 0,
            "prefix_sha256": digest,
        }
    if descriptor.scope_id is None or descriptor.prefix_message_count < 1:
        raise HTTPException(
            status_code=422,
            detail="scoped cache requires scope_id and a non-empty prefix",
        )
    if descriptor.prefix_sha256 != digest:
        raise HTTPException(
            status_code=422,
            detail="cache prefix_sha256 does not match canonical messages",
        )
    key = os.environ.get("DS_CACHE_SALT_KEY", "").encode("utf-8")
    if len(key) < 32:
        raise HTTPException(
            status_code=503,
            detail="DS_CACHE_SALT_KEY must contain at least 32 bytes",
        )
    salt_input = (
        f"design-studio-cache/1\0{descriptor.mode}\0"
        f"{descriptor.scope_id}\0{digest}"
    ).encode("utf-8")
    salt = hmac.new(key, salt_input, hashlib.sha256).hexdigest()
    return salt, {
        "schema": "design-studio.inference-evidence/1",
        "cache_mode": descriptor.mode,
        "cache_applied": True,
        "prefix_message_count": descriptor.prefix_message_count,
        "prefix_sha256": digest,
        "scope_sha256": hashlib.sha256(
            descriptor.scope_id.encode("utf-8")
        ).hexdigest(),
    }


@app.post("/v1/chat")
async def chat(req: ChatReq) -> dict[str, Any]:
    cache_salt, inference_evidence = _cache_parameters(req.messages, req.cache)
    if MOCK:
        last = req.messages[-1]["content"] if req.messages else ""
        return {
            "role": "assistant",
            "content": f"[mock] received: {last[:200]}",
            "inference_evidence": inference_evidence,
            "token_usage": unavailable_usage(
                provider="mock", model=MODEL, purpose="design_chat",
                reason="mock inference has no tokenizer-backed provider usage",
            ),
        }
    base_url = os.environ.get("DS_VLLM_URL", "http://127.0.0.1:8001").rstrip("/")
    parsed = urlparse(base_url)
    allow_remote = os.environ.get("DS_VLLM_ALLOW_REMOTE", "0") == "1"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=503, detail="DS_VLLM_URL is invalid")
    if not allow_remote and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=503, detail="non-loopback vLLM URL is disabled")
    payload = {
        "model": MODEL,
        "messages": req.messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": req.enable_thinking},
    }
    if req.tools is not None:
        payload["tools"] = req.tools
    if cache_salt is not None:
        payload["cache_salt"] = cache_salt
    try:
        async with httpx.AsyncClient(
            timeout=float(os.environ.get("DS_VLLM_TIMEOUT_SECONDS", "180"))
        ) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
            completion = response.json()
        message = completion["choices"][0]["message"]
        message["inference_evidence"] = inference_evidence
        message["token_usage"] = normalize_usage(
            completion.get("usage"), provider="vllm", model=MODEL,
            purpose="design_chat", cache_requested=cache_salt is not None,
        )
        return message
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=503, detail=f"vLLM inference unavailable: {type(error).__name__}"
        ) from error


class SupportJobReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)


class ReferenceFormJobReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]
    active_revision: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)


@app.post("/v1/reference-form-jobs", status_code=202)
async def create_reference_form_job(
    req: ReferenceFormJobReq, background_tasks: BackgroundTasks, response: Response
) -> dict[str, Any]:
    """Create four sequential, deterministic candidates from an active revision."""
    try:
        job, created = reference_form_jobs.create(
            req.spec, req.active_revision, req.idempotency_key
        )
    except ReferenceFormJobError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not created:
        response.status_code = 200
    elif not MOCK:
        background_tasks.add_task(
            reference_form_jobs.enforce_production_gate, job["job_id"]
        )
    return job


@app.get("/v1/reference-form-jobs/{job_id}")
async def get_reference_form_job(job_id: str) -> dict[str, Any]:
    job = reference_form_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="reference-form job not found")
    return job


@app.get("/v1/reference-form-jobs/{job_id}/result")
async def get_reference_form_job_result(job_id: str) -> dict[str, Any]:
    status, result = reference_form_jobs.result(job_id)
    if status == "missing":
        raise HTTPException(status_code=404, detail="reference-form job not found")
    if status != "completed" or result is None:
        raise HTTPException(status_code=409, detail=f"reference-form job is {status}")
    return result


@app.post("/v1/reference-form-jobs/{job_id}/cancel")
async def cancel_reference_form_job(job_id: str) -> dict[str, Any]:
    job = reference_form_jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="reference-form job not found")
    return job


@app.get("/v1/reference-form-jobs/{job_id}/artifacts/{name}")
async def get_reference_form_artifact(job_id: str, name: str) -> Response:
    try:
        artifact = reference_form_jobs.artifact(job_id, name)
    except ReferenceFormJobError as error:
        raise HTTPException(status_code=404, detail="artifact not found") from error
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    data, media_type = artifact
    return Response(
        content=data,
        media_type=media_type,
        headers={"ETag": f'"sha256:{hashlib.sha256(data).hexdigest()}"'},
    )


@app.post("/v1/support-jobs", status_code=202)
async def create_support_job(
    req: SupportJobReq, background_tasks: BackgroundTasks, response: Response
) -> dict[str, Any]:
    try:
        job, created = support_jobs.create(req.spec, req.idempotency_key)
    except SupportJobError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not created:
        response.status_code = 200
    elif not MOCK:
        background_tasks.add_task(support_jobs.run_remote, job["job_id"])
    return job


@app.get("/v1/support-jobs/{job_id}")
async def get_support_job(job_id: str) -> dict[str, Any]:
    job = support_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="support job not found")
    return job


@app.get("/v1/support-jobs/{job_id}/result")
async def get_support_job_result(job_id: str) -> dict[str, Any]:
    status, result = support_jobs.result(job_id)
    if status == "missing":
        raise HTTPException(status_code=404, detail="support job not found")
    if status != "completed" or result is None:
        raise HTTPException(status_code=409, detail=f"support job is {status}")
    return result


@app.post("/v1/support-jobs/{job_id}/cancel")
async def cancel_support_job(job_id: str) -> dict[str, Any]:
    job = support_jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="support job not found")
    return job


class ProposeReq(BaseModel):
    context: dict[str, Any] = {}
    kind: str


@app.post("/v1/propose")
async def propose(req: ProposeReq) -> dict[str, Any]:
    """Return a structurally checked v2 CAD proposal, never host authorization.

    The request context is still untrusted model/client input.  Mandatory gates
    and current document/host revisions are deliberately unavailable here; the
    Design Studio host supplies those only when authorizing a commit.
    """
    if req.kind != "geometry":
        raise HTTPException(
            status_code=422,
            detail="proposal/2 currently supports only typed CAD geometry corrections",
        )
    context = req.context
    try:
        proposal = build_replace_line_constraints_proposal(
            document_id=context["document_id"],
            base_revision=context["base_revision"],
            base_sha256=context["base_sha256"],
            object_id=context["object_id"],
            sketch_id=context["sketch_id"],
            host_geometry_id=context["host_geometry_id"],
            geometry_index=context["geometry_index"],
            host_geometry_revision=context["host_geometry_revision"],
            sketch_revision=context["sketch_revision"],
            target_start_nm=context["target_start_nm"],
            target_end_nm=context["target_end_nm"],
            session_id=context["session_id"],
            session_revision=context["session_revision"],
            source_sha256=context["source_sha256"],
            viewbox_id=context["viewbox_id"],
            element_id=context["element_id"],
            deviation_id=context["deviation_id"],
            target_ids=context["target_ids"],
            source=f"ai:{MODEL}@mock" if MOCK else f"ai:{MODEL}",
            confidence=0.5,
            prompt_id=context.get("prompt_id"),
            proposal_id=context.get("proposal_id"),
        )
    except (KeyError, ProposalBuildError, TypeError) as error:
        raise HTTPException(status_code=422, detail=f"invalid geometry proposal context: {error}") from error

    structurally_valid, reasons = validate_proposal_structure(proposal)
    return {
        "proposal": proposal,
        "structurally_valid": structurally_valid,
        "commit_authorized": False,
        "reasons": reasons + ["trusted host mutation context and gate results are required"],
    }


class SurrogateReq(BaseModel):
    model_id: str
    inputs: dict[str, Any]


@app.post("/v1/surrogate")
async def surrogate(req: SurrogateReq) -> dict[str, Any]:
    # A surrogate ALWAYS returns an error bound; the UI must show it and offer an
    # exact re-solve. Mock returns a zeroed prediction with a wide bound.
    return {"outputs": {"value": 0.0, "model_id": req.model_id}, "error_bound": 0.05}


class Generate3DReq(BaseModel):
    image_base64: str | None = None
    # Final overall mesh bounds, not body-only package dimensions.
    target_dimensions_mm: tuple[float, float, float]
    seed: int = 0


@app.post("/v1/generate3d")
async def generate3d(req: Generate3DReq) -> dict[str, Any]:
    """Generate normalized shape geometry, then recover absolute scale."""
    import base64

    if any(value <= 0 or value > 10_000 for value in req.target_dimensions_mm):
        raise HTTPException(status_code=422, detail="target_dimensions_mm values must be in (0, 10000]")
    try:
        result = shape_backend.generate(req.image_base64, req.target_dimensions_mm, req.seed)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "model": SHAPE_MODEL if not MOCK else "mock:dimensioned-box",
        "mock": MOCK,
        "format": result.format,
        "dimensions_unit": "mm",
        "mesh_coordinate_unit": result.coordinate_unit,
        "dimensions_mm": result.dimensions_mm,
        "mesh_base64": base64.b64encode(result.data).decode("ascii"),
    }
