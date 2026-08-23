# Phase S13 — solver scale-out + C++ hot path (E20)

The last Class-B phase: making the SI engines practical on real board sizes. A
24-layer board has thousands of controlled nets, each an independent extraction
that is itself a stack of dense solves. Exit criterion: those nets extract in
parallel, in minutes not a weekend, with progress and cancel, reusing unchanged
work, and the hot dense-solve runs natively when the core library is present.

## The dense solve is the one hot path (`DenseSolver.cs`, `core/src/linalg.cpp`)

Profiling the MoM RLGC extraction (`MoM2D`) and the FDM PDN solver shows almost
all of the time in one place: an LU factor + solve of an N×N dense system
(N≈400 for a meshed cross-section), repeated for vacuum and dielectric, per
frequency, per net. Everything funnels through that, so it is the single
migration target the roadmap names.

`DenseSolver.SolveColumns` is that funnel. It factors A once and solves every
right-hand side (the per-conductor excitations). The managed LU with partial
pivoting is the always-correct reference and the default; when the native core
library is loaded, the factor+solve runs in C++ (`dc_dense_lu_solve` →
`dc::dense_lu_solve`) instead. The two paths use identical pivoting and the same
singular-pivot clamp, so they agree to numerical precision — verified both by a
C++ unit test (`core/tests`) and by a managed test that extracts the same
microstrip with the native path forced off and on. `MoM2D` now calls
`DenseSolver` rather than carrying its own LU, so the PDN solver and any future
dense consumer share the one accelerated routine. `NativeMath` probes for the
library once and falls back silently, so nothing here is required for
correctness — only for speed.

## Run every net at once (`SweepScheduler.cs`)

The nets are independent, so the throughput win is simply spreading them across
all cores. `SweepScheduler.Run` is an order-preserving parallel map: results are
written by input index (deterministic output regardless of completion order), a
live `IProgress<SweepProgress>` reports the completed-net count, and a
`CancellationToken` stops the sweep cooperatively (a cancelled token surfaces an
`OperationCanceledException`). `RunKeyed` returns an id→result map for the
per-net board sweep.

## Don't recompute what didn't change (`ExtractionCache.cs`)

Re-routing one net, moving one decap or running a what-if leaves most controlled
nets byte-for-byte identical. `ExtractionCache` keys each net's result on the net
id, the frequency-grid signature (a 64-bit FNV over the grid, so different grids
never collide) and the document's monotonic `BoardDocument.Version`. Any edit
bumps the version, so the touched board misses and recomputes while everything
else is a hit. It is thread-safe, so it backs the parallel sweep directly.

## The board-wide entry point (`ChannelExtractor.ExtractManyParallel`)

`ExtractManyParallel(doc, netIds, freqs, cache, progress, ct)` ties the three
together: it warms any one-time lazy document state on the first net
single-threaded (extraction is otherwise read-only), then fans the rest out
through the scheduler, each net consulting the cache. The result is a net-id → S-
parameter map. A second run over an unchanged board is all cache hits; an edit
recomputes only what moved. This is the path a full 24-layer board's controlled
nets run through.

## Scope and next

The native hot path is the dense LU; vector fitting and the FDM assembly remain
managed (they are not yet the bottleneck, and the dense solve they would share is
already native). The cache is per-process and keyed on the document version; a
persistent on-disk cache across sessions is the obvious extension. Building the
core library (`cmake` in `core/`) is what flips the dense solve from managed to
native — the suite passes either way.

With S13, both board classes — the dense multi-socket DDR5 motherboard (Class A,
S7–S9) and the 112G/HDI/backplane baseboard (Class B, S10–S12) — are designable
and now extract at real scale. The remaining roadmap item is **E21**, the SI/PI
design advisor that consumes all of these engines' output.
