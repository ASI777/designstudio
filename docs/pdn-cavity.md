# Phase S5 — Spatial PDN cavity solver (E6) + compliance masks & jitter (E5)

Two engines that finish the "does this board pass" story: a spatial power-delivery
solver that scores decap *placement*, and per-standard eye masks + jitter that turn
the S4 statistical eye into a compliance pass/fail. Exit criterion: a DDR5 board
passes/fails with spatial decap analysis and a mask report.

## E6 — Plane-cavity PDN (`PlaneCavity.cs`)

The lumped `PdnAnalyzer` treats the whole plane as one capacitor and assumes every
decap sits on the load. Above ~100 MHz that is wrong: the power/ground plane pair
is a 2-D resonant cavity, and a decap only helps where it can reach.

**Model.** A rectangular plane pair `a×b`, separation `d`, is solved with the
cavity-resonator modal expansion:

```
Z_pq(ω) = jωμd/(ab) · Σ_m Σ_n  Cm²Cn²·sinc²·cos(mπxp/a)cos(nπyp/b)·
                                     cos(mπxq/a)cos(nπyq/b) / (k² − k_mn²)
```

with `k² = ω²μεrε0(1 − j·tanδ_eff)`, `tanδ_eff` adding plane conductor loss
(`skin/d`) to the dielectric `tanδ`, and a port-size `sinc` smoothing.

- The **(0,0) mode** is exactly the plane capacitance `C = εrε0·ab/d` — verified
  to ~1 %.
- `k = k_mn` are the **plane resonances** `f_mn = (c/2√εr)·√((m/a)²+(n/b)²)` —
  the self-impedance peaks land on them to 0 %.

**Decaps.** Each decap is a port terminated by its series R-L-C
`Z_cap = ESR + jωESL + 1/(jωC)`. The input impedance at the load (port 0) is the
multiport Schur complement, then the VRM branch in parallel:

```
Z_in = Z00 − Z0d·(Zdd + diag(Z_cap))⁻¹·Zd0 ,   Z_total = Z_in ∥ Z_vrm
```

A decap near its SRF crushes the local Z (≈18 mΩ vs ≈1.4 Ω in the validation
case); the classic **VRM–decap anti-resonance** falls out of the same matrix.

**Deliverables.**
- `Z(f)` at the load with the real decap placement.
- **Per-decap effectiveness ranking** — the change in the band-worst Z if that
  decap is removed. Positive = it helps; negative = it is *making* an
  anti-resonance worse (a genuinely useful diagnostic).
- **Placement hint** — at the worst frequency, the plane is scanned for the
  highest transfer impedance to the load; that antinode is where the next decap
  bites hardest.

`PdnAnalyzer.BuildCavity` resolves the rectangle (plane bounding box), the load
(footprint with the most pads on the net) and decap coordinates from the board;
`AnalyzeSpatial` returns the report and `CheckSpatial` raises DRC rule 22.

## E5 — Compliance masks & jitter (`ComplianceMask.cs`, `JitterDecomposition.cs`)

**Masks.** A mask is the inner keep-out the eye must clear at the target BER — a
minimum height (V) and width (UI). Presets cover PCIe Gen3/4/5, DDR5-6400 and a
generic 10 Gb/s. `ComplianceMasks.Check(mask, eye)` returns pass/fail with margins.
Because the link simulator is LTI/pre-equalisation (Tx FFE / Rx CTLE-DFE are E4
step 4 / S6), a lossy channel correctly *fails* until equalisation is applied.

**Jitter.** `JitterDecomposition.Decompose` splits the horizontal closure into
data-dependent jitter (DDJ, from the ISI-limited eye width), injected Dj, and Rj,
combined as `TJ(BER) = DDJ + Dj + 2·Q(BER)·Rj` leaving eye width `1 − TJ`.

`LinkSimulator.CheckCompliance(net, standard)` runs the channel at the standard's
rate/BER and returns the mask result plus the jitter report — the per-standard
sign-off, attachable to DRC.

## Scope and future work

- The cavity is a single rectangle per net; irregular planes are approximated by
  their bounding box. Multi-cavity stitching, split planes and via-field coupling
  to the cavity are later refinements.
- Masks are simplified receiver keep-outs; full per-standard mask polygons and
  derating tables (DDR5 Vref/temperature) are an extension.
- The big eye-reopening piece — Tx/Rx equalisation and vendor AMI — is Phase S6
  (E4 step 4 + E7 DDR timing), which consumes exactly these eye/jitter objects.
