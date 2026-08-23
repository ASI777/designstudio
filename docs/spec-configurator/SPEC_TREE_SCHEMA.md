# Spec-Tree Schema — Research Artifact

This document defines the **meta-schema**: the shape every archetype spec-tree must
follow so the adaptive Spec Engine can drive it. It is the contract between three
parties:

- **The archetype author** (an engineer) who fills in slots, weights, defaults, and
  the cascade graph once per application family.
- **`intent_agent`** (LLM) which parses a free-text prompt and marks each slot
  `filled` / `implied` / `open`.
- **The Spec Engine** (`spec_engine.py`, future) which computes the question budget
  and order from the two together.

This is a research/design artifact — **not** wired into the app yet.

---

## 1. File layout

One YAML file per archetype: `docs/spec-configurator/archetype_<name>.yaml`.
The robotic-arm reference is `archetype_robotic_arm.yaml`.

```
archetype:           # metadata block
  id, name, summary, match_keywords, base_complexity

slots:               # the weighted dependency graph — the actual product
  - id, prompt, header, question, type, options[], default,
    variance_weight, depends_on, unlocks, locks, fill_hints,
    adds_components, cost_model, layer_impact
```

---

## 2. The slot — every field, and why it exists

| Field | Type | Purpose |
|---|---|---|
| `id` | string | Stable key (e.g. `motor_type`). Referenced by cascade edges. |
| `header` | string ≤12 chars | Chip/tag shown in the UI option card. |
| `question` | string | The full question text shown when the slot is `open`. |
| `type` | enum | `single` \| `multi` \| `scalar` \| `auto`. `auto` slots are never asked — only defaulted. |
| `options` | list | Curated choices. Each carries its own cost/layer/component deltas. |
| `default` | ref | Option `id` applied when the slot is `defaultable` or the user skips. |
| `default_silent` | bool | If true, defaulting happens without ever surfacing the slot (just listed in summary). |
| `variance_weight` | 0.0–1.0 | **Ask-order key.** How much this decision changes the hardware. Highest asked first. |
| `depends_on` | list | Slot is dormant until these slots are answered (and a condition matches). |
| `unlocks` | list | When answered with a given value, these slots become live (the cascade). |
| `locks` | list | When answered with a given value, these slots are removed (branch pruned). |
| `fill_hints` | map | Phrase → option mapping that lets `intent_agent` mark the slot `filled`/`implied`. |
| `adds_components` | list | `ComponentSpec` seeds this answer injects into `design_loop`. |
| `cost_model` | expr | How this answer contributes to the running BOM estimate. |
| `layer_impact` | int | Layers this answer forces (e.g. high-current → +2). |

---

## 3. Slot state — the four-way classification

Before any question is shown, `intent_agent` assigns every slot one of:

| State | Set by | Asked? | In summary? |
|---|---|---|---|
| `filled` | Prompt stated it explicitly (matched a `fill_hints` phrase) | No | Yes — "from your prompt" |
| `implied` | A *prior answer or another filled slot* forces it | No | Yes — "implied, confirm" |
| `defaultable` | Not stated, but `default` exists and `default_silent: true` | No | Yes — "default applied" |
| `open` | Undetermined AND design-critical (no safe silent default) | **Yes** | becomes a question |

The `intent_agent` contract is a single JSON return:

```json
{
  "archetype": "robotic_arm",
  "slot_states": {
    "axis_count":   {"state": "filled",  "value": "six",    "evidence": "6-axis"},
    "motor_type":   {"state": "filled",  "value": "bldc",   "evidence": "BLDC"},
    "control_method":{"state": "implied","value": "foc",    "evidence": "said FOC"},
    "brake":        {"state": "open"}
  }
}
```

---

## 4. The question-budget computation

```
1. Match archetype (intent_agent).
2. For every slot: assign state (intent_agent).
3. Resolve cascade: apply unlocks/locks from all filled+implied slots.
   → prunes whole branches, may open new slots.
4. questions = [ slot for slot in live_slots if slot.state == "open" ]
5. Sort questions by variance_weight DESC.
6. Ask one at a time. After each answer:
      re-run steps 3–5  (an answer can close downstream slots).
7. Offer "Accept remaining defaults" at every step → collapses all open slots
   to their default and jumps to the Summary.
8. On Summary confirm → emit SpecSheet → design_loop.run(spec_sheet).
```

Steps 3 and 6 are why a 12-question sparse prompt collapses: each high-variance
answer prunes the branches below it.

---

## 5. The SpecSheet output (what design_loop receives)

When the tree is complete, the engine emits a flat, validated spec — this replaces
the vague string `design_loop.run()` currently takes:

```json
{
  "archetype": "robotic_arm",
  "resolved_slots": { "axis_count": "six", "motor_type": "bldc", "...": "..." },
  "component_seeds": [
    {"role": "gate_driver", "search_query": "DRV8353 3-phase", "quantity": 6},
    {"role": "mcu", "search_query": "STM32H743 LQFP", "quantity": 1}
  ],
  "constraints": {"copper_oz": 2, "max_layers": 6, "assembly": "reflow"},
  "estimates": {"bom_usd": 184.50, "layer_count": 6, "board_mm2": 4800}
}
```

`component_seeds` is the union of every chosen option's `adds_components`. This is
the precise, deterministic skeleton that makes `design_loop`'s vendor search and
netlist design hit rate jump — it is no longer guessing the BOM, it is filling a
validated one.

---

## 6. Authoring rules (the hard part)

1. **5–7 high-variance slots per archetype carry the design.** Everything else is a
   default or a cascade leaf. If an archetype needs >8 high-weight slots, it should
   probably be split into two archetypes.
2. **Every slot must have a safe `default`.** No dead-ends. If no safe default
   exists, the slot's `variance_weight` is 1.0 and it is always asked.
3. **`fill_hints` is the prompt-density lever.** The richer the phrase map, the more
   a dense prompt pre-fills, the fewer questions. Authoring `fill_hints` well is what
   makes "dense prompt → few questions" actually work.
4. **Cascade edges must be acyclic.** `unlocks`/`locks` form a DAG; the engine
   topologically orders within equal variance weights.
5. **Cost/layer deltas must be real.** The running estimate is the trust feature;
   fabricated numbers destroy it. Pull `cost_model` anchors from live DigiKey where
   possible.

See `archetype_robotic_arm.yaml` for the worked reference and
`WALKTHROUGH.md` for the dense/medium/sparse prompt traces.
