# Design Studio — High-End Board Roadmap (Tier 5+)

Where the S1–S6 stack stops and what it takes to design the two board classes
above mainstream servers:

- **Class A — dense multi-socket DDR5 motherboards.** 8–12 memory channels per
  socket × 2 sockets, PCIe Gen5/CXL, power delivery for 300–400 W CPUs. These
  land at **18–24+ layers**: stripline pairs with dedicated reference planes top
  and bottom, many split power planes, ground shields between high-speed groups.
- **Class B — AI/GPU accelerator baseboards (HGX/OAM-UBB), switch line cards,
  backplanes/midplanes.** **24–40 layers, 50+ at the extreme**, lots of 112G
  PAM4 SerDes, large power planes, press-fit connectors.

Honest framing: this is the jump from "credible Gen4/DDR5 design with our own
engines" to "tool you can build a real datacenter board on." It is a multi-quarter
program per tier, and a couple of pieces (full split-plane PDN, 112G PAM4 with
statistical crosstalk) are where most of the work is. Effort scale as before:
M = weeks, L = months, XL = quarters. Each engine lists what's missing today, the
build, and where it plugs into the current code.

---

## Tier 5 — Stackup & interconnect at scale

### E10. Layer-count scale + intelligent multi-plane stackup (M)
What's missing: `BoardDocument.SetCopperLayers` clamps to 16; `EnsureStackup`
only marks L1 and L(n−1) as planes. A 24-layer board needs interleaved
signal/plane pairs and a *reference assignment* per signal layer.
- **Build:** raise the clamp to 64; rewrite `EnsureStackup` to lay down a
  proper high-layer stack (signal/plane/signal sandwiches, ≥1 ground reference
  adjacent to every signal layer); per-layer copper weight (2 oz inner for
  power); per-signal-layer "reference plane above/below" resolution feeding
  `ImpedanceEngine.Reference`.
- **Where:** `BoardDocument`, `StackupLayer` (add `CopperWeightOz`,
  `ReferenceLayerAbove/Below`), `StackupPlanner`, the stackup UI and `.dsproj`.
- **Exit:** a 24-layer stackup round-trips, and every controlled net resolves a
  valid stripline reference.

### E11. HDI — blind/buried/stacked microvias (L)
What's missing: `ViaItem` is a single barrel spanning `FromLayer..ToLayer` with
optional backdrill; the `ViaModel` is one coaxial barrel + stub. Dense boards
use blind, buried and stacked/staggered microvias.
- **Build:** via *types* (through / blind / buried / microvia) and via *stacks*
  (microvia-on-microvia, skip vias); the cavity/stub model extended for short
  laser microvias (capacitance-dominated, no stub resonance) and via stacks
  cascaded; DRC for aspect ratio, capture-pad, staggered-vs-stacked rules.
- **Where:** `ViaItem` (+`ViaType`, stack grouping), `ViaModel`,
  `HighSpeedDrc`, the fab exporter (drill spans per via class).
- **Exit:** a stacked-microvia HDI transition extracts S-parameters and the
  fab output lists the correct laser/mechanical drill spans.

### E12. Reference-plane integrity & return path at scale (M–L)
What's missing: `HighSpeedDrc` rule 14 only checks "is there *any* plane"; rule
17 checks a GND via within 1 mm. On a 24-layer board you must verify each
signal's *actual* reference is continuous and that plane changes have a real
return path through the right plane.
- **Build:** per-signal reference-plane tracking; split/void crossing detection
  (signal crossing a gap in its reference plane); stitching-via adequacy at
  every layer transition; reference-change penalty into `ChannelExtractor`.
- **Where:** `HighSpeedDrc`, `Crosstalk` (shared spatial index), `PlaneShape`.
- **Exit:** a trace crossing a plane split is flagged; a clean reference passes.

---

## Tier 6 — Power delivery at scale

### E13. Multi-rail PDN + irregular/split-plane cavity (L)
What's missing: `PlaneCavity` is one *rectangular* plane pair per net;
`PdnAnalyzer` analyses one rail at a time. Real boards have many rails on split,
L-shaped, perforated planes.
- **Build:** the **2-D FDM polygon plane solver** the original roadmap flagged as
  the alternative to the rectangular modal sum — meshes arbitrary plane polygons
  (from `PlaneShape.Fill`) for Z(f) between any ports, handles voids/splits;
  multi-rail analysis (VCCIN/VCCFA/VDDQ/VPP…) with shared ground; cavity coupling
  between adjacent rails.
- **Where:** new `PlanePdnFdm.cs` beside `PlaneCavity`; `PdnAnalyzer.BuildCavity`
  picks modal (rectangular) vs FDM (irregular) automatically.
- **Exit:** a split VDDQ island gives a Z(f) that the rectangular model can't,
  validated against the modal solver on a rectangle.

### E14. Plane-aware IR drop + electro-thermal for 300–400 W (L)
What's missing: high-current CPU rails need DC IR drop on real 2 oz copper with
many vias sharing current, and the resulting copper heating.
- **Build:** plane-resolved IR-drop mesh (reuse the `IrDrop`/`PowerIntegrity`
  mesh) with per-layer copper weight and via current sharing; coupled DC
  electro-thermal (copper resistance rises with temperature) for the 300–400 W
  case; current-density / via-count adequacy DRC.
- **Where:** `PowerIntegrity`, `Thermal`, `Ampacity`.
- **Exit:** a 350 W rail reports worst-case droop and the hottest plane region.

---

## Tier 7 — 112G PAM4 / differential / multi-board

### E15. Differential link simulator + PAM4 (L)
What's missing: `LinkSim` is single-ended NRZ. 112G is 56 GBaud **PAM4** (4
levels, 3 eyes) over differential pairs.
- **Build:** true differential channel (use `MoM2D`'s coupled odd/even modes and
  mixed-mode S-parameters) end-to-end; PAM4 in `StatisticalEye` (4 levels → 3
  sub-eyes, level mismatch/RLM); PAM4 masks and jitter (per-eye) in `ComplianceMask`
  / `JitterDecomposition`.
- **Where:** `StatisticalEye`, `LinkSimulator`, `ComplianceMask`,
  `ChannelExtractor` (mixed-mode), `MoM2D`.
- **Exit:** a 112G PAM4 differential channel reports three sub-eye heights and a
  per-standard pass/fail.

### E16. Statistical crosstalk-aware eye (M–L)
What's missing: `Crosstalk` gives a closed-form NEXT number used as a jitter
penalty; at 112G the aggressors must fold into the victim's eye *distribution*.
- **Build:** extract victim+aggressor coupled channels (`MoM2D` N×N), build each
  aggressor's pulse-response contribution, convolve their PDFs into the victim's
  statistical eye (the framework already convolves ISI PDFs).
- **Where:** `StatisticalEye`, `ChannelExtractor`, `MoM2D`.
- **Exit:** adding a switching aggressor closes the victim eye by the measured
  coupled amount, not a closed-form estimate.

### E17. Vendor IBIS-AMI executable loading (M)
What's missing: `IAmiModel`/`BuiltInAmi` exists, but real SerDes sign-off uses
the vendor's compiled `.dll`/`.so` — and for Gen5/112G the vendor model *is* the
sign-off.
- **Build:** the P/Invoke wrapper implementing `AMI_Init`/`AMI_GetWave`/`AMI_Close`
  per the IBIS-AMI spec; `.ami` parameter-tree parser; reserved/model-specific
  parameter passing; statistical *and* bit-by-bit (GetWave) flows.
- **Where:** new `AmiNative.cs` implementing `IAmiModel`; `LinkSimulator`.
- **Exit:** a reference vendor AMI model loads and its equalised eye matches the
  vendor's golden waveform.

### E18. Macromodelling + multi-board channel assembly (L)
What's missing: `ChannelExtractor` uses a direct IDFT; backplane channels are
daughtercard → connector → backplane → connector → daughtercard, each a separate
S-parameter block that must cascade causally.
- **Build:** **vector fitting** → rational macromodel → guaranteed-causal/passive
  time-domain convolution (the roadmap's E4-step-2 done properly); a channel
  *assembly* that cascades board segments + vendor connector `.s4p` (press-fit)
  + cable models via the existing Touchstone import.
- **Where:** new `VectorFit.cs`; `ChannelExtractor`, `Touchstone`/`ChannelBlock`.
- **Exit:** a 3-board backplane channel assembled from vendor connector models
  produces a causal pulse response and a 112G eye.

---

## Tier 8 — DDR5 subsystem completeness

### E19. Full DDR5 channel topology (L)
What's missing: `DdrTimingEngine` does one byte lane. A socket has 8–12 channels,
each with a data group (DQ×8 + DQS + DM) *and* a fly-by command/address bus,
often 2 DIMMs per channel (2DPC) with stubs and ODT.
- **Build:** full DIMM topology (per-byte data groups + CA fly-by with the
  characteristic write-leveling skew), 2DPC stub/ODT modelling, per-channel and
  per-socket margin roll-up; CA-bus and DQ-bus timing both.
- **Where:** `DdrTimingEngine`, a new `DdrChannelTopology` builder.
- **Exit:** a 2-socket, 8-channel, 2DPC DDR5-6400 board reports a per-channel
  margin table and an overall pass/fail.

---

## Tier 9 — Scale & infrastructure (finish E9)

### E20. Solver scale-out + C++ hot path (L–XL)
What's missing: the managed MoM/PDN solvers are fine per-cross-section but a
24-layer, thousands-of-net board needs throughput, and vector fitting / large
dense solves want native speed.
- **Build:** sparse/dense complex solver wrap (Eigen or Intel MKL); the
  multithreaded sweep scheduler with progress/cancel; board-wide result cache
  keyed on `BoardDocument.Version`; migrate the `MoM2D` and `PlanePdnFdm` hot
  loops to the C++ `core/` (`core/mom2d.cpp`, `core/planepdn.cpp`) behind the
  existing P/Invoke layer.
- **Where:** `core/`, `Interop/NativeCore.cs`, all SI engines (caching).
- **Exit:** a full 24-layer board's controlled nets extract in minutes, not the
  weekend, with cancel/progress.
- **Status (S13, 2026-06-14):** delivered — `SweepScheduler` (parallel + progress
  + cancel), `ExtractionCache` (version-keyed reuse), `ChannelExtractor.ExtractManyParallel`,
  and the dense-solve hot path migrated to `core/src/linalg.cpp` (`dc_dense_lu_solve`)
  behind `DenseSolver` with a managed fallback. See `docs/scale-out.md`.

---

---

## Tier 10 — AI design advisor (keep the loop ahead of the engines)

### E21. SI/PI-aware design advisor (M–L) ✅ Done (2026-06-14)
What's missing: the AI loop (`CircuitStateExport` → `design-studio.circuit-state`
→ LLM → `design-studio.advice`) is still scoped to *component/netlist*
completeness. Its board model is a single global dielectric + a layer count, and
it sees **none** of the S2–S6 analysis — so it cannot reason about eye margins,
via stubs, decap placement, DDR timing or stackup, exactly the decisions that
matter on a 24–50-layer board.
- **Build (three steps):**
  1. *Feed it the analysis* — widen `circuit-state` to carry the real per-layer
     stackup and summarised SI/PI results (channel eye height/width/BER + mask,
     spatial PDN worst-Z + decap ranking + placement hint, DDR per-lane margins)
     with drill-down, not raw sweeps. **(started — see schema extension below)**
  2. *Teach it the design space* — expand the `advice` contract so the model
     reasons about SI/PI sign-off and emits SI/PI actions (back-drill a via, move
     a decap to an antinode, add an EQ tap, change the stackup / reference
     assignment), not just add/remove/replace parts.
  3. *Solve scale* — hierarchical, region/net-scoped context and retrieval so a
     thousand-net, 50-layer board is tractable for a model.
- **Where:** `CircuitStateExport`, `docs/circuit-advisor/ADVISOR_PROMPT.md`, and a
  new context/summarisation layer.
- **Exit:** the advisor, given a board that fails a DDR5 lane or a PDN target,
  recommends the right SI/PI fix (back-drill / decap move / EQ / stackup) — not
  just a part change.
- **Status (2026-06-14):** delivered — `SiPiAdvisor` (deterministic SI/PI
  recommender, the exit criterion), `AdviceContract` (typed parse/validate +
  serialise of `design-studio.advice/1` with the SI/PI ops), `ContextScope`
  (failing-net-first scoping), `CircuitStateExport.BuildState`. See `docs/si-pi-advisor.md`.

Sequencing: E21 consumes the other engines' output, so it tracks behind them —
step 1 can land now (the engines exist), steps 2–3 alongside S9/S12.

---

## Build plan

| Phase | Engines | Unlocks | Exit criterion |
|-------|---------|---------|----------------|
| S7  | E10 stackup scale + E12 ref-plane integrity | Class A routing | 24-layer stackup; every controlled net has a continuous reference |
| S8  | E13 split-plane PDN (FDM) + E14 IR/thermal | Class A power | Split VDDQ Z(f) + 350 W rail droop/thermal |
| S9  | E19 full DDR5 topology | **Class A sign-off** | 2-socket / 8-ch / 2DPC DDR5-6400 per-channel margins |
| S10 | E11 HDI microvias | Class B density | Stacked-microvia transition extracts + correct drill spans |
| S11 | E18 macromodel + multi-board + E17 vendor AMI | Class B channels | Backplane channel from vendor connectors, vendor AMI eye |
| S12 | E15 PAM4/differential + E16 statistical crosstalk | **Class B sign-off** | 112G PAM4 diff channel, crosstalk-aware 3-eye pass/fail |
| S13 ✅ | E20 scale-out + C++ hot path | both, at scale | **Done** — parallel cached board extract, native dense-solve hot path, progress/cancel |
| E21 ✅ | SI/PI advisor (recommender + advice contract + context scope) | AI loop | **Done** — recommends back-drill / decap / EQ / length-tune / reference fixes, not just parts |

Sequencing logic: S7–S9 make a **dense multi-socket DDR5 motherboard (Class A)**
designable end-to-end; S10–S12 add the **112G/HDI/connector** capability for
**AI baseboards, switch line cards and backplanes (Class B)**; S13 makes both
practical on real board sizes.

Rough scale, one strong engineer full-time: each of S7–S12 is one to two
quarters, S13 one to two more — i.e. Class A in ~3 quarters, Class B in ~3 more.
A team compresses that proportionally.

What stays out of scope even here (same line as the original roadmap): full-wave
3-D extraction of entire boards (Clarity/HFSS-class on HPC), die/package
co-design, and transient electro-thermal co-simulation. Those remain an
export-to-external-tool story via E8 (ODB++ / openEMS / Touchstone).
