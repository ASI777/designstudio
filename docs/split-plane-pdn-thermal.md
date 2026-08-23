# Phase S8 — Split-plane FDM PDN (E13) + electro-thermal IR drop (E14)

The second high-end-roadmap phase: power delivery on the irregular planes real
dense boards use, and the heating of the 300–400 W rails that feed modern CPUs.
Exit criterion: a split VDDQ Z(f) the rectangular model can't produce, plus a
high-current rail's droop and thermal.

## E13 — Split-plane FDM PDN (`PlanePdnFdm.cs`)

`PlaneCavity` (S5/E6) is a closed-form modal sum for a *rectangular* plane pair.
Real power planes are L-shaped, split into islands, and perforated by antipads —
none of which a rectangle represents. `PlanePdnFdm` meshes the **actual plane
copper** (`PlaneShape.Fill`, cell-centred so each cell is exactly h²) into a 2-D
RLC grid:

- between adjacent in-copper cells, a series plane-pair branch
  `Zs = 2·Rs + jω·μ0·d` (skin loss + the loop inductance per square);
- at each cell, a shunt to the other plane `Ysh = jω·(ε0·εr·h²/d)·(1 − j·tanδ)`;
- decaps and the VRM as admittances added at their cell.

`Z(f)` at the load is the solution of `Y·V = I` for a unit current injected at
the load node. Because disconnected copper simply isn't reachable through the
mesh, a **split** plane is handled for free — the port only sees its own island,
which is exactly the case the rectangular model gets wrong.

`PdnAnalyzer.AnalyzeSpatialFdm` builds it from a net's largest plane (load =
the footprint with the most pads on the net, decaps at their coordinates) and
returns the same `PlaneCavity.Result` as the modal path, so the two are
interchangeable; the modal solver stays for clean rectangles.

**Validated:** matches `PlaneCavity` to <0.1 % on a rectangle (cap region and
first resonance), a half-plane split raises Z ~2× (lost capacitance), a decap
crushes Z at its SRF, and L-shaped planes solve. The dense per-frequency solve
is managed here; the sparse/C++ scale-out is E20.

## E14 — Electro-thermal IR drop (`IrDrop.AnalyzeElectroThermal`)

The DC IR solve is isothermal, but a 300–400 W rail pushing 100–300 A through
the copper heats it, copper resistivity rises ~0.39 %/°C, and the droop gets
worse. This couples the two:

```
IR solve with ρ(T)  →  per-cell Joule heat  →  2-D thermal solve
(copper conduction + convection both faces)  →  ρ(T)=ρ0(1+α(T−25))  →  repeat
```

It reports the worst droop, the **hottest plane region**, and the peak current
density. The ρ rise is soft-capped (≤5×) so a pathologically concentrated
current can't run the coupling away. New DRC **rule 24** flags droop over budget
or a plane running hot.

**Validated:** the coupled droop exceeds the isothermal droop by exactly the
`1+α·ΔT` resistivity factor (9.9 → 11.8 mV at 77 °C on a 100 A rail), with stable
magnitudes once the current is distributed over the load's pads (as a real BGA
does).

## Scope and next

S8 + S7 (stackup/reference) + **S9 (full DDR5 topology)** complete Class A —
dense multi-socket DDR5 motherboard sign-off. Still simplified: a single rail at
a time (multi-rail coupling between adjacent power islands and the full thermal
model with heatsinks/airflow are refinements), and the thermal solve is
convection-only (no forced air / heatsink θ yet). The C++/sparse scale-out for
thousand-node meshes is E20/S13.
