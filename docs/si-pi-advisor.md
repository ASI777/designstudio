# E21 — SI/PI-aware design advisor

The AI loop closes the design cycle: export the board state, a model recommends
the next moves, you apply them and re-export. Until now that loop reasoned only
about the *netlist* — missing parts, power budgets, unconnected pins. It was
blind to the S2–S13 analysis, so it could not advise on the decisions that
actually matter on a 24–50-layer board: eye margins, via stubs, decap placement,
DDR timing, reference planes.

E21 makes the loop SI/PI-aware in three steps.

## Step 1 — feed it the analysis (already landed)

`circuit-state` now carries the real per-layer **stackup** and summarised
sign-off results: per-net **channel eyes** (`signal_integrity`: eye height/width,
insertion loss, mask pass, and now `has_via_stub`), the **spatial PDN**
(`power_integrity`: worst-Z vs target, resonances, decap ranking, placement
hint), and **DDR byte-lane margins** (`ddr_lanes`: worst setup/hold and the worst
bit) — drill-down summaries, not raw sweeps.

## Step 2 — teach it the design space (`SiPiAdvisor.cs`, `AdviceContract.cs`)

`SiPiAdvisor.Recommend(CircuitState)` is a deterministic recommender that maps
each sign-off failure to the physically correct fix:

- **Closed/failed channel eye** with an un-backdrilled through-via → `backdrill`
  the stub; if it's loss-limited (`insertion_loss_db` ≤ −10 dB) → `set_eq`
  (Tx FFE + Rx CTLE/DFE); closed with neither a stub nor high loss → a
  `stackup`/reference-plane fix.
- **Power rail over its impedance target** → `add_decap` at the antinode (using
  the PDN `placement_hint`), sized by the resonance frequency; any decap with a
  negative ranking contribution (worsening an anti-resonance) → `move_decap`.
- **DDR lane miss** → negative hold margin → `length_tune` the early bit / re-match
  byte-lane skew; negative setup margin → `set_eq`; failing with positive margins
  → a `reference`/SSN check.

This is the ground truth the LLM is held to: the advisor produces the right SI/PI
action for a failing board *without any model call*, which is the E21 exit
criterion. The advice contract (`design-studio.advice/1`) gains the SI/PI ops
(`backdrill`, `move_decap`, `add_decap`, `set_eq`, `length_tune`, `stackup`,
`reference`) alongside the original `add`/`remove`/`replace`. `AdviceContract`
serialises the deterministic report into that JSON and **parses + validates** a
model's response into the same typed `AdviceReport` — malformed JSON, the wrong
schema, or an unknown op fail loudly instead of silently dropping actions, so the
in-house recommender and a model reply are interchangeable downstream.

## Step 3 — make it scale (`ContextScope.cs`)

A thousand-net, 50-layer board is too large to hand a model whole, but the
decisions live in a small hot set: nets whose eye or PDN target fails, lanes that
miss timing, and nets that aren't fully routed. `ContextScope.Apply` keeps that
set in full and caps the healthy remainder to a `ContextBudget`, recording what
was omitted in `context_note` so the model knows the view is scoped (and can ask
for a net-scoped drill-down). A board already within budget is untouched.

## Where it plugs in

`CircuitStateExport.BuildState` now returns the typed `CircuitState` (and `Build`
serialises it), so a caller can `BuildState → ContextScope.Apply → serialize`, or
run `SiPiAdvisor.Recommend` directly on the state to get grounded fixes before
(or instead of) a model call. The full advisor prompt lives in
`docs/circuit-advisor/ADVISOR_PROMPT.md`.

## Scope and next

The recommender is rule-based and first-order — it names the right fix and where,
not the exact tap weights or decap value to three digits (the engines size those
when the fix is applied and re-analysed). Net-scoped retrieval is a cap-and-note
today; a learned relevance ranker and an apply-then-re-simulate loop (close the
loop automatically) are the natural extensions. With E21 the roadmap's AI tier is
delivered: the advisor reasons about SI/PI sign-off, not just parts.
