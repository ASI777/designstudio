# Design Studio — Master Roadmap: The Mechatronic Design & Physics Engine

**Status:** Strategic roadmap (v1, 2026-06).
**Scope:** A graphics-cum-physics engine on the existing Design Studio stack that
brings *design thinking* to **unified mechatronic** (mechanical + electronic)
engineering, with ~70% of the creation work AI-assisted, and a product that is
both **aesthetically design-led and practically engineering-sound**.

This document is the umbrella plan. It does **not** replace the existing
electronics simulation roadmaps — it absorbs them as one workstream:

- `docs/ROADMAP-SIMULATION.md` → **Electronic Physics** workstream (SI/PI, E1–E9).
- `docs/ROADMAP-HIGHEND.md`, `docs/ROADMAP-ADVANCED.md` → high-speed sign-off tiers.
- `docs/physics-engine.md` → the shipped closed-form physics/math engine (baseline).

---

## 0. Thesis

> Most CAD/EDA tools are *drafting* tools with analysis bolted on. We build the
> inverse: a **design-and-physics environment** where the simulation, the
> aesthetics, and the AI co-pilot are first-class, and the drawing is a
> by-product of decisions the engine helped you make well.

Three bets, balanced (per the agreed scope — all three, not one):

1. **Real-time** — a GPU viewport with interactive physics so a designer *feels*
   the mechanism/board behave while editing (game-engine immediacy).
2. **High-fidelity** — analysis-grade solvers (FEA, thermal, multibody, SI/PI)
   so the same model that looks good also passes sign-off.
3. **AI-native** — generative proposals, ML surrogates for instant feedback, and
   an LLM copilot that turns intent into geometry — all behind validation gates.

The differentiator is **integration**: mechanical and electronic in one model,
design-thinking method in the workflow, and AI threaded through every stage —
not a pile of disconnected tools.

---

## 1. Where we are (honest current state)

| Layer | Built today | Gap for the vision |
|---|---|---|
| **Geometry** | nm-integer 2D PCB model (`core/pcb_model`); OCCT 3D B-rep **optional** (`core/src/occt_kernel.cpp`, `USE_OCCT=ON`) | OCCT must become **mandatory & central**; need meshing, assemblies, mechanical solids, parametric history |
| **Physics** | Closed-form EE engine (`physics.cpp`, 298 lines): microstrip/stripline Z₀, losses, IPC current, via parasitics — reference-grade, CI-tested | No mechanical physics (stress/fatigue/kinematics), no field solvers (on the SI/PI roadmap), no real-time rigid-body |
| **Routing/Rules** | Multi-layer A* router, 13-family DRC, copper pour, spatial index | EE-only; no mechanical constraints/tolerances |
| **Graphics** | Qt 2D canvas (`FpCanvas`), `Board3DBuilder` regenerates a scene per visit | No persistent GPU scene graph, no LOD/culling, no PBR/real-time physics viewport |
| **App** | Dual front-ends: C# WPF (`BoardDocument`, `FabExporter`) + Qt (`QtDesignStudio`, 16 cpp) | Two UIs to converge; data model is PCB-centric |
| **AI** | Datasheet→footprint vision loop (`DatasheetImport.cs` *trust boundary*), `SiPiAdvisor` (deterministic recommender), circuit-advisor JSON contract | No generative geometry, no ML surrogates, no general design copilot, no model-serving infra |
| **Method/UX** | Functional, engineering-first | No design-thinking workflow, no rationale capture, aesthetics ad-hoc |

**Reusable assets to build on (not throw away):** the C-API boundary discipline,
the *grounded-in-references + canonical CI tests* ethos (`physics-engine.md`), the
parametric library pattern (`ParametricFootprints`, IPC-7351 generators), and the
**validation-gate pattern** (`DatasheetImport` validates AI output before it
enters the model) — that last one is the template for the entire AI strategy.

---

## 2. Design-thinking foundation (method embedded in the product)

The five reference texts are not decoration — each maps to a concrete product
principle and, where possible, a buildable feature.

| Source | Core idea | Product principle | Concrete feature |
|---|---|---|---|
| **Lawson, *How Designers Think*** | Design is non-linear; problem & solution **co-evolve**; designers work from "primary generators," not fixed specs | Never force a linear spec→CAD pipeline. Make exploration cheap and reversible; AI *proposes*, human *disposes* | Branchable design states + cheap undo; a **design-rationale trail** recording why each decision was made |
| **Cross, *Design Thinking*** | "Designerly ways of knowing": **abductive** reasoning, reflective practice, expertise as pattern libraries | AI's job is abductive *proposal generation*; expert heuristics become reusable deterministic rules | Generative design (abduction) + a rules engine extending the `SiPiAdvisor` pattern to mechanical |
| **IDEO, *Field Guide to HCD*** | Inspiration → Ideation → Implementation; the **desirability / feasibility / viability** lenses; start from real human need | Workflow has explicit phases; the three lenses are gates; every project starts from a *use-in-the-real-world* brief | A project "brief" front door; phase view; a DFV scorecard the copilot keeps updated |
| **Shigley's *Mechanical Engineering Design*** | The engineering design process; factor of safety; failure theories (von Mises, Goodman fatigue); machine-element design | Mechanical analysis must be as rigorous & reference-cited as the EE engine | **Mechanical physics engine** + a **parametric machine-element library** (shafts, gears, bearings, springs, fasteners) |
| **Sclater & Chironis, *Mechanisms & Mechanical Devices Sourcebook*** | A catalog of reusable mechanisms (linkages, cams, ratchets, clutches, gears) | Mechanisms are reusable "components," exactly like footprints | **Parametric mechanism library** + AI mechanism selection ("rotary→linear under these constraints → here are 4 candidates") |

**The synthesis:** the mechanism library is to mechanical what the footprint
library is to electronic; the mechanical physics engine is to Shigley what
`physics.cpp` is to microwave EE; the HCD phases + DFV lenses become the app's
top-level workflow; and Lawson/Cross tell us the AI must support *iterative
co-evolution*, never one-shot generation.

---

## 3. Target architecture

Five layers over one mechatronic data model. Everything crosses the C-API
boundary as today (no C++ types/exceptions/STL across the ABI).

```
┌──────────────────────────────────────────────────────────────────┐
│ L4  AI LAYER   generative design · ML surrogates · LLM copilot ·   │
│                datasheet/intent extraction   (all behind gates)    │
├──────────────────────────────────────────────────────────────────┤
│ L3  GRAPHICS   Qt RHI → Vulkan scene graph · GPU culling/LOD ·     │
│                PBR + optional ray-traced preview · interactive     │
├──────────────────────────────────────────────────────────────────┤
│ L2  PHYSICS    real-time (Jolt) ‖ mechanical CAE (Chrono+FEA) ‖    │
│                electronic (physics.cpp + SI/PI roadmap)            │
├──────────────────────────────────────────────────────────────────┤
│ L1  GEOMETRY   OCCT B-rep (mechanical) + nm-integer 2D (PCB) +     │
│                meshing · assemblies · parametric history          │
├──────────────────────────────────────────────────────────────────┤
│ L0  DATA MODEL  one document: parts, mechanisms, nets, materials,  │
│                 constraints, tolerances, design-rationale trail    │
└──────────────────────────────────────────────────────────────────┘
```

Key architectural rules:
- **Real-time and high-fidelity are different solvers on the same geometry.**
  Jolt drives the interactive viewport (approximate, 60 fps); Chrono/FEA/MoM run
  on demand for sign-off (accurate, seconds–minutes). They never share a code
  path — they share the *model*.
- **AI never writes to the model directly.** It emits a typed proposal that a
  deterministic validator accepts/rejects (the `DatasheetImport` pattern, generalized
  into a `ProposalContract` framework). Physics sign-off stays deterministic.
- **One data model, two domains.** Mechanical solids and electronic nets coexist
  with cross-domain constraints (a connector's mechanical mate *is* its electrical
  pin map; enclosure walls *are* thermal/EMI boundaries).

---

## 4. Technology choices (with rationale, alternatives, risk)

| Concern | Choice | Why | Alternatives considered | Risk |
|---|---|---|---|---|
| **Geometry kernel** | **OCCT** (already integrated, make mandatory) | Open, B-rep + STEP, meshing; we already have `occt_kernel.cpp` | Parasolid/ACIS (commercial $$), CGAL (no B-rep history) | OCCT robustness on degenerate ops; mitigate with healing + guards |
| **Real-time physics** | **Jolt Physics** | Modern C++, multithreaded, deterministic, permissive, clean to embed | PhysX (heavier, NVIDIA), Bullet (aging) | Single-thread perf < PhysX; fine for design-scale scenes |
| **Mechanical CAE (MBD/kinematics)** | **Project Chrono** (BSD-3) | C++ multibody + FEA + FSI in one library; mechanism dynamics out of the box | MBDyn, custom | Heavy dependency; integrate as optional module like OCCT |
| **Structural/thermal FEA** | **CalculiX** kernel / Chrono-FEA, **+ ML surrogate** | Established solver; surrogate gives interactive feedback (≈50× faster, <5% error per literature) | Code_Aster (GPL friction), in-house | Meshing pipeline is the hard part; surrogate accuracy must be bounded & flagged |
| **Electronic field solvers** | Per `ROADMAP-SIMULATION.md` (MoM 2D, via cavity, IBIS-AMI) | Already planned & scoped | — | Already risk-assessed there |
| **Rendering backend** | **Qt RHI** → Vulkan (Metal/D3D free) | We're on Qt; RHI abstracts Vulkan/Metal/D3D; Vulkan proven on large CAD assemblies (Autodesk VRED 2026) | Raw Vulkan (more control, more cost), bgfx | RHI maturity for heavy CAD; borrow CADR-style GPU culling/LOD patterns |
| **AI — geometry generation** | LLM → parametric program (CadQuery/our DSL) → OCCT, **validated** | Text-to-CAD via code-gen is the proven 2025 pattern; stays inside a real kernel | Direct mesh/B-rep generation (degenerate-prone) | LLM emits invalid geometry on edge cases → the validator catches it |
| **AI — fast feedback** | **ML surrogate models** trained on our own solver outputs | Interactive "good enough" stress/EM/thermal while editing | None (full solve is too slow for interaction) | Surrogate drift; always show confidence + offer exact re-solve |
| **AI — generative/topology** | Topology optimization (SIMP/level-set) + RL-guided search, FEA/surrogate in loop | Mature; ~40% weight savings reported under stress/displacement constraints | Pure generative nets | Manufacturability of results; constrain to fabricable space |
| **AI — copilot/orchestration** | LLM with tool-use over a typed action API + the deterministic advisors | Reuses circuit-advisor contract + `SiPiAdvisor`; latest Claude models | — | Hallucinated engineering claims → never let LLM sign off; cite or defer |
| **Compute / hosting** | **AMD Instinct MI300X on AMD cloud** (ROCm 7 / HIP) | 192 GB HBM3 / 5.3 TB/s self-hosts 70B-class models on **one** GPU (405B on 8×); GPU-accelerates solvers (rocSPARSE/rocSOLVER/rocALUTION) and surrogate training; differentiable-FEA on-GPU for generative design | NVIDIA/CUDA (vendor lock), local RTX-4050 (too small) | ROCm/HIP porting maturity vs CUDA; mitigate via vLLM-on-ROCm + PETSc/MFEM HIP backends |

---

## 5. The 70% AI strategy (honest)

**Interpretation:** AI *assists or accelerates ~70% of the creation effort*;
humans steer, judge, and own the ~30% that is novel decisions and certified
sign-off. AI is a copilot across the funnel, never the certifier.

| Design-funnel stage | AI role (proposes) | Human role (disposes) | Validation gate |
|---|---|---|---|
| **Brief / need (Inspiration)** | Summarize requirements, surface analogous prior designs, draft DFV scorecard | Owns the problem framing | — (advisory) |
| **Concept (Ideation)** | Generative concepts; mechanism selection (Sclater); intent→geometry | Picks/edits; co-evolves problem↔solution (Lawson/Cross) | Geometry validator (kernel-valid, manufacturable) |
| **Detailed design** | Auto-place, auto-route, parametric sizing (Shigley element design), datasheet→footprint | Approves tolerances, constraints | DRC/rules engine; tolerance checks |
| **Analysis** | ML surrogates for instant stress/EM/thermal; flag hot spots | Requests exact solve on critical items | **Deterministic solver** is the only sign-off |
| **Optimization** | Topology/parameter optimization under constraints | Sets objectives & limits; chooses among results | FEA-in-loop constraint satisfaction |
| **Documentation/Fab** | Generate BOM, drawings, fab notes, rationale narrative | Reviews & releases | Export validators (Gerber/STEP/ODB++) |

**Non-negotiables:**
- Physics/safety **sign-off is deterministic and reference-cited**, like today's
  `physics-engine.md`. An LLM may *explain* a result; it may never *be* the result.
- Every AI proposal carries provenance + confidence and passes a typed contract
  before touching the model (generalized `DatasheetImport` trust boundary).
- Surrogates always display error bounds and a one-click "re-solve exactly."

**Compute reality (resolves Open Decision #2):** AI and heavy solvers run on
**AMD Instinct MI300X (AMD cloud, ROCm/HIP)**, not the local RTX-4050. The 192 GB
HBM3 changes the strategy: we **self-host** 70B-class copilot/advisor models in
our own VPC (privacy + cost control), **GPU-accelerate** the FEA/EM/MoM solvers
and surrogate *training* via HIP/rocSPARSE, and run **differentiable FEA** for
gradient-based generative design — all on one accelerator family. The app stays a
thin client; compute is a service. See `docs/H0-FRAMEWORK.md` for the serving
stack and the on-GPU solver plan.

---

## 6. Phased roadmap (Horizons)

Effort scale (matching `ROADMAP-SIMULATION.md`): **M** = weeks, **L** = months,
**XL** = quarters. The Electronic-Physics workstream (E1–E9) runs in parallel
on its own schedule; milestones below are the *new* mechatronic/graphics/AI work.

### Horizon 0 — Foundations (≈1–2 quarters)
*Goal: one model, a real GPU viewport, interactive physics, and the AI spine.*

| # | Unit | Deliverable | Exit criterion | Effort |
|---|---|---|---|---|
| H0.1 | **Mechatronic data model** | One document spanning mechanical solids + electronic nets + constraints + rationale trail; versioned, git-friendly | A board *and* an enclosure load/save in one project; cross-refs resolve | L |
| H0.2 | **OCCT mandatory + assemblies** | Promote OCCT from optional to core; assembly tree, STEP I/O, healing, meshing service | Import a STEP assembly, tessellate, render | L |
| H0.3 | **Qt RHI/Vulkan viewport** | Persistent scene graph, GPU culling/LOD (CADR-style), PBR | 100k-triangle assembly at 60 fps; layer-aware like `PcbCanvas` | L |
| H0.4 | **Real-time physics (Jolt)** | Interactive rigid-body: drag a part, feel collisions/joints | Mechanism moves under mouse at 60 fps | M |
| H0.5 | **AI spine** | `ProposalContract` framework (generalized trust boundary) + model-serving (local + API) + rationale store | Any AI output round-trips through a validator before commit | M |
| H0.6 | *(parallel)* SI/PI **S1**: geometry exchange + solver infra | Per `ROADMAP-SIMULATION.md` | Geometry round-trips to openEMS; `.s4p` imports | — |

### Horizon 1 — Mechanical engine + first copilots (≈2 quarters)
*Goal: real mechanical analysis and the first genuinely useful AI creation tools.*

| # | Unit | Deliverable | Exit criterion | Effort |
|---|---|---|---|---|
| H1.1 | **Mechanical physics engine** | `core/mech_physics`: stress/strain, factor of safety, Goodman fatigue, failure theories (Shigley), reference-cited + CI canonical tests | Cantilever/shaft cases within textbook tolerance | L |
| H1.2 | **Machine-element library** | Parametric shafts, gears, bearings, springs, fasteners (Shigley) — the mechanical `ParametricFootprints` | Place a parametric spur gear set; auto-sized to load | L |
| H1.3 | **Mechanism library + kinematics** | Sclater catalog as parametric mechanisms; Chrono kinematics/MBD | 4-bar linkage simulates; motion envelope plotted | L |
| H1.4 | **Structural FEA + surrogate** | CalculiX/Chrono-FEA meshing pipeline; train v1 surrogate on its outputs | Exact FEA matches benchmark; surrogate <5% on trained class, with error bar | XL |
| H1.5 | **Text→geometry copilot v1** | Intent → parametric program → OCCT, validated | "M3 bracket, 40×20, 2 holes" → valid solid | L |
| H1.6 | **Generative design v1** | Topology optimization under stress/displacement, FEA/surrogate in loop | Bracket mass −30% meeting constraints | L |

### Horizon 2 — Mechatronic co-design + deep AI (≈2–3 quarters)
*Goal: the integration nobody else has — mechanical and electronic as one.*

| # | Unit | Deliverable | Exit criterion | Effort |
|---|---|---|---|---|
| H2.1 | **Cross-domain constraints** | Enclosure↔PCB fit, connector mate = pin map, keep-outs, thermal/EMI boundaries shared | Move a PCB → enclosure boss + connector cutout follow | L |
| H2.2 | **Electro-thermal co-sim** | Couple EE power loss (PDN/IR) → thermal FEA → mechanical expansion | Hot component warps detected; board+enclosure thermal map | XL |
| H2.3 | **Multi-physics generative** | Optimize across mechanical + thermal + EE objectives | Heatsink-bracket co-optimized for stiffness + thermal | XL |
| H2.4 | **Self-trained surrogates** | Train surrogates on the platform's accumulated solver runs | Surrogate library covers common part classes with bounds | L |
| H2.5 | **Design-thinking workflow** | HCD phases, DFV scorecard, rationale narrative auto-drafted | A project walks brief→concept→detail→sign-off with rationale | L |

### Horizon 3 — Production sign-off + ecosystem (≈2 quarters)
*Goal: trustworthy enough to ship real hardware from.*

| # | Unit | Deliverable | Exit criterion | Effort |
|---|---|---|---|---|
| H3.1 | **Validation & certification suite** | Expanded canonical tests across all engines; accuracy datasheets per solver | Every solver has a published accuracy class | L |
| H3.2 | **Exchange & interop** | STEP AP242, ODB++, Touchstone, glTF, drawing/BOM export | Round-trip to a commercial MCAD + ECAD tool | L |
| H3.3 | **Collaboration / digital twin** | Multi-user, design-state branching, as-built twin hooks | Two designers branch & merge a mechatronic project | XL |
| H3.4 | **UI convergence + aesthetics pass** | Converge WPF/Qt; design-system, motion, polish | One front-end; design-led look that still reads as engineering | L |

**Sequencing logic:** H0 makes the model/viewport/AI-spine real so everything
after is additive; H1 delivers the first standalone mechanical value + AI tools
people will actually use; H2 is the moat (integration + deep AI); H3 earns trust
for production use. The SI/PI workstream lands its own milestones throughout.

---

## 7. Mechatronic data model (the crux)

One versioned document (extend the `.dsproj` v2 philosophy: JSON, git-friendly,
atomic writes, migration). Entities:

- **Parts** (mechanical solids, OCCT B-rep refs) and **Footprints/Nets** (existing).
- **Mechanisms** (parametric, from the Sclater library) with joints & DOF.
- **Materials** unified (mechanical: E, ν, σ_y, fatigue; electronic: εr, tanδ,
  σ — one library, multi-domain properties).
- **Constraints & tolerances** (geometric, electrical, thermal) — cross-domain.
- **Design-rationale trail** (Lawson/Cross): every committed change records the
  intent, the alternatives considered, and which gate approved it. This is also
  the AI's audit log and the seed for the auto-drafted design narrative.

Invariant (carry over the existing discipline): the model is the single source
of truth; engines rebuild their native state from it before each run (the
`BoardDocument.RebuildNative` pattern), so UI/engine drift is impossible.

---

## 8. Validation & accuracy philosophy

Carry `physics-engine.md`'s ethos to every new engine:
- **Reference-cited:** each model names its source (Shigley eq./table, IEEE
  benchmark, IPC standard) — extend the references table per engine.
- **Canonical CI values:** every engine ships hand-verified cases that run in CI
  (cantilever deflection, gear bending stress, 4-bar kinematics, FEA benchmark).
- **Stated accuracy class:** reference-grade (≤1–2%), engineering (±5%), or
  estimate (±20%) — surfaced in the UI, never hidden.
- **Surrogates are flagged, never silent:** show error bound + exact re-solve.

---

## 9. Aesthetics & practicality (the "Design Studio" promise)

- A **design system** (typography, spacing, motion, a restrained palette) so the
  tool feels crafted — the IDEO *desirability* lens applied to our own product.
- The viewport is the hero: PBR materials, soft shadows, optional ray-traced
  preview, smooth camera — engineering that is pleasant to inhabit.
- But never style over substance: every beautiful view is backed by a real
  solver and real numbers. Aesthetics earns trust; physics keeps it.

---

## 10. Risks, anti-goals, scope discipline

**Anti-goals (explicitly out):**
- Beating SolidWorks/CATIA/Ansys/Cadence head-on on raw feature count. We win on
  *integration + AI + design-led workflow*, not parity.
- Full-wave 3D extraction of whole boards, die/package co-sim (already out per
  `ROADMAP-SIMULATION.md`); HPC-class CFD.
- LLM-as-certifier. Ever.

**Top risks & mitigations:**
1. *Over-scope / two-CAD-domains-at-once.* → Horizon gates with hard exit
   criteria; mechanical can ship value (H1) before full co-design (H2).
2. *OCCT/Chrono/Jolt integration weight.* → Optional-module pattern (like
   `USE_OCCT`); strict C-API isolation; one dependency promoted per horizon.
3. *Surrogate/LLM trust erosion.* → Validation gates + accuracy classes +
   deterministic sign-off are load-bearing, not optional.
4. *Dual front-end drift (WPF/Qt).* → Converge by H3.4; new work targets Qt.
5. *Meshing pipeline (the silent FEA blocker).* → Treat as its own H1.4 sub-track;
   reuse OCCT tessellation; fall back to exact solve only on clean meshes.

---

## 11. Team, build/buy, sequencing

- **Build:** the data model, the integration glue, the AI spine/validators, the
  mechanical physics *closed-forms* (Shigley), the libraries (mechanisms,
  elements), the UX/design system. These are our differentiators.
- **Buy/adopt (open):** OCCT (geometry), Jolt (real-time), Chrono + CalculiX
  (CAE), Qt RHI (rendering), Eigen/MKL (numerics). Don't rebuild solved kernels.
- **Skills needed:** C++ numerics/graphics, OCCT, ML (surrogates/generative),
  LLM tool-use orchestration, and — critically — an interaction/visual designer.
- **Minimum viable team:** the SI/PI roadmap notes ~quarters per tier for one
  strong engineer; this umbrella is a multi-engineer, multi-year program. Stage
  funding to horizon exit criteria, not calendar.

---

## 12. Definition of "production-level" & success metrics

A horizon is *production-level* when:
- Every solver in it has a **published accuracy class + CI canonical tests**.
- Every AI feature has a **validation gate** and provenance.
- The workflow it enables produces a real artifact that **passes the relevant
  standard** (DRC/IPC for EE; factor-of-safety/fatigue for mechanical; export
  validators for fab/STEP).

Product metrics: % of creation steps AI-assisted (target ≈70% by H2), time from
brief→validated concept, surrogate-vs-exact agreement rate, and "designs taken to
real fabrication" — the real-world-usefulness north star.

---

## 13. Open decisions (need a call before/within each horizon)

1. **Front-end convergence:** commit to Qt and sunset WPF, or keep both through
   H2? (Affects every UI deliverable.)
2. ~~**AI hosting**~~ **RESOLVED:** AMD Instinct **MI300X on AMD cloud** (ROCm/HIP).
   Self-hosted 70B-class models + GPU-accelerated solvers. See `H0-FRAMEWORK.md`.
3. **License posture:** Chrono (BSD) and OCCT (LGPL) are fine; confirm no GPL
   solver (Code_Aster) enters the link.
4. **First vertical:** which real product do we dogfood H1 on (a mechatronic
   device that exercises both domains)? Pick it now — it disciplines scope.

---

### Sources

Engine/tech research (2025–2026): Jolt/PhysX/Bullet comparison
(github.com/jrouwe/JoltPhysics), AI surrogates & generative design
(tgm.solutions, neuralconcept.com, arxiv 2006.02138, 2306.15166), text-to-CAD
(arxiv 2505.06507, 2511.06194, 2505.08137), rendering (khronos.org VRED-2026,
Qt RHI docs, github Rendering-FIT/CADR), CAE (projectchrono.org). Design &
engineering canon: Lawson *How Designers Think* (2005); Cross *Design Thinking*
(2023); IDEO.org *Field Guide to Human-Centered Design*; Shigley/Budynas
*Mechanical Engineering Design* (2024); Sclater & Chironis *Mechanisms &
Mechanical Devices Sourcebook* (2001). Internal: `docs/ROADMAP-SIMULATION.md`,
`docs/physics-engine.md`.
