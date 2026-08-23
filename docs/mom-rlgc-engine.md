# Phase S2 — Causal materials & the MoM RLGC field solver

This note documents the two engines added in Phase S2 (`E2` causal materials and
`E1` the 2D Method-of-Moments RLGC solver), how they are validated, and the one
implementation subtlety that is easy to get wrong.

The goal of S2 is the roadmap exit criterion: **RLGC within ±2 % of reference
tables to 50 GHz.** Below 5 GHz the existing closed-form / FD path is fine; the
problem at 16–50 GHz is that (a) the dielectric is dispersive and must be
*causal*, and (b) loss (conductor skin effect + roughness + dielectric) and
inter-conductor coupling all have to come out of the same field solve.

## E2 — Causal materials (`Materials.cs`)

### Wideband-Debye (Djordjevic–Sarkar) dispersion

Datasheets give a handful of `(f, Dk, Df)` points. The legacy log-linear
interpolation between them is **not causal**: it does not satisfy the
Kramers–Kronig relations, so the phase velocity is wrong at high frequency and
the simulated eye closes for the wrong reason. S2 fits a single wideband-Debye
term instead:

```
ε*(ω) = ε∞ + Δε / (ln10·(m2−m1)) · ln( (10^m2 + jf) / (10^m1 + jf) )
```

with the decade bounds fixed at 1 kHz … 1 THz and `ε∞`, `Δε` fitted so that the
point nearest 10 GHz (the band that matters for Gen4/DDR5) reproduces `Dk` and
`Df` exactly. This term has an almost-flat loss tangent across the band and the
correct causal real-part rise toward low frequency — exactly what laminate
datasheets show. `MaterialDef.DkCausalAt / DfCausalAt / EpsComplexAt` expose it.

`Causality.KramersKronigResidual` reconstructs `ε′` from `ε″` via the subtractive
KK integral and compares; the residual is a few percent (quadrature-limited),
confirming the model is causal.

Reference: A. R. Djordjevic et al., *"Wideband frequency-domain characterization
of FR-4 and time-domain causality,"* IEEE Trans. EMC, 2001.

### Surface roughness

At 16 GHz the copper skin depth (~0.5 µm) is comparable to the foil tooth
height, so current crowds over a rough surface and conductor loss is multiplied
by `Ksr(f) ≥ 1`. Two models, selected per material:

- **Hammerstad–Jensen** — `Ksr = 1 + (2/π)·atan(1.4·(Δ/δ)²)`. Two-parameter,
  saturates at 2×, the classic default; good to ~20 GHz.
- **Cannonball–Huray** — `Ksr = 1 + Cr / (1 + δ/r + δ²/2r²)`, a physically
  grounded "snowball" model that does not artificially cap at 2× and tracks
  measured loss past 40 GHz. Parameterised here from a single RMS roughness via
  the Simonovich cannonball stack (`r ≈ Rq`, `Cr = 14π/36`).

Roughness lives on `MaterialDef` (`roughness_rq_um`, `roughness_model`) and the
built-in laminates are populated (e.g. standard FR-4 = 2 µm, Tachyon 100G =
0.3 µm Huray).

## E1 — MoM RLGC solver (`MoM2D.cs`)

A boundary-element (Method-of-Moments) solve in the transverse plane.

- **Unknowns:** equivalent free-space line charges `σ` on conductor surfaces and
  on dielectric–dielectric interfaces.
  - Conductor segments enforce the potential: `Σ A_ij σ_j = V_i`.
  - Interface segments enforce normal-`D` continuity:
    `(εa+εb)/(2ε0(εa−εb))·σ_i + Ē_n(others) = 0`.
- **Ground planes** are handled exactly by image charges (one mirror for
  microstrip; the `2nb ± y0` series for stripline), so only the trace and the
  substrate-top interface are discretised.
- **Capacitance `C`**: free charge `= εr_face · σ` integrated over each
  conductor (validated exactly against homogeneous fill and partial-fill series
  capacitors). **Inductance `L = μ0ε0·C_vacuum⁻¹`.**
- **`R(f)`** via Wheeler's incremental-inductance rule: recede every conducting
  wall by `δ(f)/2` and `R = ω·ΔL`. This gives the correct `√f` skin-effect
  scaling and, because the trace-to-plane gap grows by a full `δ`, folds in the
  ground-plane loss. Scaled by the material roughness factor.
- **`G(f) = ω·C·q·tanδ(f)`** with the dielectric filling factor
  `q = (εeff−1)/(εr−1)` and the causal loss tangent from E2.
- **Modal decomposition** of the 2×2 system → even/odd impedances and
  differential/common-mode `Z` and `εeff` for coupled pairs.

`Microstrip` / `Stripline` return a `FieldSolver2D`-compatible `Solution`;
`CoupledMicrostrip` / `CoupledStripline` return modal results; `ExtractRlgc`
returns the full per-unit-length `L,C,R,G` (with `Gamma()` and `Zc()`) at a
frequency, which `ChannelExtractor` cascades.

### Validation

| Case | MoM | Reference | Error |
|------|-----|-----------|-------|
| Microstrip Z0 (thin) | 53.0 Ω | HJ 52.99 Ω | +0.2 % |
| Microstrip εeff (thin) | 3.14 | HJ 3.18 | −1.2 % |
| Wide microstrip Z0 (w/h=10) | 16.27 Ω | HJ 16.47 Ω | −1.2 % |
| Stripline εeff | 4.200 | εr 4.2 | exact |
| Partial-fill series cap | 1.595 | analytic 1.600 | −0.3 % |

Finite trace thickness correctly lowers Z0 below the zero-thickness HJ value
(HJ omits it); a thickness-corrected HJ matches the MoM to ~1 %.

### Implementation subtlety (read this before editing)

**Every dielectric interface segment must be oriented consistently** — here all
segments run in increasing-x so their normal points `+y` toward the "above"
medium, and `(εa, εb)` are assigned accordingly. If one side of the interface is
built in the opposite direction its normal flips, the bound-charge sign flips
with it, and the two halves **cancel in the charge sum** — the dielectric
loading silently vanishes and `εeff` collapses toward `(εr+1)/2`. This produced a
robust, mesh-converged *wrong* answer (≈14 % low) during development; the fix was
purely orientation, not resolution.

A same-medium "interface" (`εa == εb`, used for the vacuum reference solve)
carries no bound charge and is skipped entirely, so the vacuum capacitance is a
clean conductor-only problem.

### Mesh density

`NSide = 60` segments per conductor and `NIface = 150` per interface stretch
(graded toward the conductor edges) put the answer within ~1 % of the
fully-converged result (`N ≈ 420`); finer meshes move it < 0.3 %. The cross-
section solve is frequency-independent except for `εr(f)` and `δ(f)`, so the
channel extractor solves a 6-point frequency grid per (layer, width) and
interpolates.

### Future work

This is pure-managed C# so it validates in the Linux CI test project. It is the
natural candidate for the C++ `core/mom2d.cpp` migration noted in the roadmap
once a board-wide sweep makes the `O(N³)` per-frequency solve the bottleneck.
