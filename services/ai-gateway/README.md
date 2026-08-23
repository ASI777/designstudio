# AI Gateway (Horizon 0 / U6)

Thin inference gateway for Design Studio. Production target: **AMD Instinct
MI300X** (ROCm/HIP) in the AMD cloud, self-hosting Qwen via vLLM-on-ROCm.
The desktop app is a thin client and never links ROCm — it calls these
endpoints. Implements `docs/schemas/ai-gateway.openapi.yaml`.

## The trust boundary
The AI never writes the model directly. New CAD mutations use the revision-bound
**ProposalContract v2** (`docs/schemas/proposal-v2.schema.json`). The gateway
only checks its typed structure. `proposal.py` authorizes a mutation only when a
trusted host supplies the exact current document, sketch, and host-geometry
revisions, every gate result required by host-owned policy, and an atomic replay
guard. Gate names/results never come from the proposer. Proposal v1 remains
available only for rendering historical records and is always rejected for
commit. Physics/safety sign-off is never an LLM output.

## Run locally (mock mode — no GPU)
```bash
pip install -r requirements.txt
DS_AI_MOCK=1 uvicorn app:app --port 8000
curl localhost:8000/v1/health        # {"gpu":"none","mock":true,...}
```

## Tests (no GPU)
```bash
pytest -q          # or: python3 -c "import test_gateway as t; [getattr(t,f)() for f in dir(t) if f.startswith('test_')]"
```

## Production (MI300X)
```bash
docker compose --env-file cloud/mi300x-designstudio/.env \
  -f cloud/mi300x-designstudio/docker-compose.yml up -d --build
```
The Compose stack runs the GPU services separately and keeps the gateway on
loopback. The real vLLM and support-optimizer paths run only on the ROCm host;
CI exercises mock mode.

## Interaction support jobs

`POST /v1/support-jobs` accepts a locked, revision-bound
`design-studio.support-generation/1` specification. Jobs are idempotent and
cancellable. In production the gateway forwards immutable input to the
separate ROCm optimizer. Every result is marked non-authoritative and must be
reconstructed and checked by the FreeCAD host.

## Shape generation

`POST /v1/generate3d` is available in the same contract. Mock mode returns a
deterministic binary STL box at the requested overall dimensions. The
shape-only ROCm image and fine-tuning workflow live in
`services/hunyuan3d-shape/`; production returns a Hunyuan3D-generated GLB after
deterministic X/Y/Z scale recovery.

`POST /v1/reference-form-jobs` is the progressive workflow. It rejects stale
active revisions, unsafe asset paths, missing measurements and pixel-inferred
scale; returns four sequential seeded candidates with six orthographic
previews; and exposes status, result, cancellation and digest-checked artifact
downloads. One approved view routes to Hunyuan3D-2.1. Exactly six approved
cardinal silhouettes route to Hunyuan3D-Omni. Text intent is retained as
provenance and is explicitly marked `recorded_not_conditioned` for the pinned
Omni checkpoint.

Production reference-form generation remains fail-closed until the real ROCm
parity record exists. Do not set `DS_REFERENCE_FORM_ROCM_PARITY=pass` or
`DS_REFERENCE_FORM_WORKER_URL` merely to make health checks green; the worker
transfer adapter and model/container digests must be qualified first.
