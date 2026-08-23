# Phase S7 — Layer-count scale + reference-plane integrity (E10 + E12)

The first high-end-roadmap phase. It makes thick (18–64 layer) stackups
buildable with a sane plane arrangement, and adds the checks that matter once a
board has many planes: that every controlled net actually keeps a continuous
reference. Exit criterion: a 24-layer stackup where every controlled net has a
continuous reference.

## E10 — Layer scale + multi-plane stackup

- **Clamp.** `BoardDocument.SetCopperLayers` now allows **2–64** layers (was 2–16).
- **Stackup generation.** `EnsureStackup` is *unchanged* for ≤16 layers — the
  classic "L2 = GND, L(n−1) = PWR" two-plane layout, so every existing project
  and test behaves identically. For **>16 layers** it builds a proper thick
  stackup via `PlaneCadence(n)`:
  - reference planes on a **3-layer cadence** (`1, 4, 7, …`) plus a guaranteed
    bottom reference at `n−2`, so **no signal layer is more than one layer from a
    plane** (≤2 signal layers between consecutive planes);
  - **signal outer layers** (microstrip top/bottom);
  - **2 oz plane copper** (`CopperWeightOz = 2`, 0.070 mm) for the power/ground
    planes, as real server boards use.
- **Reference resolution.** `ImpedanceEngine.ReferencePlanes(doc, layer)` returns
  the nearest plane-layer indices above and below a signal layer — the signal's
  reference planes, which the integrity checks track. (`ImpedanceEngine.Reference`
  still returns the geometry/εr for impedance; this returns the plane *indices*.)

For a 24-layer board the cadence gives planes at L1, L4, L7, L10, L13, L16, L19,
L22 — eight 2 oz planes, every signal stripline-referenced, which is the SI-
friendly arrangement those boards actually use.

## E12 — Reference-plane integrity & return path (`ReferenceIntegrity.cs`)

On a 4-layer board "is there a plane?" is enough. On a 24-layer board the
failures are subtler, and the return path is everything:

- **Rule 18 — reference void crossing.** For each controlled trace, the nearest
  reference plane is found, and the trace is sampled against that plane's *actual
  copper* (`PlaneShape.Fill`, even-odd point-in-polygon over the outer ring and
  holes). If the trace passes over a gap/void/split — e.g. an antipad clearance,
  a plane split, or a foreign-net cut-out — the return current has no path under
  it, and it's flagged. (When a reference layer has no drawn `PlaneShape`, its
  `Role = Plane` is taken as solid copper and the geometry test is skipped.)
- **Rule 19 — reference change without a stitch.** For each via on a controlled
  net, the reference plane of the entry layer and the exit layer are compared. If
  they differ (the return current must hop from one plane to another) and there
  is no stitching via — a ground/plane via spanning both reference layers within
  1.5 mm — it's flagged. This is the reference-aware refinement of the plain
  "return via within 1 mm" check.
- **`Audit(doc)`** rolls these up per controlled net into a continuous / not-
  continuous verdict with a reason — the direct expression of the exit criterion.

Both live in a standalone class invoked on demand (like the spatial-PDN check),
so they don't change the counts of the existing DRC run.

## Scope and next

This is S7 of the high-end roadmap. It unlocks **Class A (dense multi-socket
DDR5)** routing together with S8 (split-plane PDN) and S9 (full DDR5 topology).
Still to come for thick boards: HDI/microvias (E11/S10), split-plane multi-rail
PDN (E13/S8), and the C++/scale-out work (E20/S13) for thousand-net extraction
times. A reference-change *electrical* penalty in `ChannelExtractor` is largely
already captured by the S3 via model at the transition; an explicit per-segment
reference-discontinuity term is a later refinement.
