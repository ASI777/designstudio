# Phase S12 — PAM4 / differential signalling (E15) + statistical crosstalk eye (E16)

The high-speed-SerDes half of Class B. Exit criterion: a 112G PAM4 differential
channel reports three sub-eye heights and a per-standard pass/fail, and adding a
switching aggressor closes the victim eye by the measured coupled amount.

## E15 — PAM4 (`StatisticalEye.cs`)

NRZ carries one bit per symbol on two levels; PAM4 carries two bits on four
levels (`Pam4Levels` = −1, −1/3, +1/3, +1), so the same symbol rate moves twice
the data — the basis of 100/112G-per-lane. The cost is the eye: four levels stack
into **three sub-eyes**, each only ⅓ the outer separation, so a PAM4 link is
roughly 9.5 dB more sensitive than NRZ before any noise or ISI.

`Pam4SubEyesAt` builds a single level-PDF: starting from a delta, every ISI tap
(and every crosstalk aggressor cursor) is folded in as **4-level random data** —
the neighbouring symbol is equally likely to sit on any of the four levels, so a
tap of value `c` spreads the PDF to `c·{−1,−1/3,+1/3,+1}`. Each sub-eye is then
the gap between the BER tails of the two adjacent level distributions (the upper
level's lower tail minus the lower level's upper tail). With no ISI the three
sub-eyes are each exactly ⅔ of the cursor; an ISI tap closes every sub-eye by
`2·Σ|tap|`, just like NRZ but thrice over.

`Pam4Analyze` sweeps the sampling phase, keeps the phase whose **worst** sub-eye
is widest, and reports all three openings, their min, the **RLM** (ratio of the
min to the max sub-eye — the level-linearity metric) and the centre phase as a
`Pam4Eye`.

## E16 — statistical crosstalk-aware eye

A victim's neighbours are not quiet. `EyeHeightAt`/`Analyze` (NRZ) and the PAM4
path take an `aggressors` argument: a list of per-aggressor coupled-cursor sets.
Each coupled cursor is folded into the victim's distribution as one more random
tap — NRZ aggressors as ±1 data, PAM4 aggressors as 4-level data. Because the
folding is statistical (every aggressor data combination weighted by probability),
the result is the true BER-contour eye, not a single worst-case bit. A switching
NRZ aggressor closes the victim by `2·Σ|coupled cursor|`; more aggressors close it
further. The coupled cursors come from the S3/S-parameter near/far-end coupling
already extracted by `Crosstalk`/`ChannelExtractor`.

## E15 — PAM4 link + differential (`LinkSimulator.SimulatePam4`)

`SimulatePam4(S21, opt, aggressors)` reuses the NRZ pulse machinery — driver edge,
Rx CTLE, Tx FFE, band-limited inverse FFT to a single-symbol response — but reads
the eye with `Pam4Analyze`. `opt.DataRateGbps` is interpreted as the **symbol
rate in GBaud** (56 GBd = 112 Gb/s). It returns the three sub-eyes, their min, the
RLM and the Nyquist insertion loss as a `Pam4Result`.

A differential lane is a routed pair (P and N nets). The MoM extraction already
references the planes, so the P-net channel from `ChannelAssembly.SegmentFromNet`
is used as the differential-through; the companion N trace establishes the coupled
pair. (Full mixed-mode 4-port assembly is the refinement; the single-ended-of-the-
pair through is the standard sign-off approximation for a symmetric pair.)

## E15 — PAM4 compliance (`ComplianceMask.cs`)

`Pam4Mask`/`Pam4Masks` mirror the NRZ masks but key on the **worst sub-eye**
opening plus an **RLM floor** (a PAM4 receiver fails on level non-linearity even
when the eyes are nominally open). Presets cover 112G (56 GBd), 100G (53.125 GBd)
and 56G (28 GBd) at pre-FEC BER 1e-6; `Check` returns a per-standard pass/fail
with the three sub-eyes and RLM in the summary.

## Scope and next

The differential channel uses the P-net through as the differential-mode response
(full mixed-mode S-parameter assembly is the accuracy refinement). PAM4 noise is
modelled as added Gaussian on the level PDF; transmitter level mismatch (true RLM
degradation) is folded through the cursor levels, not yet a separate Tx-linearity
term. Class B finishes with **S13 (the sparse/C++ scale-out)** — pushing the
dense-board solvers onto the native core.
