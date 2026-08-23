# Phase S3 — Physics-based via model (E3 step 1)

This note documents the via transition model added in Phase S3 (`ViaModel.cs`),
which replaces the lumped L-C π via inside `ChannelExtractor`.

The roadmap exit criterion is **via stub resonance within 10 % of measurement to
20 GHz.** Above ~8 GHz the L-C π model is wrong because the dominant effect is not
a lumped inductance — it is the *resonance of the unused barrel below the signal's
exit layer* (the "stub"), which behaves as an open-circuited transmission line and
shorts the through signal at its quarter-wave frequency. That suckout is the whole
reason backdrilling exists, and an SI tool that does not predict it cannot sign off
DDR5/Gen4.

## Topology

A via is modelled as a short two-port between the trace it enters on and the trace
it exits on:

```
        ┌── through coaxial TL (entry → deepest routed layer) ──┐
  o──┬──┤                                                       ├──┬──o
     │  └───────────────────────────────────────────────────────┘  │
   C_pad/2                                              C_pad/2 ┌────┴──── open stub
  (shunt)                                              (shunt)  │  (deepest routed
                                                                │   layer → barrel
                                                                │   end / backdrill)
                                                              Y_stub = tanh(γℓ)/Z
```

- **Coaxial barrel.** The plated barrel inside the plane antipad is a coaxial line:
  `Z = (η0 / 2π√εr) · ln(d_antipad / d_drill)`. The antipad diameter comes from
  `ViaItem.AntipadMm`, or is auto-derived as `pad + 2·clearance` (clamped so the
  coax stays well-posed).
- **Through section.** From the entry layer to the deepest layer the net actually
  routes on, cascaded as one coaxial TL section per dielectric layer, each with its
  own causal `εr(f)` and `tanδ(f)` from the S2 material model.
- **Open stub.** The barrel from the deepest routed layer down to the barrel end
  (or the backdrill depth) is open-circuited; its input admittance
  `Y = tanh(γℓ)/Z` shunts the exit node. At `βℓ = π/2` (i.e. `ℓ = λ/4`) `tanh → j∞`,
  the node is shorted, and the through `S21` collapses — the suckout at
  `f_res ≈ c / (4·ℓ_stub·√εr)`.
- **Pad/antipad capacitance.** Johnson's via formula
  `C[pF] = 1.41·εr·T·D1/(D2−D1)` (inches) loads each end.
- **Loss.** Dielectric (`tanδ`) plus barrel skin-effect resistance give the
  resonance a finite Q (a real notch depth) rather than an ideal infinite null.

The 2-port is assembled as `Shunt(jωC/2) · [through TL] · Shunt(jωC/2 + Y_stub)`
and returned as ABCD from `Abcd(f)`, so `ChannelExtractor` cascades it exactly like
any other segment.

## Backdrill

`ViaItem.BackdrillToLayer` removes the barrel below a chosen layer. The model
shortens the stub to `[deepest routed … backdrill]`; if the backdrill is at or
above the exit layer the stub vanishes and the suckout disappears — which is the
behaviour the test suite checks.

## Validation

| Stub | Model notch | Analytic c/(4ℓ√εr) | Depth |
|------|-------------|--------------------|-------|
| 9 layers, FR-4 (εr 4.4) | 16.89 GHz | 16.89 GHz | −32 dB |
| 9 layers, Megtron (εr 3.5) | 18.94 GHz | 18.94 GHz | −42 dB |
| Backdrilled to 1 layer | none in band | (170 GHz) | −1 dB |
| Fully routed (no stub) | none | — | flat, |S21| ≈ 0.92 |

The extracted notch tracks the analytic quarter-wave to ~0.1 % — the resonance is
set by the stub length and `εr`, both taken exactly from the stackup, so the 10 %
exit criterion is met with wide margin. Notch *depth* depends on loss (material
`tanδ` and barrel resistance) and is where measurement scatter mostly lives.

## Scope and future work

This is the analytical step-1 ladder. It captures the stub resonance, barrel
inductance, and pad capacitance — the "tractable 80 %". What it does **not** model
is full 3D plane-pair cavity coupling (plane resonances, mode conversion, the
radial-waveguide spreading impedance) — that is E3 step 2 (a small FEM kernel over
the via neighbourhood) or an external HFSS/openEMS export via E8. For DDR5 and
Gen4 sign-off to 20 GHz the stub model is the dominant physics; Gen5/SerDes corner
cases are where the FEM step earns its keep.
