# Phase S9 — Full DDR5 channel topology (E19)

The last Class-A phase. `DdrTimingEngine` (S6/E7) margins a single byte lane;
this builds the **subsystem hierarchy** a real DDR5 board has and rolls the
margins up into the sign-off table. Exit criterion: a multi-channel, 2DPC
DDR5-6400 board reports a per-channel margin table and an overall pass/fail.

## Structure (`DdrSubsystem.cs`)

```
Subsystem (one socket, or list every socket's channels together)
└── Channel  (name, DimmsPerChannel = 1 or 2)
    ├── ByteLane[]   each = DQ[…] + DQS         → data eyes & timing
    └── CaBus        = CA[…] + CK (fly-by)      → command/address timing
```

`DdrSubsystem.Analyze(doc, name, channels, spec, pdn?, eq?)` returns a `Report`
with a `ChannelResult` per channel (its lane reports, CA report, worst margin,
pass/fail) and an overall worst-channel + pass/fail.

## How each piece is margined

- **Data byte lanes** → `DdrTimingEngine.AnalyzeByteLane` directly — the
  validated S6 path (½·eye − tDS/tDH ∓ skew − crosstalk/SSN/Rj/Dj). SSN comes
  from the S8/S5 PDN model if one is supplied; equalisation from S6 if any.
- **Command/address (fly-by) bus** — CA is the *same* setup/hold problem, but
  referenced to the **clock (CK)** instead of the strobe, at **half the data
  rate** (one command per CK in 1N timing), with command setup/hold (tIS/tIH).
  So it reuses `AnalyzeByteLane` with a half-rate spec and the CA nets as the
  "bits", CK as the "strobe". Because it runs at half rate it carries more
  margin than the data lanes — exactly what the engine reports.
- **2DPC** — a second populated DIMM presents an idle-rank stub that reflects and
  adds ISI. This is modelled as extra deterministic jitter (≈10 % UI) applied to
  every lane in the channel, so a 2DPC channel always shows less margin than the
  same channel at 1DPC. (A full board-level reflection model of the idle rank is
  a later refinement.)

## Roll-up

Channel verdict = the worst margin over its data lanes and CA bus; channel passes
when every lane and CA pass. Subsystem verdict = the worst channel; it passes when
every channel passes. The summary names the worst channel and the worst bit
within it ("CH1/b0:DQ0 fails by …"), which is what a sign-off review needs.

## Scope and next

This completes Class A — a dense multi-socket DDR5 motherboard is designable and
sign-off-able end to end across S2–S9 (RLGC, vias, eye, EQ, PDN/thermal, stackup/
reference, DDR topology). Simplifications that remain refinements: the 2DPC
reflection is a jitter proxy rather than a full multi-rank channel solve; CA is
1N (2N command timing is a spec toggle); and per-socket grouping is by naming
(an explicit socket layer is cosmetic). Class B — HDI/microvias (S10), 112G PAM4
/ statistical crosstalk / vendor AMI / multi-board (S11–S12), and the C++ scale-out
(S13) — is the remainder of `ROADMAP-HIGHEND.md`.
