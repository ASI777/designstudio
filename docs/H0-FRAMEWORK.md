# Horizon 0 — Foundation Framework

**Parent:** `docs/ROADMAP-DESIGN-PHYSICS-ENGINE.md` (Horizon 0).
**Purpose:** the concrete engineering design for the foundation layer — *one
mechatronic model, a real GPU viewport, interactive physics, and the AI/solver
spine on AMD MI300X* — defined precisely enough that the work can be built in
parallel and merged unit-by-unit.

H0 ships **foundations, not finished features**: each unit is a mergeable
skeleton (working code + tests + a smoke check) that the H1+ features build on.

---

## 1. Compute model (MI300X / AMD cloud)

The desktop app is a **thin client**; all heavy compute is a **containerized
service** on AMD Instinct **MI300X** (ROCm 7 / HIP).

```
┌───────────────────────────┐        gRPC/REST         ┌──────────────────────────────┐
│  Design Studio (Qt client)│  ───────────────────────▶│  AMD cloud · MI300X (ROCm)   │
│  • RHI/Vulkan viewport     │                          │  • ai-gateway (vLLM-on-ROCm) │
│  • Jolt real-time (local)  │◀──────────────────────── │     70B-class copilot model  │
│  • thin solver client      │   proposals · results    │  • solver-svc (HIP/rocSPARSE)│
└───────────────────────────┘                          │  • surrogate-train (PyTorch) │
                                                        └──────────────────────────────┘
```

Why this shape, given MI300X's 192 GB HBM3 / 5.3 TB/s:
- **Self-host the copilot.** A 70B-class model fits on **one** MI300X (405B on an
  8× node); we run it in our VPC for privacy + cost control instead of per-token API.
- **GPU-accelerate the solvers.** FEA/EM/MoM assembly+solve via **rocSPARSE /
  rocSOLVER / rocALUTION** (PETSc/MFEM expose HIP backends) — the same accelerator
  trains the **surrogates** (PyTorch-on-ROCm) on our own solver outputs.
- **Differentiable FEA on-GPU** (JAX-SSO-style) is what makes gradient-based
  generative design (H1.6) tractable later — designed for now, not built yet.
- **Real-time physics stays local** (Jolt on the client CPU/GPU) — interaction
  latency must not depend on the network; sign-off solves go to the cloud.

> Boundary: the desktop CMake build never links ROCm. ROCm lives only in the
> service containers (ROCm base image). The client talks to them over the wire.

---

## 2. Architecture & dependency DAG

H0 has one real dependency: **the contracts gate everything**. So we define the
shared contracts first (Stage A), then fan out (Stage B) in parallel.

```
            ┌───────────────────────── Stage A (must land first) ─────────────────────────┐
            │  U1  CONTRACTS: data-model schema v3 · C-API headers · ProposalContract ·     │
            │       ai-gateway API spec   (interfaces only — no implementation)             │
            └───────────────┬───────────────┬───────────────┬───────────────┬──────────────┘
                            │               │               │               │
            ┌───────────────▼──┐ ┌──────────▼─────┐ ┌────────▼──────┐ ┌──────▼───────────┐
   Stage B  │ U2 data model    │ │ U3 OCCT+STEP   │ │ U4 RHI viewport│ │ U6 AI gateway +  │
 (parallel) │   load/save/migr │ │   +tessellate  │ │   +scene graph │ │   ProposalContract│
            └──────────────────┘ └────────┬───────┘ └────────▲──────┘ └──────────────────┘
                                          │  DcMesh          │ meshes
                                          └──────────────────┘
                                 ┌────────────────────────┐
                                 │ U5 Jolt real-time      │  (consumes data-model joints,
                                 │   (one body + 1 joint) │   emits transforms to U4)
                                 └────────────────────────┘
```

Once U1 lands, **U2/U3/U4/U5/U6 are independent PRs** (each builds against the
U1 headers/schemas, stubbing what it doesn't own). U4 renders whatever meshes
U3 produces, but compiles and smoke-tests against a stub mesh until U3 merges.

---

## 3. Shared contracts (Stage A / U1)

### 3.1 Mechatronic document schema v3 (extends `.dsproj` v2)
Language-neutral JSON, git-friendly, atomic writes, **v2→v3 migration**.

```jsonc
{
  "format": "design-studio.project/3",
  "units": "nm",                       // mechanical + electronic share nm integers
  "materials": [                        // ONE library, multi-domain properties
    { "id": "AL6061", "mech": { "E_GPa": 68.9, "nu": 0.33, "yield_MPa": 276,
                                "fatigue": "..." }, "thermal": {...} } ],
  "parts":   [ { "id": "p1", "kind": "brep", "shape": "shapes/bracket.brep",
                 "material": "AL6061", "xform": [16 floats] } ],
  "board":   { "$ref": "design-studio.project/2 board object (unchanged)" },
  "assembly":{ "tree": [...], "joints": [ { "kind": "revolute", "a": "p1",
                 "b": "p2", "axis": [...], "limits": [...] } ] },
  "constraints": [ { "kind": "mate|fit|keepout|thermal|emi", ... } ],
  "tolerances":  [ ... ],
  "rationale":   [ { "ts": "...", "intent": "...", "alternatives": [...],
                     "gate": "drc|fea|manual", "source": "human|ai:<model>" } ]
}
```

### 3.2 C-API additions (header sketch — `core/include/designcore/c_api.h`)
Same ABI discipline (opaque handles, plain structs, `DcStatus`, two-phase fetch):
```c
/* document */    DcStatus dc_doc_open(const char* path, DcDocHandle* out);
                  DcStatus dc_doc_save(DcDocHandle, const char* path);
                  int      dc_doc_version(DcDocHandle);
/* geometry */    DcStatus dc_brep_load_step(const char* path, DcShapeHandle* out);
                  DcStatus dc_brep_tessellate(DcShapeHandle, double deflection,
                                              DcMesh* out);   /* two-phase fetch */
                  DcStatus dc_assembly_flatten(DcDocHandle, DcMeshList* out);
/* realtime  */   DcStatus dc_rt_world_new(DcDocHandle, DcRtHandle* out);
                  DcStatus dc_rt_step(DcRtHandle, double dt);
                  DcStatus dc_rt_body_xform(DcRtHandle, const char* partId, double* xf16);
/* DcMesh = { int64 nverts, ntris; const float* xyz; const float* nrm; const int* idx; } */
```

### 3.3 CAD-assist evidence
The CAD Visual Helper exports `urn:design-studio:schema:cad-assist-session:1`.
The Qt application independently validates the complete contract and binds it
to the exact saved project using `document_id`, `base_revision`, and
`base_sha256`. Valid evidence is displayed in a read-only dock and has no
ProjectModel mutation surface. See `docs/CAD-ASSIST-INTEGRATION.md`.

### 3.4 ProposalContract (generalize `DatasheetImport` trust boundary)
```jsonc
{ "schema": "design-studio.proposal/2",
  "proposal_id": "<uuid>", "idempotency_key": "<sha256>",
  "kind": "geometry_correction",
  "document": { "id": "...", "base_revision": 42, "base_sha256": "..." },
  "operation": { "op": "replace_line_constraints", /* stable host target,
                   host/sketch revisions, checked int64 nm coordinates */ },
  "evidence": { /* stable CAD-assist session/element/deviation/target IDs */ },
  "provenance": { "source": "ai:<model>@<rev>", "confidence": 0.0 } }
```
A deterministic **host validator** accepts/rejects; accepted proposals commit to
the model **and** append a `rationale` entry. Mandatory gates are selected by
trusted operation policy and supplied out-of-band with current host state; a
proposer can never weaken them. Proposal v1 is retained for historical display
only and is never commit-eligible. AI never writes the model directly.

### 3.5 ai-gateway API (REST/gRPC, served on MI300X)
```
POST /v1/chat        { messages, tools }            -> tool-use stream
POST /v1/propose     { context, kind }              -> ProposalContract
POST /v1/surrogate   { model_id, inputs }           -> { outputs, error_bound }
GET  /v1/health      -> { gpu: "MI300X", rocm, models_loaded }
```

---

## 4. Stage B units (parallelizable after U1)

| Unit | Owns (files) | Builds | Smoke / e2e check |
|---|---|---|---|
| **U2 Data model** | `core/model/document.{h,cpp}`, `Model/MechatronicDocument.cs` (or Qt loader), migration | v3 load/save, v2→v3 migration, rationale append | Round-trip test: load v2 `.dsproj` → save v3 → reload, assert equality + migration |
| **U3 OCCT + mesh** | `core/geometry/{brep_kernel,step_io,tessellate}.cpp`, CMake `USE_OCCT` default ON via vcpkg | STEP load, healing, `dc_brep_tessellate`, `dc_assembly_flatten` | Load a known STEP, assert tri count > 0 and bbox within tolerance (ctest) |
| **U4 RHI viewport** | `app/QtDesignStudio/render/{RhiViewport,SceneGraph,MeshUpload,Camera}.cpp` | QRhi (Vulkan) persistent scene graph, instanced draw, GPU culling/LOD stub, PBR | Offscreen RHI: upload a stub `DcMesh`, render 1 frame to PNG, assert non-empty (like the QtSvg probe pattern) |
| **U5 Jolt real-time** | `core/realtime/{jolt_world,body_map}.cpp`, Jolt via vcpkg, `dc_rt_*` | parts→bodies, joints→constraints, `dc_rt_step` | Drop one body under gravity N steps; assert z decreased; revolute joint holds (ctest) |
| **U6 AI spine** | `services/ai-gateway/` (Python/ROCm, vLLM), `Dockerfile.rocm`, `app/.../ai/ProposalContract.{cs,cpp}` + validator | vLLM-on-ROCm container, REST gateway, ProposalContract validator | `docker build` the ROCm image; `curl /v1/health` in CI mock mode; validator unit tests accept/reject golden proposals |

Sizing: each is a **foundation skeleton** (compiles, tests pass, one real
capability demonstrated) — roughly uniform, independently mergeable.

---

## 5. Build & dependency wiring

- **Desktop (CMake + vcpkg):** OCCT (**default ON**), Jolt, Qt6 + SvgWidgets +
  RHI/Vulkan, Eigen. No ROCm. CI path stays Linux core + managed tests.
- **Service containers (ROCm base image):** `ai-gateway` (vLLM + PyTorch-ROCm,
  70B-class model), `solver-svc` (HIP + rocSPARSE/rocALUTION; H1 onward),
  `surrogate-train` (PyTorch-ROCm). Deployed to AMD cloud; app holds only an
  endpoint + auth.
- **Licensing:** OCCT LGPL, Jolt MIT, Chrono BSD (H1) — all link-safe; **no GPL
  solver** enters the binary (per roadmap Open Decision #3).

---

## 6. End-to-end verification recipe (for workers)

Each worker must, after implementing:
1. **Build:** `cmake -B core/build_linux -S core -DCMAKE_BUILD_TYPE=Release [-DUSE_OCCT=ON]` then `cmake --build core/build_linux -j`; for app units `cmake --build build_linux --target DesignStudioQt -j`.
2. **Unit tests:** `ctest --test-dir core/build_linux --output-on-failure` and, for managed, `dotnet test tests/DesignStudio.Tests/`.
3. **Unit smoke (from the table above):** the specific assertion for the unit
   (round-trip / tri-count / offscreen PNG non-empty / body-drops / curl health).
   GUI units use the **offscreen pattern** (`QT_QPA_PLATFORM=offscreen`) — no
   interactive display needed, mirroring the QtSvg-probe approach already used.
4. **Skip note:** U6's GPU path can't run on CI (no MI300X) — verify the
   container **builds** and the gateway answers in **mock mode**; the real ROCm
   smoke runs on the AMD-cloud target, not in the PR.

---

## 7. Worker instruction template (Stage B)

```
After you finish implementing the change:
1. Code review — invoke the `Skill` tool with `skill: "code-review"`; fix findings.
2. Unit tests — build + run ctest / dotnet test (commands in §6). Fix failures.
3. End-to-end — run this unit's smoke check from H0-FRAMEWORK.md §4/§6. GUI →
   offscreen; U6 → container build + mock health. Skip the real-GPU step (no
   MI300X in CI) and say so.
4. Commit & push — clear message; push branch; `gh pr create` with a descriptive
   title. If gh/push fails, note it.
5. Report — end with `PR: <url>` (or `PR: none — <reason>`).
```

---

## 8. Execution plan (two stages)

- **Stage A — U1 contracts (1 unit, must land first).** Lands the schema files,
  C-API header stubs, ProposalContract schema, and gateway API spec. Small but
  gating; everything else compiles against it.
- **Stage B — U2…U6 (5 units, parallel).** Spawned as isolated-worktree
  background agents once U1 is merged (or once the U1 headers are shared), each
  producing its own PR per §7.

This is the responsible decomposition: contracts first, then a true parallel
fan-out — not five agents racing to invent the same interfaces.

### Open calls before spawning
1. Approve the **contracts in §3** (schema shapes, C-API names) — they're the
   API every other unit commits to.
2. Confirm **Stage A first**, or fold U1 into the framework and spawn all six.
3. Confirm the **dual front-end** call (roadmap Open Decision #1) for U2/U4:
   target **Qt** for new H0 code, or also wire WPF?

### Sources
MI300X / ROCm: amd.com (vLLM×MI300X), rocm.docs.amd.com (vLLM, rocSPARSE),
rocm.blogs.amd.com (ROCm 7.0), localaimaster.com (MI300X deep dive). Solvers:
ROCm/rocALUTION, PETSc/MFEM HIP backends; arxiv 2407.20026 (differentiable FEA).
Internal: `ROADMAP-DESIGN-PHYSICS-ENGINE.md`, `physics-engine.md`.
