> **Superseded (2026-06-12).** This audit described the codebase before the
> industrial engine upgrade. The P0/P1 items below (save/load, undo, fab
> output, connectivity, decorative DRC, router rotation/scalability, net-id
> schema bug, dual source of truth, fixed buffers) have since been addressed,
> and the AI layer was removed entirely. See `CHANGELOG.md` for the current
> state; this file is kept as the historical gap analysis that drove the work.

# Design Studio — Brutal Industrial-Readiness Audit

Date: 2026-06-12 · Scope: full codebase (~1,850 LOC of source)

## Verdict

This is a well-architected **tech demo**, not a tool. The architecture (C++ deterministic core, C# UI, LLM constrained to validated JSON commands) is genuinely the right shape — better thought out than most hobby EDA projects. But no engineer can complete even one real board in it today, because it fails on table stakes before "industrial" is even a question. Altium/Cadence/KiCad represent thousands of person-years; the path to #1 is not feature parity, it is a wedge (see Strategy).

---

## P0 — Disqualifiers (it is not yet a tool)

**1. You cannot save a board.** There is no save/load anywhere. `File → New Board` is the only file operation. Close the app, lose everything. This alone ends any industrial conversation.

**2. No undo/redo.** One wrong drag or Delete is irreversible — and there's no save file to fall back on. Every mutation goes straight to both models with no command stack.

**3. No manufacturing output.** No Gerber, no Excellon drill, no pick-and-place, no BOM. A PCB tool that cannot produce fab files produces literally nothing of value. (It's on the roadmap — at position 6, behind 3D view. It should be position 1.)

**4. No schematic, no netlist, no connectivity.** The repo is named `schematic_designer` and contains no schematic editor. Nets are created ad-hoc strings; there is no ratsnest, no "is this board fully connected?" answer, no ERC, no netlist import. PCB layout without connectivity tracking is drawing, not design.

**5. DRC is decorative.** It checks exactly two things: trace-to-trace clearance and footprint-inside-outline. No pad-to-trace, pad-to-pad, via checks, annular ring, drill-to-copper, minimum width, or unconnected-net checks. Worse, violation details (rule, message, item IDs) are thrown away at the C API boundary — the UI gets only ⊗ locations. An engineer can't act on "something is wrong somewhere."

## P1 — Correctness landmines in what does exist

- **Router ignores pad rotation** (`// NOTE: rotation handled in bbox; refine later`) — routes will pass through or needlessly avoid rotated pads. It routes one layer only, no vias, and `AutoRoute` hardcodes layer 0 and 0.2 mm clearance, ignoring per-net rules.
- **Router scalability:** A* on a 0.1 mm uniform grid with `unordered_map` nodes; a 100×80 mm board is 800k cells per layer. The 2M expansion cap then **fails silently** — user sees "no route" with no reason.
- **Collinearity test uses exact `double ==`** (`router.cpp` simplify loop) — flaky merge behavior.
- **Net identity is a vector index** (`Net.id = nets_.size()`, `clearanceFor` indexes by id, C# rebuilds `netIds` assuming index==id). The moment net deletion exists, every netId in pads/traces dangles. This is a schema bug waiting to detonate.
- **Dual source of truth:** `BoardDocument` (C#) mirrors `Board` (C++) by hand at every mutation site. Trace deletion, rotation-after-move, etc. will drift. One missed sync = DRC/router operating on a different board than the one on screen.
- **Fixed buffers:** routes truncated at 4,096 points, DRC at 1,024 violations — silently.
- **Pads with `netId = -1`** are treated as obstacles for everything including their own future net; unassigned-pad routing is impossible.
- **Single 64-bit id space, linear scans** for find/remove — fine at 50 parts, not at 5,000.

## P2 — Missing industrial table stakes

Copper pours/planes, >2 layers in practice, vias in routing, non-rectangular board outlines, silkscreen/mask (enum values exist; nothing renders or exports them), zones and thermal reliefs, differential pairs, length matching/tuning, teardrops, keepouts, IPC-7351 footprint naming, import of KiCad/Altium libraries, netlist import (KiCad/OrCAD), STEP export of the assembled board, panelization, ODB++.

## P3 — Engineering process

One smoke test (4 asserts) for the entire core; zero tests for the C# side including the AI command validator — the most safety-critical code in the app. No CI. No error model across the C API (no status codes, no way to report *why* routing failed). No autosave/crash recovery. API key stored in plaintext `settings.json`. AI layer hard-wired to one provider despite the `ILlmProvider` abstraction. No logging.

The AI validator (`DesignAssistant.Apply`) checks only that the footprint *origin* is on the board — a part at (0,0) with 20 mm pads hangs off the edge and passes "verification." Pad geometry from the LLM is applied unchecked (the footprint-library path validates; the chat path doesn't).

---

## Strategy: the actual path to #1

You will not out-feature Altium (30+ years, ~10M LOC) or out-free KiCad (1,500 contributors). Head-on is a guaranteed loss. The winnable game:

**Be the AI-native layer of the PCB world, not another editor.** The genuinely differentiated asset here is the propose → validate → repair loop (FootprintGenerator's deterministic validators with LLM repair attempts is the best idea in the codebase). Nobody owns "datasheet PDF in → verified footprint + constraints out" yet — your own roadmap calls it the killer feature, so build *that* first, not 3D views.

**Adopt the KiCad file format** (`.kicad_pcb`, `.kicad_mod`) instead of inventing one. You instantly inherit millions of footprints, every fab's tooling, and a migration path *into* your tool. Your save/load problem and ecosystem problem are the same problem — solve both at once.

### Sequenced roadmap

**Phase 1 — Become a tool (weeks):**
save/load via KiCad format (or JSON v0 then KiCad), autosave; undo/redo command stack (route every mutation through commands — this also fixes the dual-model drift by making C++ the single source of truth with snapshot reads); ratsnest + connectivity engine; Gerber/Excellon/BOM/PnP export; pad-aware DRC with full violation details across the C API; structured error codes on every `dc_*` call.

**Phase 2 — Become credible (months):**
KiCad library import; copper pours; multi-layer routing with via insertion; polygonal outlines; spatial index (R-tree/quadtree) under DRC and router; push-and-shove; real test suite + CI on both sides of the interop boundary.

**Phase 3 — Become #1 in the niche (the wedge):**
datasheet→verified-footprint pipeline as a product, not a dialog; AI design review (placement critique, decoupling/return-path checks with deterministic verification); AI constraint extraction from datasheets; schematic capture with AI netlist generation. Ship the AI layer as a KiCad plugin too — meet the market where it is.

### Brutal one-liner

Right now this is 1,850 lines pretending to be two applications across a P/Invoke boundary, with no save button. Fix persistence, undo, connectivity, and fab output before writing one more AI feature — then go all-in on the datasheet pipeline, because that's the only feature here Altium doesn't already have.
