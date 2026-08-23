# Design Studio — PCB + 3D CAD

> **Supported application:** the source-built FreeCAD 1.1.1 distribution with
> the native `DesignStudioGui` module. The Qt 6 executable remains a supported
> transitional smoke-test harness; the C# WPF client is legacy reference code.

Design Studio is a deterministic, multi-layer PCB design tool with a C++20
engine and a Qt 6 desktop application, built for the board classes that matter
in 2026: HDI smartphone
boards (microvias, tight clearances), AI-accelerator boards (length-matched
high-speed classes, dense fanout) and datacenter boards (high layer counts,
thousands of parts — the engine is spatially indexed and stays fast).

## FreeCAD-hosted product architecture

FreeCAD owns the mechanical CAD, enclosure, assembly, and STEP document. The
native `DesignStudioGui` module loads the existing Qt schematic, footprint, PCB,
routing, DRC, fabrication, and analysis widgets inside that same process and
main window.

The workbench in `freecad/DesignStudioWorkbench` binds a FreeCAD document to a
`.dsproj`, derives and locks a versioned mechanical contract, and synchronizes
the legal board outline, cutouts, mounting holes, fixed connector locations,
height zones, cooling keepouts, and service clearances. **Open Schematic and PCB
Editor** shows an in-process dock; it never launches a companion application.

See [`freecad/DesignStudioWorkbench/README.md`](freecad/DesignStudioWorkbench/README.md)
for installation and the FreeCAD-first workflow.

Component resolution across schematic, PCB, STEP assembly, and simulation uses
the tamper-evident bound-component contract described in
[`docs/BOUND-COMPONENTS.md`](docs/BOUND-COMPONENTS.md).
Incremental schematic edits and electrical dependency propagation are documented
in [`docs/INCREMENTAL-ELECTRICAL.md`](docs/INCREMENTAL-ELECTRICAL.md).

```
schematic_designer/
├── core/                     C++20 engine  →  designcore.dll / libdesigncore.so
│   ├── include/designcore/   geometry, pcb_model (N-layer stackup, vias, net
│   │                         classes), obstacle_index (spatial grid), router,
│   │                         drc, pour, c_api
│   ├── src/                  implementations (+ occt_kernel.cpp, optional 3D)
│   └── tests/                engine test suite (model, router, DRC, pours, scale)
├── app/QtDesignStudio/       reusable Qt UI + transitional smoke harness
├── modules/DesignStudioGui/  native FreeCAD GUI extension
├── freecad/DesignStudioWorkbench/
│                              typed commands and selection/contract adapter
├── third_party/freecad/      source/submodule/dependency locks and notices
├── services/ai-gateway/      Python gateway and contract tests
├── testdata/                 immutable, curated test/reference inputs
├── app/DesignStudio/         legacy C# WPF reference client
├── .github/workflows/ci.yml  supported Linux gate + C++ Windows portability
└── build-linux.sh            canonical baseline build/test command
```

## Engine capabilities

- **N-layer stackup** (2–16 copper layers in the UI, 32 in the engine).
- **Vias**: through, blind/buried (span-aware copper blocking), and
  adjacent-layer **microvias** for HDI when the net class allows them.
- **Net classes**: per-class clearance, trace width, via geometry, differential
  gap, **length-matching budget (max skew)** and microvia permission. Nets have
  stable ids — deleting one never corrupts another (the old index==id bug class
  is gone).
- **Multi-layer auto-router**: A* over (x, y, layer) with via insertion and
  cost, rotation-exact pad keepouts, spatially indexed obstacle queries, and
  **explicit failure reasons** (no path / start blocked / goal blocked /
  iteration limit) — nothing fails silently.
- **Transactional interactive router**: cursor previews are isolated from the
  project until commit, include new/moved copper and online-DRC findings, and
  support deterministic walkaround, bounded recursive trace/via shove,
  springback, rollback/cancel, and staged layer-change vias through an additive
  versioned C ABI. See `docs/INTERACTIVE-ROUTING.md`.
- **DRC**: 13 rule families — trace/pad/via clearance in every combination
  (layer-aware), board edge, min width, min drill, **annular ring**,
  **hole-to-hole wall**, **unconnected-net islands**, and **length matching**
  per net class. Every violation carries the rule, a human-readable message,
  location and item ids across the C API.
- **Copper pours**: scanline fills that keep net-class clearance from foreign
  copper, connect directly to same-net copper, and are re-verified by DRC.
- **Scalability**: uniform-grid spatial index under router, DRC and pours.
  Measured in the test suite: full DRC on 2,500 pads + 2,000 traces in ~130 ms;
  a 280 mm route across a dense 4-layer board in ~30 ms.
- **C API v2**: every call returns a status code; results are stored on the
  session and fetched in a second call — no fixed buffers, no silent truncation.
- **Single source of truth**: the C# document rebuilds the native board before
  every native operation, so UI/engine drift is impossible by construction.

## Supported baseline build

On Ubuntu, install CMake, a C++20 compiler, Python 3.12+ with pip, and the Qt 6 Core,
Gui, Widgets, OpenGLWidgets, and Concurrent development packages. Then run one
command from a clean checkout:

```bash
./build-linux.sh
```

That exact command is the CI release gate. It builds the supported Qt client
and C++ core, runs the C++ tests and a headless Qt startup smoke test, and runs
the Python gateway contract tests. Build state is stored outside the checkout
under `$DS_WORK_ROOT`, `$XDG_CACHE_HOME/designstudio/baseline`, or
`~/.cache/designstudio/baseline` (in that priority order).

See `docs/BASELINE.md` for support, data, credential, and clean-worktree policy.

## Using it

- **Build Map**: after opening a product workspace, select the repository that
  contains `acceptance/agent-workflow-controller` and click **Compile Build
  Map**. The current vertical slice produces six digest-bound packages:
  component evidence/CAD, schematic, PCB, FreeCAD, integration, and
  verification. Architecture approval is required before **Run Next Package**
  or **Run All Local Packages** becomes available. Cancel/resume occurs only at
  durable package boundaries, and the UI shows consumed/remaining budget.
  `DESIGNSTUDIO_SOURCE_ROOT` can preselect the evidence repository for demos.
  This phase validates genuine existing subsystem artifacts; it does not claim
  to rebuild them. GPU work remains visibly locked until a separately approved
  Droplet is configured.
- **Layers**: pick the active copper layer in the toolbar (or PgUp/PgDn, or
  keys 1–9). The active layer renders bright; others are dimmed. Board →
  Board Setup sets size, grid and copper layer count.
- **Select** tool: click parts, traces or vias; Shift+click multi-select; drag
  empty area for marquee; drag moves the selection (snaps to grid).
- **Place** tool: pick a footprint in the toolbar combo — a ghost preview
  follows the cursor; `R` rotates it; `Esc` returns to Select.
- **Route** tool: click-click to draw traces on the active layer; press `V`
  mid-chain to drop a via and continue on the next layer; double-click or
  `Esc` ends the chain.
- **Auto-Route** tool: click two points — the C++ engine routes around
  obstacles across layers, inserting vias as needed. Failures tell you *why*.
- **Net classes** (Board → Net Classes…): define DDR/SerDes/power classes with
  their own clearance, width, via and skew rules; assign nets to classes.
- **Pours** (Board → Generate Copper Pour…): solid GND/power fills per layer.
- **Tools → Run DRC** (`Ctrl+D`): full rule set with messages — click a
  violation to zoom to it.
- **Nets panel**: routed length and connectivity state (islands) per net.
- **Right-click** = context menu; right/middle-drag = pan; wheel = zoom;
  `F` fit; `Ctrl+A` select all; `Del` delete; `Ctrl+Z`/`Ctrl+Y` undo/redo.

## Projects, autosave, fabrication output

- `File → Save / Open` — JSON project format v2 (`.dsproj`), git-friendly,
  crash-safe atomic writes; v1 projects migrate automatically. Autosave every
  2 minutes with recovery offered on open.
- `File → Export Fabrication Outputs…` writes the complete handoff package:
  Gerber RS-274X for **every copper layer** (traces, pours, rotation-exact pad
  regions, via lands), top/bottom **solder mask** and **paste**, board outline,
  **Excellon drill files split by plated span** (through + one file per
  blind/buried/micro span), BOM CSV, side-aware pick-and-place CSV.

## Footprints

- **Library → Footprint Library Manager**: JSON library in
  `%APPDATA%\DesignStudio\footprints\` (one file per footprint, hand-editable).
- **Library → Add Standard Footprint Set**: deterministic IPC-7351-style
  parametric generators — chip passives (0402…1206), SOT-23, SOIC-14/16,
  QFN-16/32, through-hole pin headers (IPC-2221 drill/annular ring rules).

## Physics & mathematics engine (`docs/physics-engine.md`)

Textbook-grounded electrical math in the C++ core, exposed live in the UI:
Hammerstad–Jensen microstrip and exact Cohn stripline (AGM elliptic
integrals), IPC-2141A differential pairs, εeff/delay, skin effect and
conductor+dielectric loss, IPC-2221 current capacity, Onderdonk fusing, via
L/C/R/θ, crosstalk and plane capacitance — each model cited to the references
in `TextBook Datasets/`. **Tools → Physics Calculator…** computes against the
board's stackup (Board Setup: εr, dielectric height, tanδ, copper weight);
**Net Classes → Width from impedance…** synthesizes the trace width (and diff
gap) that hits a target Z0/Zdiff and writes it straight into the routing
rules.

## Closed AI design loop

Datasheet PDF → (extractor prompt, any vision LLM) → `component/2` JSON →
**Library → Import JSON…** → place & wire → **File → Export Circuit State
(AI advisor)…** → attach to Gemini/Claude with
`docs/circuit-advisor/ADVISOR_PROMPT.md` → `advice/1` JSON back (what to add,
remove, replace — each "add" returns as another component/2 file). Large BGAs
are described parametrically (grid + per-ball pin table, JEDEC lettering) and
expanded deterministically; package height flows into the 3D view; the full
electrical payload (parameters, thermal, power domains) rides along into the
circuit-state file so the advisor reasons from real datasheet numbers.

## Datasheet → JSON pipeline (`docs/datasheet-extractor/`)

Extract a component from any datasheet PDF with the engineered prompt in
`EXTRACTOR_PROMPT.md` (use any vision LLM), then **Library → Import JSON…**.
The JSON (`design-studio.component/1`, validated by `component.schema.json`)
carries everything the five datasheet jobs need: pin table with electrical
types, supply limits and required external components (with verbatim
constraints like "min 10 µF, ESR ≤ 3 Ω"), recommended land pattern in mm,
pin-1/polarity markers, and module outline hints.

The importer is the trust boundary: deterministic validators check pin/pad
agreement, pad overlaps, annular rings and unit plausibility before anything
enters the library, and the import log surfaces required externals plus
suggested high-speed net classes (e.g. USB 90 Ω differential).

Pin electrical types flow onto placed pads and power the **ERC checks** that
run with every DRC: output-vs-output conflicts, connected no-connect pins,
and power-input nets with no power source.

## 3D view

The 3D tab regenerates the board scene from the PCB on every visit: FR4 slab,
outer-layer copper, via barrels, extruded component bodies with per-package
height heuristics. Drag to orbit, wheel to zoom.

## Mechanical analysis and cloud review

The mechanical path keeps FreeCAD/OpenCascade as the editable B-Rep authority,
DesignCore as the native numerical engine, and the AI gateway as an advisory
layer. Linear-static CSR solving is implemented in the CPU reference backend;
an optional CUDA/cuBLAS backend can be enabled with
`-DDESIGNCORE_ENABLE_CUDA=ON` when a CUDA toolkit is installed. The worker
boundary has explicit slots for modal, thermal, contact, impact, fatigue/creep,
and injection-flow analysis; an uninstalled worker reports `unavailable` and
cannot be treated as a pass.

See [`docs/MECHANICAL-ENGINE-ARCHITECTURE.md`](docs/MECHANICAL-ENGINE-ARCHITECTURE.md)
for the trust boundary and exact file map. The optional browser review client
is in [`web/mechanical-viewer`](web/mechanical-viewer); it uses Three.js for
GLB display and semantic view modes while Qt remains the desktop host.

## 3D mechanical kernel (optional, Open CASCADE)

```powershell
vcpkg install opencascade
cmake -B build -DUSE_OCCT=ON -DCMAKE_TOOLCHAIN_FILE=<vcpkg>\scripts\buildsystems\vcpkg.cmake
```

OCCT is LGPL-2.1-with-exception: commercial closed-source use is permitted.

## Roadmap (next)

1. Net-aware routing: route by net from the ratsnest, not click points
2. Push-and-shove interactive routing
3. KiCad file format import/export (inherit the library ecosystem)
4. Polygonal board outlines; thermal-relief spokes on pours
5. Interactive length tuning (serpentines) on matched classes
6. ODB++ / IPC-2581 export; STEP export of the assembled board

See `CHANGELOG.md` for what changed in this revision and
`INDUSTRIAL_AUDIT.md` for the historical gap analysis that drove it.
