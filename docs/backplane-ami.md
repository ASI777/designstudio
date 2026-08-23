# Phase S11 — Macromodel + multi-board assembly (E18) + vendor AMI (E17)

The backplane-channel and SerDes-sign-off half of Class B. Exit criterion: a
backplane channel assembled from vendor connectors produces a causal pulse
response and an AMI-equalised eye.

## E18 — Multi-board channel assembly (`ChannelAssembly.cs`)

A backplane link is not one board — it is

```
daughtercard ─► press-fit connector ─► backplane ─► connector ─► daughtercard
```

each a separate S-parameter block. `ChannelAssembly` cascades an ordered list of
2-port blocks by multiplying their ABCD matrices and exposes the combined
`S21(f)`. Board segments come from `SegmentFromNet` (a routed net extracted by
`ChannelExtractor` into an `SParameterBlock`); vendor connector and cable models
come from `.s4p`/`.s2p` files through the existing Touchstone reader. The combined
channel feeds `LinkSimulator.SimulateChannel(S21, …)`, which reuses the driver,
equalisation and eye machinery. Cascading multiplies the losses, so each extra
connector closes the eye — the 112G backplane reality.
`LinkSimulator.PrecursorEnergyFraction` reports the pre-cursor energy of the pulse
response as a causality check.

## E18 — Vector-fit macromodel (`VectorFit.cs`)

The direct inverse FFT is accurate but its causality depends on the band-limiting.
A rational macromodel

```
H(s) = e^{-sτ} · ( d + Σ_k r_k / (s − p_k) ),   Re(p_k) < 0
```

is **causal by construction** — its impulse is exactly zero before the transport
delay τ. The tractable, linear fit: pull τ from the unwrapped phase slope, fit the
smooth residual with a fixed bank of real poles by real least-squares (so residues
and the impulse are real), then re-apply the delay. Fixed poles give ~10 % accuracy
on skin-effect channels (the √f branch cut isn't rational); full Gustavsen
pole-relocation is the accuracy refinement. The causality guarantee is exact and
is what a cascaded backplane time-domain response needs.

## E17 — Vendor IBIS-AMI (`AmiModel.cs`)

A real SerDes sign-off uses the vendor's AMI model: a compiled `.dll`/`.so`
(`AMI_Init` → `AMI_GetWave` → `AMI_Close`) configured by a `.ami` parameter file —
a nested-parenthesis tree of `(name (Usage…)(Type…)(Value…))` clauses.

- `AmiModel.Parse` tokenises and parses the `.ami` tree (handles quoted strings and
  `|` comments).
- `AmiModel.ToEqualizer` maps the `Model_Specific` **CTLE / FFE / DFE** parameters
  onto the in-house equalisers, so the eye opens exactly as the parameters specify.
- `ParametricAmi : IAmiModel` is the managed default, driven by the parsed
  parameters.
- `NativeAmi : IAmiModel` is the P/Invoke wrapper around a real vendor binary
  (resolved at runtime via `NativeLibrary`). Loading an actual vendor `.dll` is a
  platform integration and surfaces a clear error where it (or the symbols) aren't
  present — the cross-platform test suite uses `ParametricAmi`.

## Scope and next

The vector fit uses fixed poles (causal, ~10 % accurate); the accurate channel path
remains the direct IDFT fed by the cascade. The native AMI `GetWave` marshalling is
left as the integration point. The connector/board cascade assumes 50 Ω-referenced
blocks (mixed-mode/differential assembly is the S12 differential work). Class B
finishes with **S12 (112G PAM4 + statistical crosstalk-aware eye)** and **S13 (the
sparse/C++ scale-out)**.
