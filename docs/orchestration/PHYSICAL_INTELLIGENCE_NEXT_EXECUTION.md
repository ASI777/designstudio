# Physical Intelligence: Post-Release Execution Brief

**Authority:** GPT-5.6 Sol high owns this brief, acceptance criteria, and package states. GPT-5.6 Luna xhigh implements the first package marked `REWORK`, or the first package marked `READY` when no rework exists. Luna must not edit this file or accept its own work.

## Executor prompt

> Read `docs/orchestration/PHYSICAL_INTELLIGENCE_NEXT_EXECUTION.md` completely. Execute only the first work package marked REWORK, or the first package marked READY when no rework exists. Preserve unrelated worktree changes, use apply_patch for source edits, run every listed acceptance command, and stop with the required evidence report. Do not edit the orchestration brief, mark your own package accepted, begin a blocked package, invent physical-test observations, or fabricate solver/manufacturer evidence.

## Current state

```text
Brief state: ACTIVE — PACKAGE 9 READY
Current executor action: execute 9-guided-physical-validation
Accepted dependency baseline: packages 1–7 in PHYSICAL_INTELLIGENCE_LUNA_AGENT.md plus package 8 below
Repository: ${DESIGNSTUDIO_REPO}
Install prefix: ${DESIGNSTUDIO_PREFIX}
Worktree policy: dirty; preserve every unrelated change
Execution order: serial, 8 → 9 → 10 → 11 → 12
External product-release gate: blocked until real physical observations and controlled manufacturer inputs exist
```

## Audit findings that define this plan

The package 1–7 implementation remains the compatibility baseline. The installed Qt harness starts offscreen, the DesignStudio workbench imports through the launcher, and the installed `designstudio-agentd` matches the current release binary with SHA-256 `820d890662826c1281a071261e333419f98d859fd11474baa4dc70f5f5ead529`. The prior complete acceptance run passed 67/67 tests.

The first package 8 implementation rebuilt and installed successfully and its expanded suite passed 71/71 tests. Sol acceptance is nevertheless withheld: the Wayland and desktop-entry fixtures can report readiness before the embedded workspace is constructed, lifecycle cleanup is not stress-tested, and incomplete candidate metrics still influence digital ranking. These are acceptance-test and fail-closed correctness defects, not permission or bootstrap failures.

The audit identified these remaining gaps:

1. The desktop entry is valid, executable, MIME-registered, and points to the installed launcher, but the real Wayland desktop launch terminates immediately with `SIGSEGV`. An exact launcher-environment gdb run places the crash in `DesignStudioGui.so` `(anonymous namespace)::Module::showWorkspace`, called while `DesignStudioWorkbench` is auto-activated. The existing Qt harness smoke cannot detect this native FreeCAD-host crash, and `modules/DesignStudioGui/tests/run-native-smoke.sh` is not registered in CTest.
2. Physical-design capture exists through typed chat tools, but `ProductConfiguratorPanel` is an option browser rather than the promised guided photo/sketch calibration, hardware-volume, material/process, candidate-testing, and local-redesign workspace.
3. There is no physical grip-buck test plan/observation/decision contract. No real small-, medium-, or large-hand observations have been recorded.
4. Candidate analysis correctly reports reach, wall, manufacturability, mass, and material use as `incomplete`, but `_score_candidate` still assigns heuristic reach and volume-derived material-use points. An incomplete metric must not influence a final selection.
5. `propagation/run` preserves missing solvers as `incomplete`, but it accepts caller-supplied status objects as exact-adapter results. A UI, agent, or LLM must not be able to manufacture a solver pass.
6. The current native AP242 runtime is strong but uses a synthetic two-box fixture. The 10.6 MB, 106-component Agent Workflow Controller assembly is not yet the production-scale semantic-selection and performance regression fixture.
7. STEP import is lazy and backgrounded, but the current worker discards stale completion rather than cancelling work, the mesh cache key depends on path/size/mtime instead of the source content digest, and `interactionPreview_` does not yet reduce rendering work while orbiting.
8. Manufacturing release is correctly `blocked-pending-contracted-profile`. No agent may remove that block without a controlled fabrication profile, assembler identity, physical prototype evidence, and the other external gates recorded in the acceptance project.
9. `/tmp/designstudio-build` and `/tmp/designstudio-venv` are ephemeral and may be absent at the start of a run. Executors must recreate them instead of treating their absence as a source failure.

## Control rules

- Only Sol changes `READY`, `BLOCKED`, `REWORK`, `IMPLEMENTED`, or `ACCEPTED` states.
- Run packages serially. Do not reset, clean, overwrite, broadly format, or delete the dirty worktree.
- Existing v1 and v2 schemas, FCStd files, STEP files, receipts, and public tool names remain readable.
- FreeCAD/OpenCASCADE B-Reps and verified measurements remain geometry authority. Raster images, SVG, DXF, renders, and generated images remain evidence only.
- Human observations must be entered by a real user and labelled with provenance. Synthetic test fixtures must set `synthetic_fixture=true` and can never unlock a real product decision or manufacturing release.
- A solver pass is valid only when produced by a registered deterministic adapter over digest-bound inputs. Raw JSON from the UI, chat agent, or model is untrusted.
- Missing solver, material, manufacturer, calibration, or physical-test evidence remains visibly `incomplete` or `blocked`.
- Installation into `${DESIGNSTUDIO_PREFIX}` may require explicit permission. Request it; do not redirect the install silently.
- Do not weaken full-suite tests, lower geometric tolerances, bypass hashes, or replace native AP242 verification with a hand-authored sidecar.

## Reproducible baseline bootstrap

Run from the repository root. Reuse an existing environment only when it contains the required packages.

```bash
python3 -m venv /tmp/designstudio-venv
/tmp/designstudio-venv/bin/pip install \
  cryptography==46.0.5 pillow==12.1.1 httpx==0.28.1 \
  jsonschema==4.25.1 fastapi==0.115.12 pydantic==2.10.6 uvicorn==0.34.3
cmake -S . -B /tmp/designstudio-build \
  -DPython3_EXECUTABLE=/tmp/designstudio-venv/bin/python
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build --output-on-failure
```

If dependency installation requires network access, request permission. A package report must distinguish source/test failures from environment/bootstrap failures.

## Package registry

| ID | State | Purpose | Release condition |
|---|---|---|---|
| `8-desktop-startup-reliability` | **ACCEPTED** | repair native FreeCAD-host startup crash and add real desktop regression evidence | Accepted by Sol on 2026-08-14 |
| `9-guided-physical-validation` | **READY** | guided physical-design workspace, grip-buck evidence, and honest candidate selection | Package 8 accepted |
| `10-trusted-solver-adapters` | BLOCKED | typed trusted solver registry and asynchronous propagation | Package 9 accepted |
| `11-production-semantic-assembly` | BLOCKED | production-scale AP242/BOM identity, cancellation, cache integrity, and interaction LOD | Package 10 accepted |
| `12-manufacturing-qualification` | BLOCKED | controlled physical/manufacturer release gate and final hardening | Package 11 accepted; real product release still requires external inputs |

## Package 8 — Desktop Startup Reliability (`ACCEPTED`)

### Reproduced failure

The installed desktop files at `$HOME/Desktop/DesignStudio.desktop` and `${XDG_DATA_HOME:-$HOME/.local/share}/applications/designstudio.desktop` are identical and pass `desktop-file-validate`. `gtk-launch designstudio` resolves the expected launcher, but `${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher` exits immediately. Running the launcher directly outside the sandbox reproduces:

```text
Program received signal SIGSEGV, Segmentation fault.
```

The exact launcher environment under gdb produces this decisive stack:

```text
#0  unknown address in the DesignStudio process
#1  (anonymous namespace)::Module::showWorkspace(Py::Tuple const&)
    from ${DESIGNSTUDIO_PREFIX}/lib/DesignStudioGui.so
#9  Gui::Application::activateWorkbench(char const*)
#10 Gui::StartupPostProcess::activateWorkbench()
#12 Gui::Application::runApplication()
```

The crash occurs after the `DesignStudioWorkbench` autoload reaches `DesignStudioGui.showWorkspace()`. Dependency resolution succeeds with the launcher paths. The separate `designstudio-qt-harness --smoke-test` passes because it does not exercise this embedded FreeCAD workspace path. The existing native smoke script also targets `$prefix/bin/FreeCAD`, which is absent from the branded installation, so it cannot currently validate the production executable even if invoked manually.

### 2026-08-14 Sol rework verdict

The implementation repaired the reproduced native crash, registered production-host tests, installed matching source/build artifacts, and passed 71/71 tests. It is not accepted because the current evidence can still be false-green:

1. `run-wayland-startup.sh` treats `phase=activation_start` plus a live process as readiness. The launcher writes that phase before `DesignStudioGui.showWorkspace()` returns, so the original post-activation crash can occur after the fixture has declared success.
2. The Wayland fixture does not inspect the visible main window, active `DesignStudioWorkbench`, embedded `DesignStudioWorkspaceDocument`, visible chat dock, native workbenches, or absence of a nested `QMainWindow`.
3. The desktop-entry fixture may fall back from `gtk-launch` to invoking `DesignStudioLauncher` directly. That fallback is useful for restricted CI diagnostics but is not evidence that the real Wayland desktop entry works.
4. Repeated activation, deactivation, document closing, and reopening are not tested for duplicate docks, toolbars, MDI documents, product surfaces, or stale workspace pointers.
5. `_score_candidate` still assigns numeric reach and material-use values while those analyses are `incomplete`. An incomplete analysis must not affect candidate rank, and a digital rank must not be presented as a physical winner.

### 2026-08-14 Sol acceptance verdict

Package 8 is **ACCEPTED**. The rebuilt and installed implementation passes the focused 7/7 tests, the complete 72/72 suite, Xvfb/X11 native smoke, strict real-Wayland startup, strict real-Wayland `gtk-launch designstudio`, and a real-Wayland 20-cycle `PartDesignWorkbench` to `DesignStudioWorkbench` lifecycle run. The lifecycle receipt proves zero owned workspace objects after each close and exactly one controller, MDI document, product surface, chat dock, and context-toolbar set after every reopen. Installed Qt smoke and the launcher CLI import also pass.

The final build/install module SHA-256 is `6cfa1079f1ee7df15d7d170d951dda25c431d70cd199a2aa0756c16d00f48904`; the source/installed launcher SHA-256 is `952893f752b5da5de077b4be0a7f44734d9740fc026fb2415b0c25f273145e75`; the installed workbench tree matches source; and the generated application-menu entry plus both installed desktop copies share SHA-256 `8948b0d2c9ea219130f1b73547804521a6f7ce7806c92a32d0dae515ec390004` and pass `desktop-file-validate`.

Negative probes confirm that syntactically malformed shutdown JSON and a correctly digested request carrying a stale token each produce a digest-valid `status=fail` receipt and leave no live DesignStudio host. Candidate validation rejects an unbacked `ranking_status=physical_validated`, changing incomplete metric placeholders cannot affect rank, and every digital ranking remains `provisional`. Package 9 is released to **READY**; packages 10–12 remain blocked in order.

### Outcome

Make the installed desktop icon and application-menu entry open one stable DesignStudio window on Wayland and X11/Xvfb. Capture actionable startup logs instead of disappearing silently, and make the exact production launcher path a mandatory CTest/release gate.

### Required implementation

- Reproduce and symbolize the invalid access inside `modules/DesignStudioGui/AppDesignStudioGui.cpp::showWorkspace`. Inspect lifetime/parenting and order of `MainWindow`, central widget, MDI subwindow, docks, and toolbars. Do not hide the crash with a delayed timer, disabled autoload, standalone substitute window, or exception swallowing.
- Make embedded workspace construction transactional. On failure, restore host toolbar/dock state and destroy only objects created by the failed attempt. Never leave dangling `QPointer` state.
- Preserve the required architecture: one FreeCAD-hosted DesignStudio window, one embedded product surface, native FreeCAD workbenches, no nested `QMainWindow`, and no second standalone Qt application.
- Repair and register `modules/DesignStudioGui/tests/run-native-smoke.sh` in CTest using the installed `DesignStudioLauncher`/`DesignStudio` and the real dependency prefix. It must not call the absent `$prefix/bin/FreeCAD` compatibility name. A missing display may use Xvfb; a printed skip or Qt-harness-only result is failure.
- Add a Wayland launcher test on this laptop in addition to Xvfb/X11. It must prove process survival, visible main window, active `DesignStudioWorkbench`, embedded `DesignStudioWorkspaceDocument`, visible chat dock, and clean shutdown.
- Add a desktop-entry launch fixture that runs `desktop-file-validate`, verifies the resolved `Exec`, calls `gtk-launch designstudio`, observes a live window/process, and closes it cleanly. It must fail on crash, early exit, wrong executable, or stale install.
- Add structured startup logging under `${XDG_STATE_HOME:-$HOME/.local/state}/DesignStudio/startup.log` with rotation and no credentials. The launcher must record executable/build ID, platform, dependency prefix, startup phase, and fatal signal/exit status. `Terminal=false` must no longer make failures invisible.
- Validate writable config/cache locations before GUI startup and report a clear error. Do not silently use shared configuration across versions. Test clean profile, migrated profile, unwritable profile, and corrupted profile; the latter two must not segfault.
- Verify the dependency Python path imports `PySide6`, `shiboken6`, and the DesignStudio workbench before activation. A missing module must produce a diagnostic and nonzero exit, never partial activation.
- Install the repaired binary/module/launcher and regenerate both desktop entries from the same template. Confirm source and installed module/launcher hashes match.
- Add a test-only, digest-bound workspace-readiness receipt written only after the completed Qt/FreeCAD object tree proves one visible top-level host window, the active `DesignStudioWorkbench`, one visible `DesignStudioWorkspaceDocument`, a non-`QMainWindow` product surface, one visible chat dock, native CAD workbenches, and no duplicate DesignStudio host objects.
- Make the strict Wayland observer wait for and validate that receipt. It must reject a missing, malformed, stale, or failed receipt, detect early process exit or a fatal signal, and request clean shutdown after readiness.
- Make strict desktop acceptance launch only through `gtk-launch designstudio` and require the same post-activation receipt. Direct-launch and offscreen fallbacks may remain separately labelled CI diagnostics but cannot satisfy the strict real-Wayland gate.
- Add a repeated activation lifecycle fixture covering at least 20 activate/deactivate cycles followed by document close and reopen. After every transition require exactly one product surface, MDI document, chat dock, and DesignStudio context-toolbar set, with no stale `QPointer` state or crash.
- Correct candidate scoring so only metrics carrying trusted `pass` evidence contribute to the score numerator or denominator. `incomplete` metrics carry no numeric preference value; changing any incomplete placeholder or heuristic must not change rank. Label every digital ranking `provisional`, and do not emit a physical winner without valid physical-test observations.

### Permitted scope

- `modules/DesignStudioGui/AppDesignStudioGui.cpp`, its CMake/test files, and directly related embedded-UI lifetime code.
- `scripts/designstudio-launcher`, `scripts/DesignStudio.desktop`, `scripts/install-desktop-shortcut.sh`, and startup-log helpers/tests.
- `freecad/DesignStudioWorkbench/InitGui.py` only when necessary to make activation transactional without disabling it.
- `freecad/DesignStudioWorkbench/DesignStudio/constraint_candidates.py`, its contract/runtime tests, and the smallest receipt/UI compatibility changes required for fail-closed provisional ranking.
- Root/package CMake registration and the smallest required install changes.

Do not begin guided physical-validation UI, solver adapters, AP242 performance work, or manufacturing release in this package.

### Acceptance gates

1. The exact current crash command no longer receives SIGSEGV, and a symbolized regression test reaches a stable visible workspace.
2. Strict real-Wayland startup produces a valid post-activation workspace receipt proving every required UI assertion before the test requests clean shutdown.
3. Strict `gtk-launch designstudio` produces the same receipt with no direct-launch or offscreen fallback. Both desktop copies validate and resolve to the installed production launcher.
4. Xvfb/X11 native smoke passes independently and cannot pass by using `designstudio-qt-harness`.
5. At least 20 activation/deactivation cycles plus document close/reopen produce no duplicate host objects, stale pointers, early exit, or crash.
6. Clean, migrated, corrupt, and unwritable profile tests produce deterministic results; no profile case crashes. Missing PySide/dependencies produce a readable log, nonzero exit, and no partial GUI.
7. Candidate tests prove that incomplete reach, wall, manufacturability, mass, material-use, or other metrics cannot influence rank. Digital results remain `provisional`, and existing physical-design session formats remain readable.
8. Existing launcher CLI imports, FreeCAD CAD runtimes, Qt harness smoke, source/install hashes, and the complete suite continue to pass after installation.

### Required commands

```bash
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -V -R \
  'designstudio_native_desktop_startup|designstudio_desktop_entry|designstudio_profile_startup|designstudio_wayland_startup|designstudio_activation_cycle|constraint_candidates_contract|constraint_candidates_runtime'
modules/DesignStudioGui/tests/run-native-smoke.sh ${DESIGNSTUDIO_PREFIX}
desktop-file-validate ${XDG_DATA_HOME:-$HOME/.local/share}/applications/designstudio.desktop
desktop-file-validate $HOME/Desktop/DesignStudio.desktop
DESIGNSTUDIO_STARTUP_TEST_STRICT=1 \
QT_QPA_PLATFORM=wayland \
bash modules/DesignStudioGui/tests/run-wayland-startup.sh \
  ${DESIGNSTUDIO_PREFIX}
DESIGNSTUDIO_STARTUP_TEST_STRICT=1 \
QT_QPA_PLATFORM=wayland \
bash modules/DesignStudioGui/tests/run-desktop-entry-test.sh \
  ${DESIGNSTUDIO_PREFIX}
ctest --test-dir /tmp/designstudio-build --output-on-failure
cmake --install /tmp/designstudio-build --prefix ${DESIGNSTUDIO_PREFIX}
```

The strict Wayland and desktop-entry commands require the laptop's real Wayland session and the configured dependency prefix. A timeout kill, `activation_start` log line, direct launcher substitution, offscreen platform, printed skip, or CI fallback is not acceptance evidence. Only Sol may mark package 8 accepted and release package 9.

## Package 9 — Guided Physical Validation (`READY`)

### Outcome

Give the user a dedicated workflow for starting from photos or sketches, calibrating verified dimensions, placing locked hardware first, generating three editable candidates, exporting grip-only bucks, recording physical observations, comparing candidates, and initiating selected-face redesign. Correct provisional scoring so incomplete evidence never masquerades as preference evidence.

### Required implementation

Add strict public contracts:

- `design-studio.physical-test-plan/1`
- `design-studio.physical-test-observation/1`
- `design-studio.physical-candidate-decision/1`

The contracts must bind session ID, candidate semantic ID, candidate FCStd/B-Rep digest, grip-buck STEP/STL digest, material and print settings, anonymous participant ID, measured hand dimensions/size group, test order, timestamp, observer, and provenance. The fixed 1–5 form must include comfort, finger reach, pressure points, wrist angle, slip resistance, and preference. A decision must record coverage, aggregate method, exclusions, tie handling, and every source observation digest. Real selection requires at least one representative adult participant in each small, medium, and large hand group, with every participant testing all three candidates in recorded/counterbalanced order; larger studies may add participants without changing the contract.

Implement a `PhysicalDesignPanel` or equivalent dedicated guided UI with:

1. Workspace-confined photo/sketch upload and immutable SHA-256 evidence.
2. Known-length/drawing-scale calibration and explicit verified dimensions.
3. Inside-out, outside-in, or co-design workflow choice.
4. Locked hardware/mechanism/PCB/battery/display/connector/cable and human/service volumes.
5. Desire, material, process, wall, feature, envelope, and hand-objective capture.
6. Three-candidate generation and comparison with failed and incomplete gates visible per metric.
7. Grip-buck export and physical observation entry.
8. Object/FaceN selection handoff into `local-redesign/2` preview/approval/rollback.

Generate one lightweight grip-only FCStd, STEP, and STL buck per accepted candidate. Grip regions must come from explicit semantic regions or user-confirmed section planes; do not infer manufacturing geometry from image pixels. Each receipt must include source B-Rep and artifact digests.

Change candidate ranking so:

- Failed hard constraints reject a candidate.
- Incomplete metrics contribute no numeric preference and are identified separately from scored metrics.
- Digital ranking is labelled `provisional` until required physical observations exist.
- The highest valid physical-test score wins; `balanced` is only the deterministic tie-breaker or pre-test provisional selection.
- Synthetic test fixtures can exercise code but cannot produce a real decision receipt.

Wire the new operations through the existing approval queue and trusted FreeCAD dispatcher. Preserve all existing physical-design and local-redesign tools.

### Permitted scope

- `docs/schemas/*physical-test*` and the physical-design session schema only when backward-compatible optional references are required.
- `freecad/DesignStudioWorkbench/DesignStudio/constraint_candidates.py`, a new physical-validation module, live tools, commands, tests, and CMake registration.
- `app/QtDesignStudio/PhysicalDesignPanel.*`, `MainWindow.*`, `DesignChatPanel.*`, and directly related UI tests/CMake files.
- The smallest adjacent install/schema registration changes.

Do not implement exact thermal/structural/EMI solvers, production AP242 performance work, or manufacturing release in this package.

### Acceptance gates

1. Draft 2020-12 schemas reject invalid 1–5 values, stale candidate/buck digests, missing print provenance, duplicate participant/candidate observations, and synthetic evidence used for a real decision.
2. Photo-first, sketch-first, hardware-first, candidate comparison, and FaceN redesign are all reachable from the guided UI without authoring JSON manually.
3. Three digest-bound grip buck artifact sets survive FCStd reload and STEP round-trip; STLs are non-empty and candidate-specific.
4. No incomplete metric affects a final score. Tests demonstrate that changing a heuristic value for an incomplete metric cannot change rank.
5. Decision creation blocks with missing size-group coverage, unresolved hard failures, stale geometry, or no real observations.
6. Existing v1/v2 physical-design, vector-native CAD, local-redesign, and Qt product tests remain unchanged and pass.

### Required commands

```bash
/tmp/designstudio-venv/bin/python freecad/DesignStudioWorkbench/tests/test_physical_validation.py
${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "p='freecad/DesignStudioWorkbench/tests/physical_validation_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})"
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -R 'physical_validation|constraint_candidates|physical_design_flow_matrix|local_redesign|qt_physical_design_workflow|qt_product_ui_contract' --output-on-failure
ctest --test-dir /tmp/designstudio-build --output-on-failure
```

## Package 10 — Trusted Solver Adapters (`BLOCKED`)

### Outcome

Replace caller-asserted solver statuses with digest-bound results from registered deterministic adapters. Run long analyses asynchronously with progress, cancellation, cache reuse, and explicit unavailable/incomplete states.

### Required implementation

- Add strict `solver-adapter-manifest/1`, `solver-request/1`, `solver-result/1`, and backward-compatible `propagation-run/3` contracts.
- Register adapter executable/library identity, supported gate, version, input/output schema, unit system, trust class, timeout, and binary digest.
- Only the daemon may launch adapters. UI/chat callers submit requests, never result status.
- Bind each result to configuration, product-graph revision, semantic dependencies, B-Rep/mesh/electrical input digests, engine version, units, uncertainty, logs, exit status, and artifact digests.
- Treat existing `propagation-run/2` caller-supplied pass/fail objects as untrusted legacy input and therefore `incomplete`; preserve receipt readability.
- Provide deterministic local adapters for analyses the existing kernels can actually prove: collision/clearance, wall thickness, overhang/process angle, solid volume/material mass/center of gravity, and cable path bend radius when an authoritative path exists.
- Keep ergonomic physical comfort, thermal, structural, vibration, antenna, grounding, and EMI incomplete unless a registered exact engine or verified physical result is available.
- Execute through cancellable background jobs. Cache only on complete input/engine digests. Surface progress, affected nodes, changed scores, failures, assumptions, and rerun reasons in the Qt panel.
- Install `designstudio-agentd` through CMake so source and installed binary cannot drift.

### Acceptance gates

- Spoofed passes, stale digests, wrong units, changed engine binaries, path escapes, missing logs, timeout, cancellation, and partial output all fail closed.
- A user edit invalidates only dependent cached gates and reruns them with causal-path evidence.
- Missing adapters remain `incomplete`; no fallback heuristic signs off.
- Source and installed daemon SHA-256 values match after installation.

### Required commands

```bash
PATH=${CARGO_HOME:-$HOME/.cargo}/bin:$PATH cargo fmt --manifest-path services/designstudio-agentd/Cargo.toml -- --check
PATH=${CARGO_HOME:-$HOME/.cargo}/bin:$PATH cargo test --locked --all-targets --manifest-path services/designstudio-agentd/Cargo.toml
PATH=${CARGO_HOME:-$HOME/.cargo}/bin:$PATH cargo build --locked --release --manifest-path services/designstudio-agentd/Cargo.toml
/tmp/designstudio-venv/bin/python services/designstudio-agentd/test_solver_adapter_protocol.py services/designstudio-agentd/target/release/designstudio-agentd
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -R 'trusted_solver|product_propagation|qt_solver|qt_product_ui_contract' --output-on-failure
ctest --test-dir /tmp/designstudio-build --output-on-failure
```

## Package 11 — Production Semantic Assembly and 3D Performance (`BLOCKED`)

### Outcome

Prove native AP242 identity and responsive 3D interaction on the actual Agent Workflow Controller assembly, not only the two-box fixture.

### Authoritative fixture

- Project: `acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj`
- STEP: `acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.step` (10,583,560 bytes at audit)
- Component evidence: `acceptance/agent-workflow-controller/generated/step-models.csv`
- Release boundary: `acceptance/agent-workflow-controller/generated/manufacturing-release-status.json`

### Required implementation

- Add a production-scale native XCAF/AP242 test that does not create or substitute a semantic sidecar.
- Compare AP242 hierarchy, component/reference identities, placements, materials/appearances, and triangle ownership to the project/BOM evidence. Missing or duplicate mappings are visible failures.
- Verify bidirectional selection among 3D, PCB tree/canvas, BOM, product graph, FreeCAD tree, and property editor after cache reload.
- Key the display cache by source STEP content digest, tessellation settings, native semantic JSON digest, and reader version—not only path/size/mtime.
- Make stale loads genuinely cancellable or process-isolated; do not merely discard their eventual result.
- Implement interaction LOD: simplified mesh while orbit/pan/zoom is active, full detail restored after interaction stops, with no identity/color loss.
- Keep grids, mesh edges, hidden-edge display, and component colors as independent controls so the window-grill artifact can be disabled without flattening the assembly.
- Record machine-readable performance evidence. Acceptance targets are an interactive project shell within 1 second, no GUI heartbeat gap over 250 ms during first STEP import, warm cache load within 2 seconds, and p95 orbit frame time no greater than 33 ms on this acceptance laptop. If hardware/display limitations prevent a threshold, report measured evidence and return `REWORK`; do not relax it silently.

### Required commands

```bash
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -R 'qt_step_mesh_reader|qt_semantic_assembly|qt_semantic_selection|qt_mesh_projection_controls|qt_pcb3d_production_fixture|qt_pcb3d_performance' --output-on-failure
/tmp/designstudio-venv/bin/python app/QtDesignStudio/tests/step_mesh/verify_agent_controller_semantics.py \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller.dsproj \
  acceptance/agent-workflow-controller/generated/agent-workflow-controller-board.step
ctest --test-dir /tmp/designstudio-build --output-on-failure
```

## Package 12 — Manufacturing Qualification and Release Hardening (`BLOCKED`)

### Outcome

Join the selected physical candidate, trusted solver evidence, production semantic assembly, controlled fabrication/assembly profile, and external qualification evidence into one fail-closed manufacturing-release decision.

### Required implementation

- Add a versioned physical-product release manifest binding electronics, enclosure FCStd/STEP/STL, authoritative drawings, semantic assembly, selected candidate decision, solver receipts, BOM, fabrication profile, CAM, material/process evidence, and every digest.
- Add guided import/review for a named fabricator and assembler, controlled stackup/DFM source, revision/date, impedance and tolerance rules, CAM conventions, and approval identity.
- Preserve the existing production exporter guard. Placeholder, synthetic, expired, unsigned, stale, or incomplete profiles remain blocked.
- Require real physical prototype observations, battery/runtime tests when applicable, safety/cell/transport evidence, and regulatory/RF evidence when applicable. `not_applicable` requires a reason and reviewer.
- Re-run native verification after any selected candidate, solver, BOM, stackup, or manufacturing-profile change.
- Generate a release package only when every required gate passes. Otherwise generate a complete blocked-status report and no production directory.
- Re-run installation verification, desktop launcher smoke, installed workbench comparison, daemon hash comparison, CAD runtimes, and the full CTest suite.

### External boundary

Software implementation can be accepted using clearly marked synthetic negative fixtures, but the real Agent Workflow Controller must remain blocked until the user supplies real grip-test observations and controlled manufacturer/qualification inputs. No agent may synthesize those records.

### Required commands

```bash
/tmp/designstudio-venv/bin/python tools/validate_physical_product_release.py \
  acceptance/agent-workflow-controller/generated/manufacturing-release-status.json
cmake --build /tmp/designstudio-build -j2
ctest --test-dir /tmp/designstudio-build -R 'manufacturing_qualification|industrial_release_gate|manufacturer_production_package|physical_validation|trusted_solver|semantic_assembly' --output-on-failure
ctest --test-dir /tmp/designstudio-build --output-on-failure
cmake --install /tmp/designstudio-build --prefix ${DESIGNSTUDIO_PREFIX}
QT_QPA_PLATFORM=offscreen ${DESIGNSTUDIO_PREFIX}/bin/designstudio-qt-harness --smoke-test
QT_QPA_PLATFORM=offscreen timeout 30s ${DESIGNSTUDIO_PREFIX}/bin/DesignStudioLauncher -c "import DesignStudio; print('DESIGNSTUDIO_LAUNCHER_IMPORT_OK')"
```

## Sol review gate

For every implemented package, Sol must independently:

1. Inspect the actual diff and confirm every changed file is inside package scope.
2. Run every package command and the complete suite from a reproducible build directory.
3. Inspect generated schemas, receipts, FCStd/STEP/STL/SVG/DXF artifacts, logs, and digests.
4. Run negative probes against stale geometry, spoofed evidence, path escapes, missing adapters, cancellation, rollback, and release guards.
5. Record `ACCEPTED` or `REWORK` with precise evidence. Release exactly one next package only after acceptance.

## Executor completion report

```text
Package:
State requested: IMPLEMENTED (never ACCEPTED)

Changed files:
- path — purpose

Public contracts:
- schema/tool/operation — compatibility note

Commands and exact outcomes:
- command — exit code — pass/fail summary

Generated evidence:
- artifact — byte size — SHA-256 — validation result

Negative/fail-closed probes:
- case — expected rejection — observed rejection

Installation state:
- source/installed workbench comparison
- source/installed daemon hashes
- launcher/Qt smoke output

Known failures or external blockers:
- explicit item, or none

Remaining risks:
- explicit item, or none
```
