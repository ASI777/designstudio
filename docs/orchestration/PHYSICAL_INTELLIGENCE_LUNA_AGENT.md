# Physical Intelligence: Sol-to-Luna Execution Brief

**Authority:** GPT-5.6 Sol high authors and reviews this file. GPT-5.6 Luna xhigh executes packages manually selected by the user. Luna must not edit this brief or mark a package accepted.

## Luna handoff prompt

Use this standard prompt when a package is marked `READY`:

> Read `docs/orchestration/PHYSICAL_INTELLIGENCE_LUNA_AGENT.md` completely. Execute only the first work package marked `READY`. Preserve unrelated worktree changes, use `apply_patch` for edits, run every listed acceptance test, and stop with the required evidence report. Do not mark your own package accepted.

When a package is marked `REWORK`, use the package-specific rework prompt in its Sol review instead. All defined build packages are now accepted; no Luna package is currently released for execution.

## Current release state

```text
Brief state: RELEASED — PACKAGES 1–7 ACCEPTED
Current executor action: none; release audit complete
Last accepted baseline: packages 1–7, including the CAD quality loop, topology replacement, authoritative HLR drawings, constraint-driven editable candidates, native AP242/XCAF semantic assembly, coupled propagation gates, release fixtures, runtime checks, and active Mod/DesignStudio installation
Worktree policy: dirty; preserve unrelated changes
Execution order: serial, 1 → 2 → 3 → 4 → 5 → 6 → 7
First milestone: CAD quality loop (packages 1–3)
```

The repository is `${DESIGNSTUDIO_REPO}`. The maintained FreeCAD/DesignStudio source is under `freecad/DesignStudioWorkbench`; the Qt application is under `app/QtDesignStudio`.

## Roles and control rules

- **Sol high:** maintains this brief, checks actual diffs and runtime evidence, changes package states, accepts or rejects work, and releases the next package.
- **Luna xhigh:** implements only the first `READY` package, or the specifically assigned `REWORK` package, runs its required checks, and reports evidence. Luna may change source, tests, fixtures, and package documentation explicitly listed by that package, but may not edit this file or claim acceptance.
- **User:** manually selects GPT-5.6 Luna xhigh, supplies the handoff prompt, and returns Luna’s evidence to Sol high for review.
- Packages run serially because the worktree already contains substantial changes. No agent may reset, clean, overwrite, or broadly format the worktree. Existing v1 formats and FCStd files must remain readable.
- FreeCAD/OpenCASCADE B-Reps and verified measurements are geometry authority. Images, renders, and raster previews are evidence/inspiration only and never CAD measurement authority.
- Every failure is fail-closed: do not silently approximate invalid geometry, missing analysis, unsupported interfaces, or unverifiable evidence.
- A package may be `BLOCKED`, `READY`, `IMPLEMENTED`, `ACCEPTED`, or `REWORK`. Only Sol high changes state or acceptance criteria. `REWORK` must include an exact correction list.

## Current source/build/test baseline

Already present and treated as the v1 compatibility baseline:

- `docs/schemas/physical-design-session-v1.schema.json`
- `docs/schemas/local-redesign-v1.schema.json`
- `freecad/DesignStudioWorkbench/DesignStudio/physical_design.py`
- `freecad/DesignStudioWorkbench/DesignStudio/live_tools.py`
- `freecad/DesignStudioWorkbench/DesignStudio/commands.py`
- `docs/schemas/mechanical-cad-program-v1.schema.json`
- `freecad/DesignStudioWorkbench/DesignStudio/mechanical_cad.py`
- `freecad/DesignStudioWorkbench/DesignStudio/mechanical_contract.py`
- `freecad/DesignStudioWorkbench/tests/test_mechanical_cad.py`
- `freecad/DesignStudioWorkbench/tests/mechanical_cad_runtime.py`
- `freecad/DesignStudioWorkbench/tests/test_physical_design.py`
- `freecad/DesignStudioWorkbench/tests/physical_design_runtime.py`
- Active installed copy: `${DESIGNSTUDIO_PREFIX}/Mod/DesignStudio`
- Build directory: `/tmp/designstudio-build`
- Install prefix: `${DESIGNSTUDIO_PREFIX}`

Baseline commands (run from `${DESIGNSTUDIO_REPO}` unless stated otherwise):

```bash
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -R 'physical_design|qt_product_ui_contract|qt_freecad_electronics_smoke|qt_step_mesh_reader|qt_mesh_projection_controls|qt_cold_start_workspace_creation' --output-on-failure
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/physical_design_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
cmake --install /tmp/designstudio-build --prefix ${DESIGNSTUDIO_PREFIX}
```

The focused baseline is 6/6 tests passing and the FreeCAD runtime reports `PHYSICAL_DESIGN_RUNTIME_OK`. The isolated acceptance environment is `/tmp/designstudio-venv` (declared Python dependencies installed and selected during CMake configuration). The complete CTest suite now passes 67/67, including the explicit `physical_design_flow_matrix` fixtures for photo-first, sketch-first, hardware-first, and local surface redesign. No release check was weakened.

## Public compatibility requirements

The following v1 identifiers and behavior must remain valid while v2 is added:

- `design-studio.mechanical-cad-program/1`, `design-studio.physical-design-session/1`, and `design-studio.local-redesign/1` remain readable and executable.
- No surface-design v1 schema/executor currently exists; `design-studio.surface-design/2` is a new contract and must not be presented as a v1 compatibility layer.
- Existing typed FreeCAD operations and FCStd files remain loadable.
- New schemas must reject malformed finite geometry, unordered knots, non-positive weights, missing provenance, unsupported guides, invalid section order, and geometry outside the locked envelope.
- New operations must be deterministic, auditable, rollback-capable, and never use SVG/DXF/PNG pixels as CAD input authority.

## Work package registry

| ID | State | Scope | Release condition |
|---|---|---|---|
| `1-vector-native-cad` | **ACCEPTED** | v2 typed B-spline/surface command layer | Sol acceptance completed 2026-08-13 |
| `2-topology-local-replacement` | **ACCEPTED** | topology-aware local patch replacement | Sol acceptance completed 2026-08-13 |
| `3-authoritative-drawings` | **ACCEPTED** | OCCT HLR/TechDraw drawings and five-sheet package | Sol acceptance completed 2026-08-14 |
| `4-constraint-candidates` | **ACCEPTED** | three constraint-driven physical candidates and guided UI | Sol acceptance completed 2026-08-14 |
| `5-semantic-assembly` | **ACCEPTED** | XCAF/AP242 identity-preserving assembly and selection | Sol acceptance completed 2026-08-14 |
| `6-coupled-physical-loop` | **ACCEPTED** | affected-node propagation and coupled engineering gates | Sol acceptance completed 2026-08-14 |
| `7-release-hardening` | **ACCEPTED** | fixtures, full suite, install, and GUI smoke | Sol acceptance completed 2026-08-14 |

### Package 1 — Vector-Native CAD Command Layer (`ACCEPTED`)

Add backward-compatible `design-studio.mechanical-cad-program/2` and new `design-studio.surface-design/2`, retaining mechanical-cad v1 execution unchanged. Add typed operations:

```text
sketch.bspline
curve.bspline3d
feature.loft
feature.guided_loft
feature.surface_fill
feature.thicken
feature.split
drawing.project
```

Contracts must carry degree, ordered knots, multiplicities, positive weights, finite millimetre control points, closed/periodic state, curve classification (`reference-locked`, `compatible-interface`, or `original-designed`), provenance, section order, guide references, continuity requirements, and output checks. Each section receives its own vector profile; do not scale one common planform. Preserve locked envelope/control datums and fail closed for unsupported guides, invalid knot vectors, failed sewing, self-intersections, or thickening failures by rolling back the whole program.

Permitted files: the mechanical-cad schemas/executor/tests, surface-design schemas/executor/tests, and the smallest directly adjacent FreeCAD command or CMake registration required to expose these operations. Do not begin local replacement, drawing replacement, semantic assembly, or candidate-ranking work in this package.

Required acceptance evidence:

1. Schema-positive and schema-negative tests cover knots, multiplicities, weights, finite 3D points, provenance, section/guide order, continuity, and envelope violations.
2. FreeCAD runtime creates editable B-spline sketches/curves, loft/guided-loft/fill/thicken/split features, recomputes repeatedly, reloads FCStd, and retains editable feature identity.
3. Generated upper/lower solids (where applicable) are valid, watertight, non-self-intersecting B-Reps; STEP round-trip succeeds; each STL is non-empty.
4. Invalid guide networks, failed sewing/thickening, and contradictory curves leave no partially committed program objects.
5. Existing v1 contract/runtime tests pass without modification of their expectations.

Required commands:

```bash
cmake --build /tmp/designstudio-build -j2
python3 freecad/DesignStudioWorkbench/tests/test_mechanical_cad.py
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/mechanical_cad_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
ctest --test-dir /tmp/designstudio-build -R 'mechanical_cad_program_v1|vector_native_cad|physical_design|qt_product_ui_contract|qt_freecad_electronics_smoke' --output-on-failure
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/vector_native_cad_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/physical_design_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
```

The package must register the named `mechanical_cad_program_v1` regression and `vector_native_cad_*` contract/runtime tests in CTest. `vector_native_cad_runtime.py` is a required new runtime fixture: it must create, recompute, save/reload, export, and verify the v2 B-Reps and artifacts described above. The command is intentionally listed before the file exists so Luna cannot report package completion without adding and running that evidence fixture.

#### Sol review verdict — 2026-08-13: `REWORK`

The first implementation report is not accepted. Green unit output did not prove the package gates, and independent probes found the following blocking defects:

1. `feature.guided_loft` calls the same `Part.makeLoft` path as `feature.loft`; guide objects are merely linked and labelled `validated`. The supplied runtime guide intersects neither section, yet the command succeeds and produces the exact unguided shape digest.
2. Continuity requirements are stored as JSON but no G0/G1/G2 boundary measurement or enforcement occurs.
3. `mechanical-cad-program-v2.schema.json` contains an empty `allOf`, which is invalid Draft 2020-12 schema, and operation-specific `params` are not described. The contract therefore cannot perform the required schema-negative checks.
4. Mechanical v2 has no locked envelope/control-datum contract, so its curve commands cannot reject geometry outside the canonical interface.
5. `feature.surface_fill` accepts multiple boundaries but silently uses only the first. `feature.thicken` can silently switch to an offset-and-cut result that ignores requested removed faces. Unsupported semantics must fail closed.
6. Most curves/features are baked `Shape` values. Their links and JSON metadata do not rebuild geometry after a control point, weight, source curve, or feature parameter changes. FCStd reload currently proves only that a saved shape remains present, not that the feature graph remains editable.
7. No self-intersection/sewing result or measurable continuity receipt is checked. The current valid-shape check is insufficient for those explicit gates.
8. The runtime exports STEP but never imports the STEP again, so STEP round-trip is unproven. It also does not prove watertight upper/lower split solids.
9. `drawing.project` emits a bounding rectangle rather than projected B-Rep edges. Package 3 owns HLR/TechDraw sheets, but package 1 still requires a real deterministic orthographic edge projection command.
10. CTest registers `vector_native_cad_runtime.py` under ordinary Python. It prints `VECTOR_NATIVE_CAD_RUNTIME_SKIPPED_FREECAD` and CTest counts that as a pass; this is not runtime evidence.
11. Program rollback removes document objects but does not prove removal of files emitted by earlier `drawing.project` commands when a later command fails.

Luna must correct only package 1. Do not begin topology-local replacement or any later package. Required corrections:

- Replace the empty/permissive schema branch with valid Draft 2020-12 operation-discriminated definitions. Run `Draft202012Validator.check_schema` on both schemas and validate positive and negative fixtures through the public schemas as well as the host validator.
- Add a typed locked-interface envelope and control datums (or an exact reference with digest) to mechanical v2. Reject every out-of-envelope control point and any moved locked datum before document mutation.
- Implement a genuinely guide-constrained OCCT construction for the supported guide-network subset. Each guide must intersect every ordered section within a declared tolerance; changing a guide must change the output B-Rep. Reject all unsupported/non-intersecting networks atomically—never relabel an unguided loft as guided.
- Produce and enforce `ContinuityResultJSON` from sampled boundary positions/tangents/normals. G0 must be measured; mandatory G1 must meet the declared normal/tangent limit; G2 must be measured and must block when requested.
- Make B-spline curves and downstream features truly recomputable/editable with typed FreeCAD properties and persistent proxies or equivalent native features. The runtime must save/reload FCStd, modify a source control point or weight, recompute, and prove the downstream digest changes while command identity and links remain stable.
- Consume every declared surface-fill boundary or reject the network. Validate sewing, closedness, self-intersection, removed-face indices, thickness semantics, split solid count, and watertightness. If the thickening fallback cannot preserve `remove_faces`, it must fail rather than changing meaning.
- Make `drawing.project` output actual deterministic orthographic B-Rep edge geometry for requested views with source digest. Do not implement package-3 HLR, annotations, sheets, or GD&T yet.
- Import the emitted STEP into a fresh document, recompute, validate the returned B-Rep/solid count/bounds, and compare it to the source within deterministic tolerances. Keep non-empty STL verification.
- Make rollback cover both newly introduced FreeCAD objects and generated files. Add negative runtime fixtures for invalid guide intersection, contradictory curve order, sewing/fill failure, self-intersection, failed thickness, invalid split, and a failure after drawing output.
- Register the CTest runtime through a configurable DesignStudio/FreeCAD launcher and make an unavailable launcher a failure on this acceptance host. A printed skip must never satisfy the test.

Rework acceptance commands must include:

```bash
python3 -c "import json; from jsonschema.validators import Draft202012Validator; [Draft202012Validator.check_schema(json.load(open(path))) for path in ('docs/schemas/mechanical-cad-program-v2.schema.json','docs/schemas/surface-design-v2.schema.json')]; print('VECTOR_NATIVE_SCHEMAS_OK')"
python3 freecad/DesignStudioWorkbench/tests/test_mechanical_cad.py
python3 freecad/DesignStudioWorkbench/tests/test_vector_native_cad.py
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -V -R '^vector_native_cad_runtime$'
ctest --test-dir /tmp/designstudio-build -R 'mechanical_cad_program_v1|vector_native_cad|physical_design|qt_product_ui_contract|qt_freecad_electronics_smoke' --output-on-failure
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/mechanical_cad_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/vector_native_cad_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/physical_design_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
```

For the next Luna xhigh run, use this rework prompt:

> Read `docs/orchestration/PHYSICAL_INTELLIGENCE_LUNA_AGENT.md` completely. Execute only package `1-vector-native-cad`, currently marked `REWORK`, and implement every correction in its 2026-08-13 Sol review. Preserve unrelated worktree changes, use `apply_patch`, run every rework acceptance command, and stop with the required evidence report. Do not edit the orchestration brief, mark the package accepted, or begin package 2.

### Package 2 — Topology-Aware Local Surface Replacement (`ACCEPTED`)

Add `local-redesign/2` while preserving v1 receipts. Capture stable target semantic ID and exact digest, selected `FaceN` set, boundary-edge signatures and adjacent faces, local frame, protected objects/clearances, requested G0/G1/G2 continuity, expansion limits, and manufacturing constraints. Preview must build a sibling patch branch, replace only selected topology, sew/heal, and prove boundary deviation ≤0.05 mm, mandatory G1 normal-angle discontinuity ≤1°, measured/reported G2 (blocking only when requested), unchanged unselected faces and unrelated semantic-object digests, protected clearances, and valid non-self-intersecting solids. Commit transfers stable identity, hides but retains baseline, and supports rollback; failed preview removes only new objects.

### Package 3 — Authoritative Engineering Drawings (`ACCEPTED`)

Replace raw edge projection with an OCCT HLR/TechDraw-backed pipeline. Produce six orthographic B-Rep views in SVG and DXF, separate visible/hidden layers, true sections, selected-boundary/continuity details, dimensions, datums, center marks, radii, diameters, angles, tolerances, supported GD&T, and curve/patch/object/source/package digests. Produce a five-sheet human-readable package (general arrangement; selected-region details; sections/continuity; material/manufacturing/tolerances; interfaces/protected clearances) and deterministic additional sheets when needed. Compare projected geometry to source B-Rep within 0.05 mm and verify stable output after FCStd reload. Drawings never become CAD input authority.

### Package 4 — Constraint-Driven Physical Design Candidates (`ACCEPTED`)

Extend physical-design sessions so photo/render/scan/sketch evidence, calibrated dimensions, locked hardware/mechanism/PCB/battery/display/connector/cable volumes, human/service volumes, desire priorities, material/process, hand-size objectives, hard constraints, scoring weights, and unresolved evidence actively drive generation. Generate exactly three editable candidates—controller `compact`, `balanced`, `comfort`; other products use three outcome-named alternatives. Hard failures remove candidates before ranking; missing analysis remains `incomplete`. Rank occupied volume, reach, clearance, wall, continuity, manufacturability, mass, material use, and user priorities. Add one guided UI for image/sketch input, calibration, inside-out/outside-in/co-design, hardware-first placement, desire/material/manufacturing capture, comparison, and selected-region redesign/preview approval.

#### Sol review verdict — 2026-08-14: `ACCEPTED`

Independent Sol verification passed the v2 session contract, the launcher-backed FreeCAD runtime, the exact-three-candidate/reload/locked-object fixture, the focused 12-test CAD/UI regression set, CMake build, install, and the active installed launcher. Candidate labels were compact/balanced/comfort, editable mechanical-cad-program/2 programs were retained in the session receipt, rejected candidates were excluded from ranking, and wall/reach/manufacturing/mass analyses stayed visibly `incomplete` rather than being treated as passes. Package 5 was released as the sole next `READY` package.

### Package 5 — Semantic Assembly and Cross-View Selection (`ACCEPTED`)

Add `semantic-assembly/2` and an XCAF/AP242-aware reader preserving hierarchy, component/solid/face IDs, names/reference designators, coordinate systems/placements, material/appearance, per-component mesh ranges/triangle ownership, and source/tessellation digests. Viewport selection must map to the same object in PCB, BOM, product graph, FreeCAD tree, and property editor. Use source colors; legacy flattened STEP remains viewable but reports identity unavailable. Acceptance uses a multi-component AP242 fixture with distinct names, placements, colors, materials, and reference designators and verifies bidirectional selection after cache reload.

#### Sol review verdict — 2026-08-14: `ACCEPTED`

Independent verification passed the native OCCT XCAF/AP242 fixture, including hierarchy-derived IDs, names/reference designators, placements, source colors, physical materials, contiguous per-component triangle ownership, source/tessellation digests, cache reload, and legacy fallback behavior. The Qt semantic selection model and assembly browser expose the same identity in the PCB component tree, PCB canvas, 3D viewport, BOM, product-graph, FreeCAD-tree, and property tabs. Embedded FreeCAD selection is routed through the trusted dispatcher with AP242 ID and reference-designator fallback. The focused semantic/step/viewport/selection suite passed 7/7, the complete build passed, and the active local prefix was installed and launcher import verified. Package 6 was then released and independently accepted.

### Package 6 — Coupled Physical-Intelligence Loop (`ACCEPTED`)

Connect the semantic product graph to deterministic affected-node propagation. A user edit re-evaluates applicable reach/grip/viewing angle; collision/tolerance/assembly/service; wall/draft/overhang/process; thermal/hot surface; structural loads/stiffness/vibration; cable/flex bend/connector; electrical clearance/antenna/ground/EMI; mass/CG/stability checks. Fast screens may rank, but exact solvers sign off. Unavailable solvers produce visible `incomplete` gates. UI shows affected objects, score changes, failures, assumptions, and why each solver reran.

#### Sol review verdict — 2026-08-14: `ACCEPTED`

Independent verification passed the strict `propagation-run/2` receipt schema, graph reachability/causal paths, eight deterministic engineering gates, explicit incomplete status for unavailable exact solvers, score deltas, persisted evidence, Qt affected-object/gate/assumption presentation, operation approval routing, and healthy daemon error handling. The Rust release daemon was rebuilt and installed from the current source (SHA-256 `820d890662826c1281a071261e333419f98d859fd11474baa4dc70f5f5ead529`); 22 Rust tests, the launcher-backed protocol fixture, Qt smoke, launcher import, and focused propagation/UI checks passed. Package 7 was then released and independently accepted.

### Package 7 — Release Hardening (`ACCEPTED`)

Repair the seven current full-suite failures by installing declared dependencies or correcting stale evidence fixtures; never weaken release checks. Add complete photo-first, sketch-first, hardware-first, and local-redesign fixtures. Verify FCStd reload, STEP, SVG/DXF geometry digests, STL, semantic selection, candidate ranking, baseline rollback, local installation, and GUI smoke through the desktop launcher. Full CTest must have zero unexplained failures. Verify the active `${DESIGNSTUDIO_PREFIX}/Mod/DesignStudio` copy after installation.

#### Sol review verdict — 2026-08-14: `ACCEPTED`

The seven pre-existing suite failures were repaired by using the declared isolated Python dependencies and correcting stale package-pin/variant evidence fixtures; validation remained strict. A dedicated `physical_design_flow_matrix` now validates photo-first, sketch-first, hardware-first, and local surface-redesign contracts. The complete suite passes **67/67**. Installed workbench source comparison reports 53/53 non-cache files matching, launcher import passes, desktop-file validation passes, and the installed vector-native CAD, physical-design, and v1 mechanical runtimes report `VECTOR_NATIVE_CAD_RUNTIME_OK`, `PHYSICAL_DESIGN_RUNTIME_OK`, and `MECHANICAL_CAD_RUNTIME_OK`. FCStd reload, STEP/STL/SVG/DXF artifact checks, semantic selection, candidate ranking, baseline retention, Qt smoke, and local-prefix installation were independently verified.

## Post-release next developments

The release is ready for controlled product work, but physical validation remains the next engineering step. The recommended sequence is: (1) run the three editable controller candidates through measured grip-buck testing and record the fixed 1–5 comfort/reach form; (2) connect exact thermal, structural, cable-bend, and electrical/EMI solvers where the current propagation receipt intentionally reports `incomplete`; (3) add measured AP242 fixtures from real hardware assemblies and verify bidirectional selection against production BOMs; and (4) promote only tested parameter sets into manufacturing packages. No raster image, SVG, DXF, or fast-screen score should be treated as manufacturing sign-off.

The executable continuation plan is `docs/orchestration/PHYSICAL_INTELLIGENCE_NEXT_EXECUTION.md`; the reproduced native desktop startup crash makes package `8-desktop-startup-reliability` its sole `READY` package.

## Sol review gate

When Luna reports `IMPLEMENTED`, Sol must independently:

1. Inspect `git diff --stat` and every changed file against the assigned scope.
2. Re-run the package’s targeted schema, unit, FreeCAD-runtime, Qt, and artifact checks.
3. Confirm v1 regression tests and compatibility requirements.
4. Inspect generated FCStd/STEP/STL/SVG/DXF artifacts and digests where applicable.
5. Record either `ACCEPTED` (then change exactly the next package from `BLOCKED` to `READY`) or `REWORK` with a precise correction list. Never accept based only on Luna’s narrative.

## Luna completion report (required)

```text
Package: <id>
State requested: IMPLEMENTED
Changed files: <absolute or repository-relative paths>
Commands run and results: <full command + pass/fail summary>
Generated artifacts: <paths, sizes, digests, reload/round-trip evidence>
Acceptance gates: <each gate PASS/FAIL with evidence>
Known failures: <exact failures; no masking>
Unrelated worktree changes preserved: yes/no
Remaining risks: <short list>
```

Luna stops after this report. Sol high—not Luna—performs acceptance and releases the next package. If a package cannot meet a gate, report `BLOCKED` or `REWORK` evidence rather than weakening the gate or editing this brief.
