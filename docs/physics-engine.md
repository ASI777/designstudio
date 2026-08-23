# Physics & Mathematics Engine

The engine lives in the C++ core (`core/include/designcore/physics.h`,
`core/src/physics.cpp`), is exported through the C API (`dc_phys_*`), and is
fully covered by canonical-value tests in `core/tests/test_core.cpp`. The UI
front-ends are **Tools → Physics Calculator…** (live, stackup-aware) and
**Board → Net Classes → Width from impedance…** (synthesis straight into
routing rules). Stackup parameters (εr, dielectric height, loss tangent,
copper thickness) are set in **Board → Board Setup** and saved in the project.

Every model is grounded in the references shipped in `TextBook Datasets/`:

| Model | Algorithm | Source in `TextBook Datasets/` |
|---|---|---|
| Microstrip Z0, εeff | Hammerstad–Jensen closed forms incl. strip-thickness correction (stated ≤ 0.2% for t=0); Hammerstad synthesis (eq. 2.118) backs the width solver | `AD EMT/Microstrip Lines and Slotlines, 4th ed.` §2.4, eqs. 2.116–2.121 (pdf pp. 107–108) |
| Microstrip losses | conductor αc = 8.686·Rs/(Z0·w) + dielectric αd = 27.3·(εr/(εr−1))·((εeff−1)/√εeff)·tanδ/λ0 | same book, §2.4.7 (pdf p. 109); `AD EMT/Rizzi — Microwave Engineering` |
| Stripline Z0 | Cohn's exact conformal mapping: Z0 = (η0/4√εr)·K(k)/K(k′), k = sech(πw/2b); finite-t via IPC-2141 effective width | `IL EMT/Foundations for Microwave Engineering` (Collin), ch. 3 |
| K(k) elliptic integral | arithmetic–geometric mean, quadratic convergence; the complementary form K(k′)=π/2·AGM(1,k)⁻¹ keeps wide strips stable | `Mathematics/Advanced Engineering Mathematics` |
| Differential pairs | IPC-2141A edge-coupled corrections (±5% class): Zd = 2Z0(1−0.48e^(−0.96s/h)) µstrip, 2Z0(1−0.347e^(−2.9s/b)) stripline | IPC-2141A; coupled-line background in `AD EMT/Microstrip Lines and Slotlines` ch. 8 |
| Propagation delay | tpd = √εeff / c | `Electromagnetics/Fundamentals of Applied Electromagnetics` |
| Skin effect | δ = √(ρ/(πfµ0)); shell-model Rac floored at Rdc; ρCu(T) with α = 0.00393/K | `Electromagnetics/` texts; verified against the classic 2.09 µm @ 1 GHz |
| Trace current | IPC-2221 fit I = k·ΔT^0.44·A^0.725 (k = 0.048 ext / 0.024 int) | IPC-2221 (standard); circuit context in `Mathematics/` circuit-analysis texts |
| Fusing current | Onderdonk adiabatic melt equation (Tm = 1083 °C) | classical result (Onderdonk) |
| Via parasitics | L = 0.2h(ln(4h/d)+1) nH, C = 1.41εr·T·D1/(D2−D1) (in), barrel R and θ from the plated annulus | Johnson & Graham engineering formulas (±20% class) |
| Crosstalk | saturated backward coefficient ≈ 1/(1+(s/h)²) | high-speed design rule of thumb |
| Plane capacitance | C = ε0εr·A/d | `Electromagnetics/` (parallel-plate) |

## Mathematics layer

`ellipticK` (AGM), the complementary-modulus evaluation, and `solveMonotone`
(bisection with 200-step refinement on monotone impedance curves) are the
reusable numerics under the synthesis functions: `microstripWidthForZ0`,
`striplineWidthForZ0`, `diffMicrostripWidthForZ` — these invert the analysis
models to "what width gives 50 Ω / what width+gap gives 90 Ω differential",
which is exactly the workflow used when global routing rules are tuned for
impedance (6 mil → 50 Ω single-ended, 6/8 mil → 90 Ω USB differential on a
suitable stackup).

## Verified canonical values (run in CI)

- K(0) = π/2; K(1/√2) = 1.85407467730 (Legendre).
- Hammerstad eq. 2.116 hand-evaluated at w/h = 1, εr = 4.4 → 71.0 Ω; the
  H-J implementation agrees within 1%.
- Cohn stripline, w/b = 1, εr = 1 → 65.4 Ω (chart value).
- Cu skin depth @ 1 GHz = 2.09 µm.
- 50 Ω and 90 Ω-differential synthesis round-trip to < 0.05 Ω.
- IPC-2221: 1 mm / 1 oz / ΔT 10 °C external ≈ 2.4 A; internal = ½ external.
- FR-4 microstrip delay within the famous 140–150 ps/inch band.

## Accuracy notes

Closed-form models, not field solvers: H-J single-ended is reference-grade
(≤ 1%); IPC-2141 differential corrections are ±5%; via formulas ±20%. For
sign-off-grade impedance on exotic stackups, validate with a 2-D field solver;
for everything day-to-day these are the same equations the industry's
calculators implement, computed at double precision with exact mathematics.
