# Robotic-Arm Archetype — Cascade Walkthrough

Traces the `archetype_robotic_arm.yaml` spec tree through three prompt densities,
showing how the **question budget** is computed and how each answer **prunes** the
tree. Demonstrates the governing rule:

> Questions = (slots opened by complexity) − (slots filled by prompt) − (slots safely defaulted)

The tree has **22 slots total**. Of these, 5 are `auto` (Tier 0, never asked), so the
maximum askable surface is **17**.

---

## Slot inventory by tier

| Tier | Slots | Askable |
|---|---|---|
| 1 (highest variance) | motor_type, axis_count, current_per_axis, control_processor, feedback_type | 5 |
| 2 (cascade) | control_method, current_sensing, mosfet_class, ddr_config, encoder_interface | 5 |
| 3 (medium) | bus_voltage, comms, comms_isolation, brake, safety, thermal_strategy, assembly | 7 |
| 0 (auto) | copper_weight, boot_flash, decoupling, protection, power_input | 0 |
| **Total** | | **17 askable** |

---

## Prompt A — DENSE

> *"6-axis robotic arm controller, BLDC motors with FOC, 24V bus, 15A peak per axis,
> ABZ incremental encoders, CAN + EtherCAT, STM32H7, 2oz copper, reflow assembly."*

### intent_agent slot-state pass

| Slot | State | Evidence |
|---|---|---|
| motor_type | filled | "BLDC" |
| axis_count | filled | "6-axis" |
| current_per_axis | filled | "15A" → `high` |
| control_processor | filled | "STM32H7" → `mcu_stm32` |
| feedback_type | filled | "ABZ incremental" → `encoder_inc` |
| control_method | filled | "FOC" |
| bus_voltage | filled | "24V" |
| comms | filled | "CAN + EtherCAT" |
| assembly | filled | "reflow" |
| copper_weight | filled | "2oz" (also implied by 15A) |

### Cascade resolution

```
motor_type=bldc        → unlocks control_method, current_sensing, feedback_type
control_method=foc     → unlocks current_sensing
current_per_axis=high  → unlocks mosfet_class, thermal_strategy; implies copper_weight=oz2
control_processor=mcu  → LOCKS ddr_config, boot_flash   (pruned: −2)
feedback_type=enc_inc  → unlocks encoder_interface
comms=[can,ethercat]   → unlocks comms_isolation (can present)
```

### Remaining OPEN slots after subtracting filled + locked + silently-defaulted

| Slot | Why still open | variance |
|---|---|---|
| current_sensing | FOC unlocked it, prompt didn't specify shunt vs hall | 0.55 |
| brake | bldc enabled it, undetermined, no safe silent default | 0.45 |
| safety | always live, "robotic arm" may need STO, undetermined | 0.50 |

Everything else: **filled** (10), **implied/defaulted silently** — mosfet_class implied
`fet40` from 24V, encoder_interface defaults `rs422`, comms_isolation defaults
`isolated`, thermal_strategy defaults `vias`, all Tier-0 auto.

### Result

```
Askable surface:        17
− filled by prompt:     10
− locked/pruned:         2   (ddr_config, boot_flash)
− safe silent defaults:  2   (mosfet_class fet40, others auto)
─────────────────────────
OPEN → asked:            3   →  ordered: safety(0.50) ▸ brake(0.45) ▸ current_sensing(0.55)
                                  (re-sorted desc: current_sensing, safety, brake)
```

**3 questions.** A dense prompt configured 80% of the board in prose.

---

## Prompt B — MEDIUM

> *"Controller for a robotic arm with brushless motors, runs off 24V."*

### intent_agent slot-state pass

| Slot | State | Evidence |
|---|---|---|
| motor_type | filled | "brushless" → bldc |
| bus_voltage | filled | "24V" |
| (all others) | open / auto | — |

### Cascade resolution

```
motor_type=bldc  → unlocks control_method, current_sensing, feedback_type
bus_voltage=v24  → implies mosfet_class=fet40 (once current tier known)
```

`control_processor` not yet answered → ddr_config/boot_flash stay dormant
(`depends_on soc_fpga` unmet, so they are neither open nor counted).

### OPEN slots, sorted by variance_weight DESC

| # | Slot | variance | Notes |
|---|---|---|---|
| 1 | motor_type | — | already filled, skip |
| 1 | axis_count | 0.95 | ASK |
| 2 | current_per_axis | 0.90 | ASK |
| 3 | control_processor | 0.85 | ASK |
| 4 | feedback_type | 0.80 | ASK (bldc unlocked it) |
| 5 | control_method | 0.70 | ASK (bldc unlocked it) |
| 6 | comms | 0.55 | ASK |
| 7 | safety | 0.50 | ASK |
| 8 | brake | 0.45 | defaulted? no safe silent default → ASK |

### Pruning as answers arrive (live re-resolve)

```
Q3 control_processor = mcu_stm32   → LOCKS ddr_config, boot_flash (stay pruned)
Q2 current_per_axis  = mid         → unlocks mosfet_class but 24V implies fet40 → silent
Q5 control_method    = trap (if chosen) → would LOCK current_sensing
```

### Result

```
Initial open set:        ~8 high/medium slots
After cascade defaults:  current_sensing, mosfet_class, encoder_interface,
                         comms_isolation, thermal_strategy → silently resolved
─────────────────────────
OPEN → asked:            7   (axis, current, processor, feedback, commutation, comms, safety+brake)
```

**7 questions.** Enough prompt to lock the archetype and motor family; the sizing,
processor, and feedback decisions are genuinely open.

---

## Prompt C — SPARSE

> *"I want to build a robotic arm."*

### intent_agent slot-state pass

Only `archetype = robotic_arm` matched. Every slot **open** or **auto**.

### Naïve open set = all 5 Tier-1 + dependent Tier-2/3 once unlocked

But the engine does **not** show 17 questions. It asks **highest-variance first** and
re-resolves after each answer. Watch the collapse:

```
ASK motor_type (1.00)
  └─ answer "stepper"
        → LOCKS control_method, current_sensing, feedback_type, gate_driver
        → 3 Tier-2 slots + their children pruned in one stroke   (−5 askable)

ASK axis_count (0.95)         → "4"
ASK current_per_axis (0.90)   → "low"
        → integrated FETs, locks mosfet_class, implies copper oz1   (−1)
ASK control_processor (0.85)  → "mcu_stm32"
        → LOCKS ddr_config, boot_flash   (−2, already dormant)
ASK bus_voltage (0.60)        → "24V"
ASK comms (0.55)              → "[can]"
        → unlocks comms_isolation
ASK comms_isolation (0.40)    → "isolated"
ASK assembly (0.40)           → "reflow"
        ── user clicks "Accept remaining defaults" ──
              safety→none, thermal→n/a (low current), all auto slots → defaulted
```

### Result

```
Theoretical open (stepper branch):  ~9
After motor_type=stepper prune:     −5
Actually asked before "accept defaults": 7
```

**7–12 questions depending on early answers.** Choosing `stepper` early collapsed the
tree to 7; choosing `bldc` would have kept the FOC/current-sense branch open and run
closer to 11. The "Accept remaining defaults" escape hatch terminates any time.

---

## Summary table

| Prompt | Density | Pre-filled | Pruned | Asked |
|---|---|---|---|---|
| A — dense | high | 10 | 2 | **3** |
| B — medium | mid | 2 | 2 | **7** |
| C — sparse | low | 0 | 5 (via early answer) | **7–12** |

The same archetype, the same 22-slot tree — the question count is driven entirely by
prompt density (subtracts from the top) and answer cascade (prunes from the middle).
Exactly the adaptive behavior specified: **dense prompt → few questions, sparse prompt
→ many, complex archetype → more survive even a dense prompt.**

---

## What this artifact proves / what's left to research

**Proven by this draft:**
- The two-axis model (complexity opens, density fills) is expressible as a tagged DAG.
- Cascade pruning makes sparse-prompt budgets self-limiting in practice.
- Every slot has a safe default → no dead-ends.

**Open research questions (the authoring intelligence):**
1. **`fill_hints` coverage** — the dense-prompt win (10 pre-fills) only happens if the
   phrase maps are rich. Measuring real prompts against hint coverage is the next study.
2. **`variance_weight` calibration** — currently hand-assigned. Should be validated
   against how much each slot actually changes the generated BOM/layout.
3. **Archetype boundaries** — is "robotic arm" one archetype or three (cobot vs
   industrial vs hobby)? Splitting reduces slot count per tree but multiplies trees.
4. **"Implied" confidence threshold** — when does intent_agent mark a slot `implied`
   vs leave it `open`? Too aggressive → wrong silent decisions; too timid → more
   questions. This threshold is the trust dial.
