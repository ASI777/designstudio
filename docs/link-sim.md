# Phase S4 — Link simulator (IBIS + statistical eye, E4 steps 1–3)

`Model/LinkSim/` turns the channel's frequency response — built from the S2 MoM
RLGC and S3 via models — into a **BER eye**: eye height, eye width, and a mask
pass/fail. That is the number every interface (PCIe, DDR5, Ethernet…) is signed
off against, so it is the point of the whole simulation stack.

Roadmap exit criterion: **eye height within 10 % of a reference sim on a Gen4
reference channel.** We validate against analytic eye cases (below) since a
commercial reference simulator is not available in-tree.

## Pipeline

```
 net ─► ChannelExtractor.ExtractAt(linear grid) ─► S21(f)
                                                     │ × driver edge (IBIS or swing+rise)
                                                     ▼
                              Hermitian inverse FFT ─► channel impulse h(t)
                                                     │ ⊛ UI rect (height = swing)
                                                     ▼
                                       single-bit (pulse) response p(t)
                                                     │ cursors c_k = p(t_s + k·UI)
                                                     ▼
                                StatisticalEye ─► BER eye height / width / mask
```

### E4 step 1 — IBIS (`IbisModel.cs`)

A tolerant IBIS 7.x parser: `[Model]` type, `C_comp`, `[Voltage Range]`,
`[Ramp]` edge rates, `[Pulldown]/[Pullup]` I-V tables, `[Package]` R/L/C. Units
(`p/n/u/m/k/M/G` and `V/A/F/H/Ohm` suffixes) are normalised to SI; unknown
keywords and the min/max columns are ignored. `ToLinearDriver()` derives the
linear driver the LTI pipeline needs: swing (supply), edge rise time (from the
ramp), and output resistance (from the I-V slope). The full nonlinear/AMI buffer
is E4 step 4 (Phase S6).

### E4 step 2 — impulse response (`Fft.cs` + `LinkSimulator`)

`ChannelExtractor.ExtractAt` samples `S21(f)` on a linear grid (DC..fmax, fmax =
8× Nyquist by default), the driver edge (single pole at `0.35/t_r`) shapes it, a
gentle raised-cosine taper on the top 12 % of the band suppresses brick-wall
ripple, and a Hermitian inverse FFT gives the causal channel impulse response.
Convolving with a UI-wide rect of height = swing yields the single-bit (pulse)
response. (The roadmap's `core/convolve.cpp` lives here in managed code.)

### E4 step 3 — statistical eye (`StatisticalEye.cs`)

The received sample is `v = c0·a0 + Σ_{k≠0} ck·a_{-k}` for random bits `a∈{±1}`.
Instead of simulating bits, we build the **distribution** of the ISI term by
convolving each cursor's two-point pmf `{½@+ck, ½@−ck}`, shift by the main cursor
to the "1" level, fold in Gaussian receiver noise, and read the BER tail. Sweeping
the sampling phase gives the eye height (widest vertical opening) and width
(horizontal span open at the target BER); Rj/Dj close the width via the dual-Dirac
budget. This is peak-distortion / StatEye analysis — exactly how Gen4/DDR5
compliance is computed.

## Validation

| Case | Eye height | Expected |
|------|------------|----------|
| Lossless channel, 1 V swing | 2.00 V | full ±swing |
| Cursors {c0=1, c1=0.3, c−1=0.1} | 1.20 V | worst-case 2·(1−0.4) |
| No ISI + noise σ=0.1 V | 0.595 V | 2·(1 − Q·σ), Q≈7.03 @ 1e-12 |
| No ISI + noise σ=0.05 V | 1.296 V | 1.297 |

End-to-end, the eye opens for short low-loss channels and closes monotonically
with trace length, data rate, loss and noise — and a via stub's suckout (S3)
pulls the eye down near its resonance, which backdrilling recovers.

## Scope and future work

NRZ, LTI, matched-termination (S21 as the channel voltage transfer). Out of scope
here and deferred:

- **Equalisation** (Tx FFE, Rx CTLE/DFE) and **vendor AMI** executables — E4 step
  4, Phase S6. These reopen the eye and are the SerDes sign-off; the statistical
  framework already in place extends to them (apply the EQ transfer to the pulse
  response / cursors).
- **PAM4** (three eyes), **crosstalk-aware** statistical eye (fold aggressor PDFs
  in — the E1 coupling matrices already exist), and reflection (S11/S22) effects
  beyond the matched assumption.
- **Compliance masks & jitter decomposition** per standard — E5.
- A **UI** "Simulate link…" action on a net/diff pair opening an eye-diagram view
  with BER contours and the mask overlay.
