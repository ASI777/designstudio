# Phase S6 — Equalisation (E4 step 4) + DDR5 timing engine (E7)

The final phase: the equalisers that make Gen4/Gen5/SerDes links close, and the
per-byte-lane DDR5 timing engine that produces the sign-off margin table. It
consumes everything the stack built — the RLGC channel (S2), via models (S3), the
statistical eye (S4), and the PDN cavity (S5).

## E4 step 4 — Equalisation (`Equalization.cs`)

At multi-gigabit rates the raw channel eye is closed; the link works only because
of equalisation. Three engines, applied inside the LTI statistical-eye pipeline:

- **Tx FFE** — an FIR pre-emphasis with taps at UI spacing, convolved into the
  pulse response. Sharpens the edge and cancels ISI before the channel.
- **Rx CTLE** — a continuous-time linear equaliser (one zero, two poles) that
  boosts the high frequencies the channel attenuated. Applied in the frequency
  domain before the inverse FFT.
- **Rx DFE** — decision feedback that cancels the first *N* post-cursor ISI taps
  exactly (no noise enhancement); in the statistical eye those cursors are
  dropped from the distortion sum.

Set via `LinkSimulator.Options.Eq`. Validated: a closed 16 Gb/s lossy eye reopens
175 → 628 mV (CTLE) → 736 mV (+DFE) → 963 mV (+FFE).

### AMI hook

`IAmiModel` is the IBIS-AMI vendor interface (`AMI_Init`/`AMI_GetWave`/`AMI_Close`).
A real vendor model is a compiled `.dll`/`.so`; a P/Invoke wrapper implements this
interface (a platform integration, not runnable in the Linux CI). The shipped
`BuiltInAmi` drives the same interface with the in-house equalisers, so vendor
models — which *are* the sign-off for Gen5/SerDes — drop in unchanged.

## E7 — DDR5 timing engine (`DdrTimingEngine.cs`)

The number a DDR5 board is signed off on: does every DQ bit have positive setup
and hold margin against the strobe at the target rate? Per bit:

```
½·eye = ½ · (equalised statistical-eye width) · UI        (S4 + S6 over S2/S3 channel)
skew  = delay(DQ) − delay(DQS)                            (DelayEngine)
jitter = xtalk + ssn + Dj + Q(BER)·Rj
   xtalk = Σ NEXT(aggressor lane bits) · swing / slew     (Crosstalk)
   ssn   = Z_pdn(f_data) · (N_bits · I_per_bit) / slew    (PlaneCavity, E6)

setup = ½·eye − tDS − skew − jitter
hold  = ½·eye − tDH + skew − jitter
```

The output is a per-bit margin table and a lane pass/fail with the worst bit named
("DQ3 fails setup by 4 ps at 6400 MT/s"). DDR5-6400 and -4800 presets ship; the
spec (tDS/tDH, swing, edge rate, Rj/Dj, per-bit current, BER) is overridable.

### Server-grade DDR5 capstone

`PhaseS6Tests.ServerGradeDdr5Capstone` builds a representative server byte lane —
8× DQ + DQS as length-matched 18 mm Megtron-6 striplines on a planed inner layer,
1 mm pitch (low crosstalk), with a decoupled 20×20 mm VDDQ plane (E6) supplying the
SSN term — and asserts the lane passes DDR5-6400 with positive setup and hold
margins (≈ +20 ps) and clears the DDR5 mask. A counter-example with a 14 mm skewed
DQ fails and is named, showing the engine discriminates.

## Scope and future work

- LTI / NRZ, matched termination. PAM4 (three eyes), reflection (S11/S22) effects,
  and full statistical crosstalk (folding aggressor PDFs into the victim eye via
  the E1 coupling matrices) are extensions.
- The DDR engine runs an independent link sim per DQ bit; caching the cross-section
  RLGC across a lane would speed a full-bus sweep.
- Real vendor AMI `.dll` loading (the `IAmiModel` P/Invoke wrapper) and per-standard
  mask polygons / DDR5 derating tables remain platform/data integrations.

With S6 the roadmap is complete: S1 exchange, S2 RLGC + materials, S3 vias, S4 the
link eye, S5 PDN + masks, S6 equalisation + DDR sign-off.
