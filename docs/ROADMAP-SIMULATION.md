# Design Studio — Advanced Simulation Engine Roadmap

Target: the analysis capability required for PCIe Gen4/5 (16–32 GT/s), DDR5
(6400+ MT/s), multi-gigabit SerDes (25–112 GT/s) and NVLink/HBM-class
interconnect — the board classes in smartphones and datacenter hardware.

Honest framing first: at these speeds the *simulation* is the design. Routing
geometry is decided by what the solvers say, not the other way around. Every
engine below is what commercial flows (HyperLynx, Sigrity, ADS, Clarity,
HFSS) provide; our job is to build the tractable 80 % and integrate the rest.

Current state (built in phases 1–6): closed-form + 2D-FD impedance, lossy
ABCD channel cascade with L-C via models, ps delay engine, NEXT closed form,
DC IR-drop, lumped PDN Z(f), 2.5D thermal. Valid to roughly 3–5 GHz. The gap
between that and 32 GT/s (Nyquist 16 GHz, harmonics to 50 GHz) is the
subject of this document.

Effort scale: M = weeks, L = months, XL = quarters. Each engine lists its
algorithm, validation strategy, and where it plugs into the codebase.

---

## Tier 1 — Frequency-domain accuracy to 50 GHz

### E1. Full 2D electromagnetic solver upgrade (L)
What's missing: our FD solver is electrostatic (C only). High-speed needs
the full RLGC(f) matrices for coupled multi-conductor systems.
- **Build:** Method-of-Moments boundary-element solver over conductor
  surfaces: per-frequency skin-effect resistance (surface impedance
  boundary), internal inductance, multi-conductor coupling matrices (not
  just pairs), trapezoidal cross-sections and soldermask layers.
- **Output:** N×N L(f), C(f), R(f), G(f) → modal decomposition → exact odd/
  even impedances, attenuation, phase velocity per mode.
- **Validation:** against published Polar/Si9000 tables and IEEE benchmark
  cross-sections; ±2 % target.
- **Where:** `core/mom2d.cpp` (C++ — this is hot-path numerical code),
  replacing `FieldSolver2D` behind the same API.

### E2. Causal, frequency-dependent material models (M)
What's missing: we interpolate Dk/Df points; at 32 GT/s you need *causal*
dispersion (wrong phase = wrong eye).
- **Build:** wideband Debye / Djordjevic-Sarkar model fitted to the material
  library points; Hammerstad/Cannonball-Huray surface-roughness loss model
  (roughness parameters added to `MaterialDef` and the laminate library).
- **Validation:** causality check via Kramers-Kronig consistency; loss curves
  vs vendor SET2DIL data.
- **Where:** extends `Materials.cs`; consumed by E1 and E3.

### E3. 3D via & breakout solver (XL — the hardest single engine)
What's missing: above ~8 GHz the L-C π model fails; via fields are 3D.
- **Build (pragmatic ladder):**
  1. Physics-based analytical via model (Schlepnev-style): plane-pair cavity
     model for the return path + coaxial barrel model → S-parameters valid
     to ~20 GHz. Months, not quarters. Covers DDR5 and Gen4.
  2. Optional FEM kernel (tet mesh over a via neighbourhood, ~50k unknowns,
     absorbing boundaries) for Gen5/SerDes corner cases — or punt to an
     external HFSS/openEMS flow via geometry export (see E8).
- **Validation:** published via S-parameter measurements (IEEE EPEPS
  benchmarks); compare stub resonance frequency and insertion loss.
- **Where:** `core/viacavity.cpp`; replaces the L-C model inside
  `ChannelExtractor` transparently.

---

## Tier 2 — Time-domain link simulation

### E4. IBIS / IBIS-AMI link simulator (XL)
What's missing: eyes at the receiver after equalization — the actual
pass/fail criterion for every interface named above.
- **Build:**
  1. IBIS 7.x parser (buffer I-V/V-T tables, package RLC) — weeks.
  2. Channel impulse response from our S-parameters (vector fitting →
     rational model → causal time-domain convolution).
  3. Statistical eye engine (LTI assumption): superpose all bit transitions
     probabilistically → BER contours without simulating billions of bits.
     This is how Gen4 compliance is actually checked. Months.
  4. AMI executable model support (vendor .dll equalizers: CTLE/FFE/DFE) —
     load and call per the AMI spec; needed for Gen5/SerDes where the SerDes
     vendor's model *is* the sign-off.
- **Validation:** IBIS golden parser test suite; eye height/width vs
  reference simulators on the open PCIe reference channels.
- **Where:** new `Model/LinkSim/` (C# orchestration) + `core/convolve.cpp`;
  UI: "Simulate link…" on a net or diff pair → eye diagram window with BER
  contours and mask overlay.

### E5. Compliance mask & jitter engine (M, after E4)
Per-standard automation: PCIe Gen4/5 Tx/Rx masks, DDR5 derating tables and
mask tests, JESD204/Ethernet masks. Jitter decomposition (Rj/Dj/ISI) from the
statistical eye. Output: pass/fail report per standard, attached to DRC.

---

## Tier 3 — Power and timing co-simulation

### E6. Plane-cavity PDN solver (L)
What's missing: our lumped PDN model ignores plane resonances and decap
*placement* (spatial Z matters above ~100 MHz).
- **Build:** cavity-mode plane-pair solver (analytical modal sum on
  rectangular segments, or 2D FDM over plane polygons) → Z(f) between any
  two (x, y) ports on the planes; decaps become port terminations at their
  actual coordinates.
- **Deliverables:** Z(f) heat-map at the BGA, per-decap effectiveness
  ranking, automatic placement suggestions upgraded from guesses to solved.
- **Where:** extends `PdnAnalyzer` with the mesh from `IrDrop`.

### E7. Simultaneous switching / DDR timing engine (L)
DDR5-specific: per-bit timing budgets combining channel delay (have),
crosstalk-induced jitter (E1 coupling + E4 eyes), SSO noise (E6 PDN at the
buffer), and Vref margins → per-byte-lane margin report in ps. This is the
engine that says "DQ3 fails setup by 4 ps at 6400 MT/s".

---

## Tier 4 — Ecosystem integration (multiplies everything above)

### E8. Geometry & model exchange (M — do this FIRST)
- Export: net/via/stackup geometry to openEMS + gprMax scripts (free FEM/
  FDTD), ODB++ for Sigrity/HyperLynx users, and our existing Touchstone.
- Import: Touchstone S-parameters as black-box channel blocks (let users
  drop a vendor connector model into the cascade), IBIS files (feeds E4).
- Why first: every engine becomes optional once geometry round-trips —
  users with HFSS licences get sign-off accuracy on day one, and our own
  engines validate against the same exports.

### E9. Solver infrastructure (M, parallel)
Shared numerical substrate the above need: sparse complex solver (or wrap
Intel MKL / Eigen), vector fitting, FFT, multithreaded sweep scheduler with
progress/cancel, result caching keyed on `BoardDocument.Version`, and a
results browser UI (plots: Z(f), S(f), eyes, heat-maps — one reusable
charting view).

---

## Build plan

| Phase | Engines | Exit criterion |
|-------|---------|----------------|
| S1 | E8 exchange + E9 infrastructure | Geometry round-trips to openEMS; vendor .s4p imports into the cascade |
| S2 | E2 causal materials + E1 MoM solver | RLGC within ±2 % of Si9000 tables to 50 GHz |
| S3 | E3 step 1 (cavity via model) | Via stub resonance within 10 % of measurement to 20 GHz |
| S4 | E4 steps 1–3 (IBIS + statistical eye) | Eye height within 10 % of reference sim on PCIe Gen4 reference channel |
| S5 | E6 plane cavity + E5 masks | DDR5 board passes/fails with spatial decap analysis + mask report |
| S6 | E4 step 4 (AMI) + E7 DDR timing | Vendor SerDes model executes; per-lane DDR5 margin table |

Sequencing logic: S1 makes external sign-off possible immediately (the
escape hatch while we build); S2–S3 make our frequency-domain data
trustworthy; S4 turns that data into the pass/fail number engineers actually
need; S5–S6 specialize for the two big interface families.

Rough scale, one strong engineer full-time: S1–S3 ≈ two quarters, S4 ≈ one
to two more, S5–S6 ≈ one more. A commercial-parity suite is a team-years
project — this plan instead targets *credible Gen4/DDR5 design* with our
own engines and *Gen5/SerDes via integration* (E8 + vendor tools), which is
exactly how most mid-tier hardware companies operate today.

What stays out of scope even here: full-wave 3D extraction of entire boards
(Clarity-class, HPC clusters), die/package co-simulation, and electro-
thermal transient co-sim. Datacenter NVLink/HBM substrate design lives in
that tier — it is package engineering more than PCB engineering.
