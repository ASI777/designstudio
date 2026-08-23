# Phase S10 — HDI: blind / buried / stacked microvias (E11)

The first Class-B phase. Until now a via was one mechanical barrel spanning
`FromLayer..ToLayer`. HDI boards — Apple-M-class laptops, AI baseboards, dense
backplanes — are built from laser microvias (one build-up layer each, stacked or
staggered) plus blind and buried mechanical vias. S10 makes those first-class.
Exit criterion: a stacked-microvia transition extracts S-parameters and the fab
output lists the correct laser/mechanical drill spans.

## Via classes (`ViaItem.ViaType`, `HdiVias.Resolve`)

| Type | Span | Drill | Notes |
|------|------|-------|-------|
| Through  | full board (L1..Ln) | mechanical | the default |
| Blind    | one end outer, other inner | mechanical | |
| Buried   | inner-to-inner | mechanical | |
| Microvia | one build-up layer | **laser**, small (≤0.15 mm), small capture pad | stack or stagger for depth |

`HdiVias.Resolve` uses the explicit `Type` flag when set to Microvia, otherwise
infers from geometry: a one-layer span with a ≤0.15 mm drill is a microvia; a
full span is Through; an outer-touching span is Blind; otherwise Buried. The flag
round-trips through `ProjectIO`.

## Signal integrity

No new model was needed. A laser microvia is ~0.06–0.1 mm long — far too short to
resonate in band — so it is a capacitance-dominated transition that the existing
`ViaModel` already produces (the stub it would compute is empty or resonates at
hundreds of GHz). A **stacked microvia** (e.g. L1→L2→L3→L4) is just three short
transitions in series, and `ChannelExtractor` already multiplies every via's ABCD
along the net — so a stacked-microvia HDI transition extracts passive, low-loss
S-parameters out of the box (validated in the tests).

## Design rules (`HdiVias.Check`, rule 25)

- **Microvia aspect ratio** — build-up depth ÷ drill must be ≤ 1.0 (laser drilling
  won't plate a deeper hole reliably).
- **Microvia span** — a laser microvia is a single build-up layer; a multi-layer
  span should be stacked microvias or a mechanical via.
- **Annular ring / capture pad** — `(pad − drill)/2` ≥ 50 µm.
- **Mechanical aspect ratio** — barrel ÷ drill ≤ 12 (plating limit).
- **Stack depth** — more than 3 stacked microvias needs a special process; any
  stacked microvia must be copper-filled (flagged as a note).

`HdiVias.Stacks` groups co-located microvias on consecutive spans so the stack
rules can see the whole stack.

## Fab output (`FabExporter.DrillSpanReport`)

A human-readable drill program: every distinct (class, layer-span, drill) grouped
and labelled **LASER** (microvia) or **MECH** (through/blind/buried) with its
1-based layer span, drill diameter and count — exactly what the fab needs to set
up the laser and mechanical drill passes for an HDI build. The Excellon files
themselves already group by span (one file per plated span).

## Scope and next

The microvia SI model is the short-transition approximation (capacitance +
tiny inductance), which is right to ~20 GHz; a full laser-via barrel/pad model is
a refinement. Staggered-vs-stacked routing automation and copper-fill tracking
are router/fab features beyond this analysis layer. Class B continues with
S11–S12 (112G PAM4, statistical crosstalk, vendor AMI, multi-board channel
assembly) and S13 (C++/sparse scale-out).
