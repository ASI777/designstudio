# Memory Architecture + Agentic Harness

Implements the three-layer memory and the deterministic workflow orchestrator
from `docs/spec-configurator/MEMORY_ARCHITECTURE_RESEARCH.md` and
`docs/orchestration/PRODUCTION_ORCHESTRATION_RESEARCH.md`.

Stdlib-only (runs on the local 6 GB box). No external services, no embedding
model — similarity is TF-IDF; storage is JSON files.

## Modules

| File | Layer | Responsibility |
|------|-------|----------------|
| `pin_roles.py` | A | Normalize vendor pin names → ~16 canonical roles (PCBSchemaGen-style). |
| `component_kg.py` | A | Ingest `component/2` JSON → property graph (parts, pins, rails, required-externals). Query API. |
| `design_memory.py` | B | A-Mem style design notes with TF-IDF retrieval + auto-linking (self-evolving). |
| `verifier.py` | C | Netlist ERC against the KG: hallucinated pins, ground shorts, power-floating, missing decoupling. Returns LLM-facing feedback. |
| `harness.py` | — | Deterministic workflow state machine wiring A+B+C: routing → retrieve → components → netlist → **verify gate** → store. Checkpointed, budget-guarded, crash-resumable. |

## Quick start

```bash
# Build the KG from the extracted datasheet library
python3 -m swarm.memory.component_kg testdata/component-library

# Self-tests / demos
python3 -m swarm.memory.design_memory   # retrieval + auto-link
python3 -m swarm.memory.verifier        # ERC on a broken netlist
python3 -m swarm.memory.harness         # full pipeline: gate closes, memory retrieves
```

## How the harness maps to the production patterns

- **Routing** — `classify_intent()` (build / modify / ask). Keyword now; swap in an
  LLM call later with the same contract.
- **Prompt chaining** — phases run in fixed order, control returns between each:
  `start → retrieve → components → datasheet → netlist → verify → store`.
  The **datasheet** phase fetches + extracts a component/2 JSON for any proposed
  part not yet in the KG and ingests it, so the netlist designer always has the
  pin structure it needs (the intent→datasheet→netlist bridge).
- **Passives skip extraction** — capacitors/resistors/inductors/ferrite-beads get
  a deterministic **IPC-7351 land pattern** (`passives.py`) keyed by package code
  (0402/0603/0805…), not a datasheet extraction. No LLM call, DRC-safe footprint,
  and no electrical property lost (value comes from the MPN, net from the netlist,
  any rating requirement from the parent IC's `required_externals`). This is the
  big speed win — a typical build drops from ~26 LLM calls to ~5.
- **Budget**: scales with component count (`_budget()`), and **never aborts once a
  netlist exists** — verify/store/apply are deterministic and free, so a completed
  design is always validated and applied rather than discarded over budget.
- **Component substitution + cascade** — in the datasheet phase, an *IC* with no
  datasheet OR out of stock is **substituted** with an in-stock alternative that
  has one. Because per-IC sub-components (decoupling, pull-ups…) are instantiated
  at netlist time from the part's KG `required_externals`, the substitute's
  sub-components automatically replace the original's. If no substitute works the
  part is **dropped**, and its datasheet-derived sub-components are never created
  (the cascade). Wired via `Deps.find_substitute` / `provider_substitute`.
- **Evaluator-optimizer** — the `verify` phase feeds errors back to `netlist` and
  re-proposes, up to `MAX_REFINE`.
- **Durable execution** — every phase checkpoints the `Session` to JSON;
  `Harness.resume(sid)` continues a crashed run from its last phase.
- **Governance** — `BUDGET_LLM_CALLS` aborts a runaway design.

This is the **runtime** orchestrator (ships in the product). It is **not**
Anti-Gravity (prototype/dev-swarm only).

## Self-improvement: the closed retrieve→reuse loop

The harness doesn't just *remember* more with use — it *designs better* with use:

1. **Retrieve (quality-weighted)** — `retrieve()` ranks past cases by
   `similarity × (0.5 + 0.5·quality)`, so a clean prior design outranks a flaky
   one at equal similarity.
2. **Reuse (CBR)** — `_p_components` / `_p_netlist` adopt a similar good case's
   components and *verified* netlist before calling the LLM. If the seeded
   netlist still passes the verifier gate, the design lands in **0 refines, 0 LLM
   calls**.
3. **Outcome signal** — `_p_store` scores each design: clean-first-pass = 0.95
   (1.00 if it came from reuse), each refine costs quality, partial = 0.25. That
   score feeds back into step 1, so good designs are preferred and bad ones fade.
4. **Credit** — a reused case's `reuse_count` increments, so frequently-useful
   designs rise further.

Demonstrated in `python3 -m swarm.memory.harness`:

```
Run 1 (cold):   1 refine, 3 LLM calls, quality 0.75
Run 2 (reuse):  0 refines, 0 LLM calls, quality 1.00   ← cheaper AND cleaner
```

The more good designs the corpus holds, the more often a new design lands clean
on the first pass for free.

## Wiring the real agents (production)

The harness is LLM-agnostic. Inject the existing agents as phase providers:

```python
from swarm.memory.harness import Harness, Deps
from swarm.memory.component_kg import ComponentKG
from swarm.memory.design_memory import DesignMemory
from swarm.agents import intent_agent, connection_agent

kg  = ComponentKG().load()
mem = DesignMemory().load()

def propose_components(session):
    intent = intent_agent.parse(session.intent_text)
    # … vendor-search each spec, return [{ref, mpn}] …

def propose_netlist(session, feedback):
    # connection_agent.design(component_jsons, goal + feedback)
    # return [{ref, pin, net}]

deps = Deps(kg=kg, memory=mem,
            propose_components=propose_components,
            propose_netlist=propose_netlist)
result = Harness(deps).run("build a BLDC robotic arm controller, 24V, FOC")
```

The C++/Qt app drives this via `QProcess` (a thin CLI wrapper), keeping the
orchestrator embedded and serverless per the production research.

## End-to-end: prompt → board on the canvas

`harness_cli.py "<intent>" --project <path>` runs the whole chain and **applies the
design to the board**:

```
intent → components → datasheet → netlist → verify → store
                                                  │
                                          apply_to_board
                                                  │
        .dsproj with placed footprints (real pad geometry) + net table
                                                  │
                 Qt file-watcher reloads → canvas shows the design
                                                  │
                        run /subsystem then /route to finish
```

`apply_to_board.py` instantiates a footprint per component (real pad geometry from
the extracted component/2 JSON, a generic default for value-only passives), builds
the net table, wires each pad's net from the netlist, sizes the board, and writes
the `.dsproj` (backing up any existing project to `.bak`). Extracted datasheets are
persisted to `~/.config/product_design/library/` so their footprints are reusable.

## MCP connection to the suite (`harness_mcp.py`)

The runtime orchestrator is exposed as an stdio MCP server so the Qt app (and
Claude) drive it instead of spawning agents ad-hoc.

```bash
python3 -m swarm.memory.harness_mcp        # stdio transport
```

Register it (same pattern as `product_server.py`), e.g. in `~/.claude/settings.json`:

```json
{ "mcpServers": {
    "design-harness": {
      "command": "python3",
      "args": ["-m", "swarm.memory.harness_mcp"],
      "cwd": "${DESIGNSTUDIO_REPO}"
    } } }
```

Tools:

| Tool | LLM needed | Purpose |
|------|-----------|---------|
| `design_build(intent)` | yes | Run the full workflow for a NL intent |
| `design_status(session_id)` | no | Fetch a session (phase, log, result) |
| `design_record_outcome(session_id, outcome)` | no | Feed back accepted/exported/manufactured/rejected |
| `design_list(limit)` | no | Recent sessions |
| `memory_search(query, k)` | no | Retrieve similar past designs (quality-weighted) |
| `memory_stats()` | no | Corpus size, links, mean quality |
| `kg_ingest(dir)` / `kg_stats()` / `kg_component(mpn)` | no | Build/inspect the Component KG |

The no-LLM tools are fully operational with zero external deps. `design_build`
wires the real `intent_agent` + `connection_agent` and **degrades gracefully**
(clear failure, never a crash) when the LLM backend or vendor APIs are absent.

## Production properties (what makes it operation-ready)

- **Self-improving**: closed retrieve→reuse loop + real-world outcome signal
  (`record_outcome`) + A-Mem evolution (corroboration). A repeat design reuses a
  prior verified case via `adapt_netlist` (ref remapping) → 0 refines, 0 LLM calls.
- **Correct**: an empty/degenerate netlist is never stored as "clean" — it
  refines then fails (quality 0.10), so bad results can't poison reuse.
- **Durable**: every phase checkpoints; `Harness.resume(sid)` continues a crash.
- **Governed**: `BUDGET_LLM_CALLS` aborts runaways.
- **Embedded/serverless**: stdlib only, JSON stores; the app drives it over MCP.

## Compact-KG netlist generation (`netlist_designer.py`)

The netlist step sends the KG's **compact ~300-token representation per part**
(pins as `number:name:role`, rails, required-externals) to the LLM — not the full
16k-token datasheet. A 12-part board is ~1800 tokens / 7 KB (0.34% of ARG_MAX),
so it fits in argv and the LLM stays focused on connectivity. This fixed the old
`connection_agent` "Argument list too long" overflow and uses Layer A directly.

Verified end-to-end: compact prompt → 10-connection netlist → verifier PASSED →
stored (quality 0.95) for a DRV8353 gate-driver design.

The LLM call is injectable (`design_netlist(..., llm_call=)`) so the pipeline is
testable without a model; the default tries the Anthropic API then `agy`.

## Storage locations

- KG:      `~/.config/product_design/component_kg.json`
- Memory:  `~/.config/product_design/design_memory/note_*.json`
- Sessions:`~/.config/product_design/sessions/session_*.json`
