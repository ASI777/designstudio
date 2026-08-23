# Changelog

## 2026-06-14 — Layer-count advice + custom board shapes

Two design-ergonomics features on top of E21 and the board model.

- **Layer-count recommendation (`Advisor/LayerPlanner.cs`):** estimates the copper
  layers a design calls for from the exported state — signal-routing demand (the
  densest component's BGA escape + overall net count), whether the board carries
  high-speed channels or DDR (stripline references push routing inward), and the
  number of power domains (plane pairs) — returning an even, fab-realistic count
  with a transparent rationale. `SiPiAdvisor` now emits a `set_layers` action (new
  `AdviceOp.SetLayers`, in the advice contract + `ADVISOR_PROMPT.md`) when the
  board has fewer layers than the parts call for.
- **Custom board shapes (`Model/BoardOutline.cs`):** the board outline is now a
  polygon, with builders for rectangle, square, circle and rounded-rectangle (plus
  arbitrary polygons), point-in-polygon containment and a bounding box.
  `BoardDocument` gains `OutlinePolygon` / `Shape` / `SetBoardOutline` /
  `SetBoardShape` / `EffectiveOutline`; width/height track the bounding box so the
  router keep-out and view scaling stay correct. The outline flows through the
  Clipper2 plane generator (planes fill the real shape), the Gerber edge layer, the
  canvas (`PcbCanvas` draws the polygon), `ProjectIO` round-trip, and a
  `BoardOutline.OutsideOutline` board-edge check. Board Setup gains a shape picker
  (rectangle / square / circle / rounded) and the copper-layers field is now an
  editable 2–64 entry (was a dropdown capped at 16).
- Tests: `LayerShapeTests.cs` — the planner scales from a small 2-layer job to a
  many-layer dense DDR/BGA board and the advisor recommends or stays quiet
  accordingly; `set_layers` round-trips the contract; circle bounding box +
  containment, `SetBoardShape`, the outside-outline check, and a project round-trip
  of a circular board.
- `docs/board-shapes.md` documents the outline model.


## 2026-06-14 — E21: SI/PI-aware design advisor

The roadmap's AI tier — the design loop now reasons about SI/PI sign-off, not
just the netlist. Builds on the circuit-state schema widening (stackup + S2–S13
results) with the design-space reasoning and context scaling.

- **`SiPiAdvisor.cs` (step 2 — teach it the design space):** a deterministic
  recommender over the exported `CircuitState` that maps each sign-off failure to
  the physically correct fix — a closed eye with an un-backdrilled via stub →
  `backdrill`; a loss-limited eye (≤ −10 dB) → `set_eq`; a rail over its Z target →
  `add_decap` at the antinode (sized by the resonance) + `move_decap` for a decap
  worsening an anti-resonance; a DDR negative hold margin → `length_tune` the early
  bit, a negative setup margin → `set_eq`; an otherwise-unexplained closed eye →
  `stackup`/`reference`. Typed `AdviceOp`/`AdviceAction`/`AdviceReport`. This is the
  ground truth the LLM is held to — the right SI/PI action falls out with no model
  call (the E21 exit criterion).
- **`AdviceContract.cs`:** serialises the report and parses + validates a model's
  `design-studio.advice/1` response into the same typed `AdviceReport` — malformed
  JSON, wrong schema, or an unknown op throw `AdviceFormatException` instead of
  silently dropping actions. The advice contract gains the SI/PI ops (`backdrill`,
  `move_decap`, `add_decap`, `set_eq`, `length_tune`, `stackup`, `reference`).
- **`ContextScope.cs` (step 3 — scale):** keeps the hot set (failing eyes/rails,
  missed DDR lanes, unrouted nets) in full and caps the healthy remainder to a
  `ContextBudget`, recording omissions in `context_note` so a thousand-net board is
  tractable. A board within budget is untouched.
- **`CircuitStateExport`:** refactored into `BuildState` (returns the typed
  `CircuitState`) + `Build` (serialises it), so callers can build → scope →
  serialise or run the advisor directly; `CsChannel.HasViaStub` and
  `CircuitState.ContextNote` added; the embedded response contract and
  `docs/circuit-advisor/ADVISOR_PROMPT.md` extended with the SI/PI ops + fields.
- Tests: `PhaseE21Tests.cs` — stub eye → backdrill, loss eye → EQ, rail over
  target → add/move decap, DDR hold → length-tune, DDR setup → EQ, a passing board
  → no SI/PI actions; the advice contract round-trips and rejects malformed/wrong-
  schema/unknown-op input; context scope keeps failing nets and caps the rest.
- `docs/si-pi-advisor.md` documents the three steps.


## 2026-06-14 — Phase S13: solver scale-out + C++ hot path (E20)

The last Class-B phase — making the SI engines practical on real board sizes.

- **Dense-solve hot path (`DenseSolver.cs` + `core/src/linalg.cpp`):** the MoM
  RLGC extraction and FDM PDN solver spend almost all their time in one place — a
  dense LU factor + solve of an N×N system (N≈400), per frequency × per net.
  `DenseSolver.SolveColumns` factors once and solves every excitation; the managed
  LU is the always-correct reference, and when the native core library is present
  the factor+solve runs in C++ (`dc_dense_lu_solve`). Identical pivoting + singular
  clamp ⇒ the two paths agree to precision (verified by a C++ unit test and a
  managed native-on/off parity test). `MoM2D` now routes through `DenseSolver`
  instead of carrying its own LU. `NativeMath` probes for the library once and
  falls back silently — nothing here is required for correctness, only speed.
- **Parallel sweep (`SweepScheduler.cs`):** an order-preserving parallel map over
  the independent nets — deterministic output regardless of completion order, a
  live `IProgress<SweepProgress>` completed-net count, and cooperative cancel
  (`OperationCanceledException` on a cancelled token). `RunKeyed` returns an
  id→result map.
- **Board-wide cache (`ExtractionCache.cs`):** per-net results keyed on net id +
  frequency-grid signature (64-bit FNV) + `BoardDocument.Version`. Any edit bumps
  the version so the touched board recomputes while unchanged nets are hits.
  Thread-safe, backs the parallel sweep directly.
- **`ChannelExtractor.ExtractManyParallel`:** warms lazy document state on the
  first net single-threaded, then fans the rest across all cores through the
  scheduler + cache, returning a net-id → S-parameter map. The path a 24-layer
  board's controlled nets run through.
- C API: `dc_dense_lu_solve` (in `core/include/designcore/linalg.h`,
  `core/src/linalg.cpp`, exported via `c_api`, built into the CMake target, with a
  `core/tests` unit test).
- Tests: `PhaseS13Tests.cs` — DenseSolver recovers a known solution + multi-RHS
  inverse, native/managed extraction agree, the scheduler preserves order +
  reports progress + cancels, the cache hits on the same version and misses after
  a version bump (and distinct grids get distinct signatures), and the parallel
  extract matches the serial one while the cache reuses unchanged nets and
  recomputes after an edit.
- `docs/scale-out.md` documents the hot path, scheduler, cache and entry point.

Class B is complete. Remaining roadmap item: **E21** (SI/PI design advisor).


## 2026-06-14 — Phase S12: PAM4 / differential signalling (E15) + statistical crosstalk eye (E16)

Closes the high-speed-SerDes half of Class B: 112G-per-lane PAM4 and a
crosstalk-aware statistical eye.

- **E15 — PAM4 (`StatisticalEye.cs`):** four-level signalling (`Pam4Levels`
  −1, −1/3, +1/3, +1) with three stacked sub-eyes. `Pam4SubEyesAt` folds the ISI
  taps (and any aggressors) as 4-level random data into a level PDF and reads each
  sub-eye as the gap between the BER tails of the two adjacent level
  distributions; `Pam4Analyze` sweeps the sampling phase and reports the three
  sub-eyes, their worst-case min, the RLM level-linearity ratio and the centre
  phase (`Pam4Eye`). Validated: a lossless channel gives three equal 2/3-height
  sub-eyes (RLM 1); ISI closes each by 2·Σ|tap|.
- **E16 — statistical crosstalk-aware eye:** `EyeHeightAt`/`Analyze` (NRZ) and the
  PAM4 path take an `aggressors` argument — each aggressor's coupled cursors are
  extra random-data taps folded into the victim's ISI PDF, so a switching
  aggressor closes the eye by the measured coupled amount (an NRZ victim closes by
  2·Σ|coupled cursor|, validated).
- **E15 — PAM4 link (`LinkSimulator.SimulatePam4`):** drives a channel transfer
  `S21(f)` (treating `DataRateGbps` as the symbol rate in GBaud — 56 for 112G)
  through the same driver/CTLE/FFE/IFFT pulse machinery and reports the three
  sub-eyes + min + RLM (`Pam4Result`). A routed differential pair feeds it via
  `ChannelAssembly.SegmentFromNet` (the P-net is the differential-through).
- **E15 — PAM4 compliance (`ComplianceMask.cs`):** `Pam4Mask`/`Pam4Masks` with
  112G (56 GBd), 100G (53.125 GBd) and 56G (28 GBd) presets — the keep-out is the
  worst sub-eye opening plus an RLM floor; `Check` returns a per-standard
  pass/fail.
- Tests: `PhaseS12Tests.cs` — lossless PAM4 three equal sub-eyes, ISI/aggressor
  close them, RLM unity on a flat pulse, NRZ eye closes by the coupled amount,
  more aggressors close it further, the mask passes a clean eye and fails a closed
  one, and a 112G PAM4 differential lane reports three sub-eye heights plus a
  per-standard verdict.
- `docs/pam4-crosstalk.md` documents the PAM4 sub-eye, crosstalk folding and mask.

Class B remainder: S13 (the sparse/C++ scale-out).

## 2026-06-14 — Phase S11: macromodel + multi-board assembly (E18) + vendor AMI (E17)

The backplane-channel and SerDes-sign-off half of Class B.

- **E18 — multi-board channel assembly (`ChannelAssembly.cs`):** cascades an
  ordered list of 2-port S-parameter blocks (board segments extracted from a
  routed net via `SegmentFromNet`, plus vendor connector/cable `.s4p` imported
  through the existing Touchstone reader) by multiplying their ABCD matrices, and
  feeds the result to a new `LinkSimulator.SimulateChannel(S21, …)`. A backplane
  is daughtercard → connector → backplane → connector → daughtercard; more
  connectors close the eye. `LinkSimulator.PrecursorEnergyFraction` is a causality
  metric on the resulting pulse response.
- **E18 — `VectorFit.cs`:** a guaranteed-causal rational macromodel —
  `H(s)=e^{-sτ}(d+Σ rk/(s−pk))` with all poles in the left half-plane. The bulk
  delay is pulled from the phase slope and the residual fit with a real-pole bank
  by least-squares, so the impulse is **exactly zero before τ** (validated). Fixed
  poles give ~10 % accuracy on skin-effect channels (full pole-relocation is the
  accuracy refinement); the causality guarantee is exact.
- **E17 — vendor IBIS-AMI (`AmiModel.cs`):** a `.ami` parameter-tree parser
  (nested-parenthesis `(name (Usage…)(Type…)(Value…))`), `ToEqualizer` mapping the
  Model_Specific CTLE/FFE/DFE onto the in-house equalisers, `ParametricAmi`
  (`IAmiModel`, the managed default), and `NativeAmi` (the P/Invoke wrapper around
  a real vendor binary — present for completeness; loading an actual vendor `.dll`
  is a platform integration). A parsed AMI model's equalisation reopens a lossy eye.
- Tests: `PhaseS11Tests.cs` — vector-fit recovers the delay + is causal + tracks
  the response; a backplane assembled from connector blocks produces a causal eye
  and more connectors close it; the `.ami` tree parses into an equaliser and that
  equalisation reopens a lossy eye.
- `docs/backplane-ami.md` documents the assembly, macromodel and AMI flow.

Class B remainder: S12 (112G PAM4 + statistical crosstalk) and S13 (C++ scale-out).

## 2026-06-14 — Phase S10: HDI — blind/buried/stacked microvias (E11)

First Class-B phase — the HDI interconnect dense laptop/AI/backplane boards use.

- **`ViaItem.ViaType`** (Through/Blind/Buried/Microvia), round-tripped through
  `ProjectIO`. Through/Blind/Buried are mechanical; Microvia is laser.
- **`HdiVias.cs`:** `Resolve` classifies a via from the explicit flag or geometry
  (a 1-layer span with a ≤0.15 mm drill is a microvia); `AspectRatio`,
  `BarrelLengthMm`; `Stacks` finds co-located microvias on consecutive spans;
  and DRC **rule 25** — microvia aspect ratio (>1.0), single-build-up-layer span,
  capture-pad annular ring (<50 µm), mechanical aspect (>12), and stacked-microvia
  depth (>3) / copper-fill note.
- **`FabExporter.DrillSpanReport`:** the per-span drill program, each span
  labelled **LASER** (microvia) or **MECH** (through/blind/buried) with its 1-based
  layer span, drill and count — what the fab needs to set up the laser and
  mechanical drill passes. (The Excellon files already group by span.)
- **SI:** no `ViaModel` change needed — a laser microvia is too short to resonate
  (a capacitance-dominated transition), and a stack is the cascade of those short
  transitions, which `ChannelExtractor` already multiplies along the path.
- Tests: `PhaseS10Tests.cs` — through/blind/buried/microvia classification,
  stacked-microvia detection, a **stacked-microvia transition extracts**
  passive/low-loss S-parameters, the drill-span report lists laser + mechanical
  spans, a bad microvia aspect is flagged and a clean HDI stack passes, and
  `ViaType` round-trips through `ProjectIO`.
- `docs/hdi-microvias.md` documents the via classes, the model, and the drill report.

Class B continues with S11–S12 (112G PAM4 / statistical crosstalk / vendor AMI /
multi-board) and S13 (C++ scale-out).

## 2026-06-13 — Phase S9: full DDR5 channel topology (E19)

Completes Class A — a dense multi-socket DDR5 board signs off end to end.

- **`DdrSubsystem.cs`:** the channel/socket hierarchy on top of the S6 byte-lane
  engine. `Channel` = data byte lanes (DQ + DQS) + an optional fly-by
  command/address bus + a DIMMs-per-channel count; `Analyze` rolls margins up
  per channel and across the whole subsystem (one socket or several — just list
  every channel) into a **per-channel margin table + overall pass/fail**.
  - each data lane → `DdrTimingEngine.AnalyzeByteLane` (the validated S6 path);
  - the **CA fly-by bus** is the same setup/hold problem against the *clock* at
    half the data rate (1 command per CK) with command setup/hold (tIS/tIH), so
    it reuses `AnalyzeByteLane` with a half-rate spec — and comes out with the
    expected extra margin;
  - **2DPC** adds the idle rank's reflection/ISI as deterministic jitter (≈10 %
    UI), closing every lane in the channel.
- Tests: `PhaseS9Tests.cs` — matched single channel passes (with CA analysed),
  multi-channel per-channel table, a skewed channel fails and is named, 2DPC
  reduces margin vs 1DPC, and the CA bus is comfortably open at half rate.
- `docs/ddr5-subsystem.md` documents the topology and roll-up.

With S7 (stackup/reference) + S8 (split-plane PDN/thermal) + S9 (DDR5 topology),
**Class A — dense multi-socket DDR5 motherboards — is designable and sign-off-able
end to end.** Class B (HDI / 112G PAM4 / backplanes) is S10–S13.

## 2026-06-13 — Phase S8: split-plane FDM PDN (E13) + electro-thermal IR (E14)

Second high-end-roadmap phase — power delivery on the real, irregular planes
dense boards actually use, and the heating of high-current rails.

- **E13 — `PlanePdnFdm.cs`:** a 2-D finite-difference plane-pair impedance solver
  over the *actual* plane copper (`PlaneShape.Fill`, cell-centred mesh). Series
  branches `2·Rs + jω·μ0·d`, shunt cell capacitance `jω·ε0εr·h²/d·(1−j·tanδ)`,
  decaps/VRM as node admittances; `Z(f)` at the load = the solution of `Y·V = I`.
  Handles **splits, L-shapes and voids** — disconnected copper simply isn't
  reachable, so a port sees only its own island. `PdnAnalyzer.AnalyzeSpatialFdm`
  picks it for real planes; `PlaneCavity` (modal) stays for rectangles. Validated:
  matches the modal solver to **<0.1 %** on a rectangle, a split island raises Z
  ~2×, a decap crushes Z at its SRF.
- **E14 — electro-thermal IR drop (`IrDrop.AnalyzeElectroThermal`):** the DC IR
  mesh coupled to a 2-D thermal solve — per-cell Joule heat → temperature (copper
  conduction + convection) → `ρ(T)=ρ0(1+α(T−25))` → re-solve, iterated. Reports
  worst droop, the **hottest plane region**, and peak current density, with a
  soft ρ-cap so a concentrated current can't run away. Validated: the coupled
  droop exceeds the isothermal droop by the `1+α·ΔT` factor (e.g. 9.9 → 11.8 mV
  at 77 °C). New DRC rule 24 (droop-over-budget / plane-overheat).
- Tests: `PhaseS8Tests.cs` — FDM matches modal on a rectangle, split/L-shape
  handling, decap effect; electro-thermal heats + droops more than isothermal,
  low current barely heats.
- `docs/split-plane-pdn-thermal.md` documents both solvers.

## 2026-06-13 — Phase S7: layer-count scale + reference-plane integrity (E10 + E12)

First phase of the high-end roadmap (`docs/ROADMAP-HIGHEND.md`) — what dense
multi-socket DDR5 / AI / backplane boards need before anything else.

- **E10 — layer scale + multi-plane stackup:** `SetCopperLayers` now goes to 64
  (was 16). `EnsureStackup` is unchanged for ≤16 layers (the classic two-plane
  layout — zero regression), but for >16 it lays down a proper thick stackup:
  reference planes on a 3-layer cadence so **every signal layer is adjacent to a
  plane**, signal outer layers, and 2 oz plane copper. New
  `StackupLayer.CopperWeightOz` and `ImpedanceEngine.ReferencePlanes(doc, layer)`
  → the nearest plane indices above/below a signal (its reference planes).
- **E12 — reference-plane integrity & return path (`ReferenceIntegrity.cs`):**
  per-controlled-net `Audit` (is the reference continuous?) plus DRC **rule 18**
  (a controlled trace crossing a gap/void/split in its reference plane — tested
  against the real plane copper with an even-odd point-in-polygon over
  `PlaneShape.Fill`) and **rule 19** (a via whose signal reference plane *changes*
  with no stitching via within 1.5 mm). Kept separate from the main DRC run so
  existing rule counts are untouched.
- Tests: `PhaseS7Tests.cs` — scale to 64, classic ≤16 layout preserved, a
  24-layer stackup gives every signal an adjacent 2 oz reference, void crossing
  flagged (and a clean trace passes), reference-change-without-stitch flagged
  (and a stitch via clears it), and the **S7 exit criterion**: every controlled
  net on a 24-layer board has a continuous reference.
- `docs/stackup-reference-integrity.md` documents the cadence and the checks.

## 2026-06-13 — Phase S6: equalisation (E4 step 4) + DDR5 timing engine (E7)

The final roadmap phase — the equalisers that make multi-gigabit links close, and
the per-byte-lane DDR5 margin engine that signs a memory bus off.

- **E4 step 4 — `Equalization.cs`:** Tx FFE (FIR pre-emphasis convolved into the
  pulse response), Rx CTLE (one-zero/two-pole peaking applied in the frequency
  domain), and Rx DFE (first N post-cursor taps cancelled in the statistical eye).
  Wired into `LinkSimulator.Options.Eq`. `IAmiModel`/`BuiltInAmi` is the IBIS-AMI
  vendor hook — a compiled `.dll`/`.so` wrapper drops in behind the same interface
  (the P/Invoke wrapper is a platform integration; the built-in equalisers are the
  default). Validated: a closed 16 Gb/s lossy eye reopens 175 → 628 → 736 → 963 mV
  through CTLE → +DFE → +FFE.
- **E7 — `DdrTimingEngine.cs`:** per-DQ-bit DDR5 setup/hold margin combining the
  (equalised) statistical-eye width (S4 + S6 over the S2/S3 channel), DQ-to-DQS
  skew (`DelayEngine`), crosstalk-induced jitter (`Crosstalk`), SSN/SSO from the
  PDN cavity (E6) at the buffer, and the Rj/Dj budget:
  `setup = ½·eye − tDS − skew − jitter`, `hold = ½·eye − tDH + skew − jitter`.
  Output: a per-bit table and a lane pass/fail — "DQ3 fails setup by 4 ps at
  6400 MT/s." DDR5-6400 / -4800 presets.
- **Server-grade DDR5 capstone:** `PhaseS6Tests.cs` builds a length-matched
  Megtron-6 stripline byte lane (8× DQ + DQS) on a planed inner layer with a
  decoupled VDDQ PDN and asserts the lane passes with positive setup/hold margins
  and clears the DDR5-6400 mask; a skewed bit correctly fails and is named.
- Validated end-to-end: the matched server lane passes (setup ≈ +20 ps), a 14 mm
  skewed DQ fails by tens of ps. Tests also cover CTLE/FFE/DFE math, the AMI hook,
  and EQ reopening a lossy link.
- `docs/equalization-ddr-timing.md` documents the equalisers and the margin model.

This completes S1–S6: geometry/material accuracy (S2), vias (S3), the link eye
(S4), PDN + masks (S5), and equalisation + DDR sign-off (S6).

## 2026-06-13 — Phase S5: spatial PDN cavity solver (E6) + compliance masks & jitter (E5)

- **E6 — `PlaneCavity.cs`:** rectangular plane-pair PDN solved with the cavity-
  resonator modal expansion. Gives the port Z-matrix; the (0,0) mode is exactly
  the plane capacitance and `k=k_mn` are the plane resonances
  `f_mn=(c/2√εr)√((m/a)²+(n/b)²)`. Decaps are ports terminated by their series
  R-L-C and reduced out by the multiport Schur complement, with the VRM in
  parallel — so a decap only helps where it actually sits. Outputs Z(f) at the
  load, the plane resonances, a **per-decap effectiveness ranking** (band-worst-Z
  change if removed — negative flags a decap that makes an anti-resonance worse),
  and a **placement hint** (the highest transfer-Z spot at the worst frequency).
  `PdnAnalyzer.BuildCavity`/`AnalyzeSpatial` resolve the rectangle, load and decap
  coordinates from the board; `CheckSpatial` raises DRC rule 22 with the hint.
- **E5 — `ComplianceMask.cs` + `JitterDecomposition.cs`:** per-standard eye masks
  (PCIe Gen3/4/5, DDR5-6400, generic) checked against the S4 statistical eye, and
  a jitter split (DDJ from the ISI-limited eye, plus injected Rj/Dj → dual-Dirac
  `TJ(BER)=DDJ+Dj+2·Q·Rj`). `LinkSimulator.CheckCompliance(net, standard)` runs
  the link at the standard's rate/BER and returns the mask + jitter report.
- Validated: (0,0) mode = plane C to ~1 % (2.12 vs 2.14 Ω), self-impedance peak
  on the analytic `f_10` (1.463 GHz, 0 % error), a decap crushes Z near its SRF
  (≈18 mΩ vs ≈1.4 Ω), and the VRM–decap anti-resonance falls out naturally.
- Tests: `PhaseS5Tests.cs` — plane-cap, resonance, decap reduction, effectiveness
  ranking, placement hint; mask pass/fail per standard; jitter decomposition;
  and `BuildCavity`/`CheckCompliance` end-to-end. Exit criterion met: a board's
  PDN gets spatial decap analysis and a per-standard mask pass/fail.
- `docs/pdn-cavity.md` documents the cavity model, decap termination and masks.

## 2026-06-13 — Phase S4: link simulator — IBIS + statistical eye (E4 steps 1–3)

Turns the frequency-domain data (S2 RLGC + S3 vias) into the pass/fail number
engineers actually sign off on: a BER eye. New `Model/LinkSim/`:

- **`IbisModel.cs` (E4 step 1):** tolerant IBIS 7.x parser — `[Model]` type,
  `C_comp`, `[Voltage Range]`, `[Ramp]`, `[Pulldown]/[Pullup]` I-V, `[Package]`
  R/L/C, with engineering-unit normalisation — and a derived linear driver
  (swing, edge rate, output R) for the LTI pipeline.
- **`Fft.cs` + impulse response (E4 step 2):** radix-2 FFT; the channel `S21(f)`
  (now sampled on a linear grid via `ChannelExtractor.ExtractAt`) is taken to a
  causal single-bit (pulse) response by Hermitian inverse FFT and convolution
  with the driver-shaped bit.
- **`StatisticalEye.cs` (E4 step 3):** peak-distortion / statistical eye — cursors
  from the pulse response, ISI distribution by convolving each cursor's ±pmf,
  Gaussian receiver noise, and BER tails → eye height, eye width, worst-case
  opening, plus jitter (Rj/Dj) horizontal closure. No billion-bit simulation.
- **`LinkSimulator.cs`:** orchestration — net → channel S-params + driver →
  pulse response → eye, with insertion loss and a mask pass/fail. (UI
  "Simulate link…" hook is the next step.)
- Validated against analytic eyes: a lossless channel opens to the full ±swing
  (≈2.0 V for 1 V swing), a known cursor set reproduces the worst-case opening
  `2·(c0−Σ|ck|)` exactly (1.20), added noise closes the eye by `Q·σ` (σ=0.1 →
  0.595 V, Q≈7.03), and the eye shrinks monotonically with loss, length and data
  rate — the behaviour the Gen4 reference-channel exit criterion checks.
- Tests: `PhaseS4Tests.cs` — FFT round-trip + flat-spectrum impulse, IBIS golden
  parse + driver derivation + unit parser, eye math vs analytic, and end-to-end
  link sims (open short channel, closure with length/rate, mask fail, IBIS swing).
- `docs/link-sim.md` documents the pipeline; AMI equalisers + PAM4 are E4 step 4
  (Phase S6).

## 2026-06-13 — Phase S3: physics-based via model (E3 step 1)

Replaces the lumped L-C π via with the analytical "ladder" the roadmap calls for
(coaxial barrel + open stub + plane coupling), valid to ~20 GHz — enough to put
the via stub suckout (the resonance that kills DDR5/Gen4 eyes) in the right place.

- **`ViaModel.cs` (new):** the barrel-in-antipad is a coaxial line
  `Z = (η0/2π√εr)·ln(d_antipad/d_drill)`; the through section (entry → deepest
  routed layer) is a cascade of per-layer coaxial TL sections; the unused barrel
  below it is an **open-circuited stub** whose input admittance `Y = tanh(γℓ)/Z`
  shunts the exit node and produces the insertion-loss suckout at
  `f ≈ c/(4·ℓ_stub·√εr)`. Pad/antipad capacitance (Johnson's via formula) loads
  each end; dielectric + barrel loss set a realistic resonance Q. Uses the S2
  causal `Dk(f)` per layer. Exposes `StubResonanceHz`, `Z0ViaOhm`, `StubLengthMm`.
- **`ViaItem.AntipadMm`** (default 0 = auto from pad + net-class clearance) sets
  the coaxial via impedance; round-trips through `ProjectIO`.
- **`ChannelExtractor`:** the via block now evaluates `ViaModel.Abcd(f)` (built
  once per via from the deepest routed layer) when the stackup is live, replacing
  the L-C π — which remains the fallback. Backdrill depth feeds the stub length,
  so a backdrilled via correctly loses its suckout.
- Validated: extracted notch within **~0.1 %** of the analytic quarter-wave
  (e.g. a 9-layer FR-4 stub resonates at 16.89 GHz, 32 dB deep); backdrilling
  collapses it to ~1 dB; through vias with no stub stay flat — comfortably inside
  the S3 exit criterion of 10 % to 20 GHz.
- Tests: `PhaseS3Tests.cs` — quarter-wave resonance placement, closed-form match,
  backdrill removal, deeper-stub-resonates-lower, flat through via, antipad→Z,
  end-to-end channel suckout near `f_res`, and antipad ProjectIO round-trip.
- `docs/via-model.md` documents the formulation and references.

## 2026-06-13 — Phase S2: causal materials + MoM RLGC field solver

The frequency-domain accuracy layer the roadmap calls for (E2 + E1), pushing the
extracted data from "good to ~5 GHz" toward credible Gen4/DDR5 design to 50 GHz.

- **E2 — causal materials (`Materials.cs`):**
  - `DjordjevicSarkar` wideband-Debye model fitted to each laminate's datasheet
    points (anchored near 10 GHz). Replaces the non-causal log-linear Dk/Df
    interpolation for solver use — `MaterialDef.DkCausalAt/DfCausalAt/EpsComplexAt`
    give Kramers-Kronig-consistent dispersion (right phase ⇒ right eye).
  - `SurfaceRoughness`: Hammerstad-Jensen and cannonball-Huray conductor-loss
    factors `Ksr(f)` from an RMS roughness now carried on `MaterialDef`
    (`roughness_rq_um`, `roughness_model`); built-ins populated (FR-4 2 µm …
    Tachyon 0.3 µm Huray).
  - `Causality.KramersKronigResidual` numerically verifies the fitted model.
- **E1 — MoM RLGC solver (`MoM2D.cs`):** boundary-element (method-of-moments)
  extraction over conductor + dielectric surfaces with ground planes by images.
  Outputs the per-unit-length **L, C, R(f), G(f)** matrices, modal decomposition
  (even/odd → differential/common Z and εeff), and `FieldSolver2D`-compatible
  `Microstrip`/`Stripline` entry points. R(f) via Wheeler's incremental-inductance
  rule (correct √f skin scaling, includes ground-plane loss) × roughness; G(f)
  from the causal tanδ and the dielectric filling factor. Validated to ~1 % on Z0
  and ~1.5 % on εeff vs Hammerstad-Jensen, **exact** for homogeneous/stripline
  εeff, with the correct finite-thickness Z0 reduction HJ's formula omits.
  (Pure-managed; flagged as the C++ `core/mom2d.cpp` hot-path migration candidate.)
- **Integration (`ChannelExtractor.cs`):** with a live stackup the channel cascade
  now uses MoM RLGC(f) + causal materials + roughness per segment (cached per
  layer/width, solved on a 6-point grid and interpolated) instead of closed-form
  Z0 + flat-tanδ + bare skin. Falls back to the original path without a stackup.
- Tests: `PhaseS2Tests.cs` — Z0/εeff within ±2 % of Hammerstad references,
  stripline εeff = εr, finite-thickness Z0 trend, coupled odd/even split, causal
  anchor reproduction + dispersion + Kramers-Kronig, roughness monotonicity, RLGC
  self-consistency (Z0=√(L/C), R∝√f, G∝f), and end-to-end channel loss growth.
- `docs/mom-rlgc-engine.md` documents the formulation, the references, and the
  one subtlety that bites: dielectric interface segments must be oriented
  consistently or the bound charge cancels.

## 2026-06-12 — Closed AI design loop (component/2 + circuit-state export)

- **design-studio.component/2** (v1 still accepted): parametric `footprint.bga`
  grid with JEDEC row lettering — validated against the APQ8064 datasheet's
  28-column ball map — expanded deterministically (every populated ball must
  have a symbol pin; DNC = nc); `electrical.parameters` (any min/typ/max
  table), `thermal`, `power_domains`, `package_3d` (height/standoff drives the
  3D view), component `function`/`interfaces`. Full electrical payload is
  preserved on the library part (ElectricalJson).
- **File → Export Circuit State (AI advisor)…**: design-studio.circuit-state/1
  with design goal, board+stackup, components incl. full datasheet electrical
  payloads, nets with members + routed length + connectivity islands,
  unconnected pins, DRC/ERC summary, per-class physics — and the embedded
  design-studio.advice/1 response contract. docs/circuit-advisor/
  ADVISOR_PROMPT.md completes the loop (advice "add" actions come back as
  component/2 files to import).
- Tests: JEDEC lettering, BGA expansion/depopulation/validation, v2 and v1
  imports, circuit-state content.

## 2026-06-12 — Fix: routes now actually connect to component pins

- **Router (C++):** start/goal snap is now nearest-grid (was floor-biased),
  and the exact requested endpoints are spliced back into the path — pads
  rarely sit on the 0.1 mm routing grid, and a trace stopping half a step
  short of the pin centre was drawn-but-not-connected copper.
- **Canvas:** Route and Auto-Route clicks snap to the pad under the cursor
  (world centre, rotation-aware) and **adopt the pad's net**. Previously
  routes carried no net, so the engine treated every pad — including the two
  being connected — as a foreign-net keepout and refused to touch them.
  Trace width and via geometry now come from the adopted net's class;
  joining two different nets is refused with a status message.
- Tests at all three layers: router endpoint exactness + one-island check
  (C++), SnapToPad geometry incl. rotation and topmost-wins (C#), and an
  end-to-end off-grid pin-to-pin auto-route asserting exact endpoints,
  NetIslands == 1 and an empty ratsnest (interop).

## 2026-06-12 (later still) — Physics & mathematics engine

- C++ `physics` module (+ `dc_phys_*` C API, 20 functions): Hammerstad–Jensen
  microstrip (thickness-corrected, textbook eqs. 2.116–2.121), Cohn exact
  stripline via AGM elliptic integrals, IPC-2141A differential pairs,
  εeff/propagation delay, skin depth, conductor+dielectric loss, DC/AC trace
  resistance, IPC-2221 current, Onderdonk fusing, via L/C/R/θ/current,
  crosstalk coefficient, plane capacitance. Inverse solvers (bisection on
  monotone curves) synthesize width from target Z0 and width@gap from target
  Zdiff. Sources cited per model in docs/physics-engine.md against
  `TextBook Datasets/`.
- Stackup electrical properties on the document and in Board Setup
  (εr, dielectric height, loss tangent, copper thickness), saved in .dsproj.
- Tools → Physics Calculator… (live, stackup-aware); Net Classes →
  "Width from impedance…" writes solver results into routing rules.
- 36 new canonical-value engine checks (Legendre K values, book eq. 2.116
  hand-check, 65.4 Ω Cohn chart point, 2.09 µm skin depth, solver round-trips,
  IPC ballparks) + interop tests through P/Invoke.

## 2026-06-12 (later) — Datasheet extraction pipeline + ERC

- `docs/datasheet-extractor/`: engineered LLM prompt for datasheet PDF →
  `design-studio.component/1` JSON (pins, electrical limits, required external
  components, application circuits, land pattern in mm, pin-1/polarity,
  outline hints), JSON Schema (draft 2020-12), illustrative AMS1117 example.
- `DatasheetImporter` (+ Library → Import JSON…): deterministic validation
  gate (schema/version, unique pins/pads, pin↔pad agreement, overlap check,
  annular ring, millimetre plausibility) before a footprint enters the
  library; import log reports required externals and suggested high-speed net
  classes from the datasheet.
- Electrical pin types (input/output/bidirectional/power_in/power_out/
  passive/nc) stored per pad in the library and on placed parts.
- `ElectricalRules` ERC runs with every DRC: output conflicts (rule 101),
  connected NC pins (102), unpowered power-input nets (103).
- Tests: importer accept/reject matrix, type propagation, suggested classes,
  ERC behaviours; the test fixture is schema-validated in CI tooling.

## 2026-06-12 — AI removal + industrial engine upgrade

Target boards: smartphones (HDI), AI accelerators (high-speed, length-matched),
datacenters (high layer count, large part counts).

### Removed
- Entire AI layer: `app/DesignStudio/Ai/` (LLM providers, design assistant,
  AI footprint generator), AI settings window, AI chat panel, AI library
  generator column, `colab/` notebooks. The deterministic parametric footprint
  generators (IPC-7351-style) remain — they were never AI.
- Plaintext API-key storage (gone with the AI settings).

### Engine (C++ core)
- **N-layer stackup** (engine: up to 32 copper layers) replacing the fixed
  top/bottom enum; SMD pads carry a side, through-hole pads span the stack.
- **Vias** with explicit layer spans: through, blind/buried, microvia.
- **Net classes** (clearance, width, via geometry, diff gap, max skew,
  microvia permission) and **stable net ids** — net deletion detaches copper
  instead of dangling; ids survive (fixes the index==id schema bug).
- **Spatial index** (`obstacle_index`): uniform-grid buckets over all copper,
  shared by router, DRC and pours. O(1) neighbourhood queries.
- **Router v2**: A* over (x, y, layer); through-via and microvia insertion
  with DRC-aware via fit checks; rotation-exact pad keepouts; admissible
  layer-change heuristic; collinear merge now exact integer math (no
  `double ==`); **explicit failure reasons** + expansion counts (the silent
  2M-expansion cap is gone).
- **DRC v2**: 13 rule families (all copper-pair clearances incl. vias,
  board edge, min width, min drill, annular ring, hole-to-hole wall,
  unconnected-net islands, per-class length matching), layer-aware, net-class
  aware, spatially pruned. Full violation details cross the API.
- **Copper pours**: scanline solid fills with clearance voids, same-net direct
  connect, DRC-verified.
- **C API v2**: every call returns a `DcStatus`; route/DRC/pour results are
  stored on the session handle and fetched by index — fixed-size buffer
  truncation (4096-point routes, 1024-violation DRC) is gone.
- `dc_net_length` / `dc_net_islands` queries (routed length, connectivity).

### App (C# WPF)
- **Single source of truth**: the document no longer mirrors every mutation
  into the native board; it rebuilds the native session on demand before any
  native operation. The dual-model drift bug class is eliminated.
- Multi-layer editing: active-layer selector (toolbar / PgUp / PgDn / 1–9),
  per-layer trace colours with inactive-layer dimming, via rendering with
  blind/buried span markers, via hit-testing/selection/deletion.
- Interactive routing: `V` drops a via mid-chain and switches layer.
- Auto-route reports the engine's failure reason in the status bar.
- Board Setup dialog (size, grid, copper layer count), Net Classes dialog
  (class rules + net assignment), Pour dialog (net, layer, width, clearance).
- Nets panel: routed length + island count per net.
- Project format v2 (stackup, net table with stable ids, classes, vias, pour
  flags) with automatic v1 migration.
- **FabExporter v2**: Gerbers for every copper layer (incl. via lands and
  pours), solder mask (TH + outer SMD pads, vias tented), paste (SMD only),
  Excellon split by plated span, side-aware pick-and-place.
- 3D view: bottom-layer copper and via barrels.

### Tests & CI
- C++ test suite rewritten: 18 test groups / 64 checks covering model identity,
  rotation, multi-layer routing + microvias, every DRC family, pour clearance
  (verified by running DRC over the generated pour), connectivity, skew and a
  scalability smoke (2.5k pads + 2k traces DRC ~130 ms; 280 mm route ~30 ms).
- New `tests/DesignStudio.Tests` (xunit): undo, stable net ids, ratsnest,
  parametric footprints, project I/O round-trip + v1 migration, Gerber/drill
  content checks, and end-to-end interop tests that drive the real native
  library through `BoardDocument` (auto-route with vias, DRC messages, pours,
  net length/islands).
- GitHub Actions: CMake + ctest on Linux and Windows, `dotnet test` on both,
  WPF app build on Windows.
