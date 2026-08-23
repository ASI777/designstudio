# Research Brief — Production Agent Orchestration for the Shipped Product

**Date:** 2026-06-23
**Status:** Research / planning artifact — not built.
**Scope:** The *runtime* orchestration inside the shipped desktop app when an end
user runs a design. **NOT** Anti-Gravity — that is prototype/dev-swarm tooling and
does not ship (see memory `project-swarm`).

---

## The headline finding: this is a WORKFLOW, not an autonomous agent

Anthropic's *Building Effective Agents* draws the load-bearing distinction:

- **Workflows** — "systems where LLMs and tools are orchestrated through
  **predefined code paths**."
- **Agents** — "systems where LLMs **dynamically direct their own processes** and
  tool usage."

Their guidance: *start with the simplest thing; only reach for autonomous agents
for open-ended problems where you can't predict the steps.* The PCB design pipeline
**is** predictable:

```
intent → vendor search → datasheet extract → netlist → verify → place → route → DRC
```

These phases are known in advance. Therefore the production orchestrator should be a
**deterministic state machine (a workflow)**, with the LLM invoked only at specific
nodes — **not** an autonomous orchestrator that decides its own steps. The 2026
production consensus says the same: *"a deterministic backbone with intelligence
deployed at specific steps; agents are invoked intentionally by the flow, and control
always returns to the backbone when an agent completes."* (Morph, beam.ai)

This matters because it tells us **not** to build the fancy thing. A state machine is
simpler, debuggable, reproducible, and cheaper than an autonomous agent — and it fits
a one-user/one-project/known-pipeline product exactly.

---

## The five workflow patterns, mapped to this product

Anthropic's five composable patterns map cleanly onto what we already have or need:

| Pattern | What it is | Where it lives here |
|---|---|---|
| **Routing** | Classify input → dispatch to a handler | **Intent classifier** (gap #1): "build" → design pipeline, "fix/route/drc" → specialist agent, "what/why" → Q&A. Today `routeInput()` is keyword-only and misses "build X". |
| **Prompt chaining** | Fixed sequential steps, each consumes the last | The **design_loop** spine: intent → vendor → datasheet → netlist. Each step's output feeds the next. |
| **Parallelization** | Fan out independent calls | **Vendor search** across DigiKey/Mouser/Nexar (already parallel in `aggregator.py`); **datasheet extraction** of N components could fan out. |
| **Evaluator-optimizer** | One call generates, another critiques, loop | The **verification loop** (Task 6): LLM proposes netlist → checker validates against the component KG + DRC → per-pin feedback → LLM refines. |
| **Orchestrator-workers** | Central LLM splits work dynamically | Only the **gap-fill iteration** (unpredictable missing parts) genuinely needs this; the rest is fixed chaining. Use it narrowly. |

**Key discipline (from the research):** *"keep LLMs focused on intent classification
and natural-language understanding while routing execution to typed, testable code."*
In this product the LLM should only **propose** (intent, datasheet fields, netlist);
**DRC, routing, placement, net assignment** stay deterministic C++/Python. That is
already mostly the architecture — preserve it.

---

## Durable execution: the `.dsproj` is already the checkpoint store

The 2026 reliability literature (DBOS, Temporal, Inngest, Azure Durable Task) converges
on **durable execution**: checkpoint after each step, make steps idempotent, resume from
the last checkpoint after a crash. Their guidance:

- *"Checkpoint after each complete step — after every LLM call and after every tool
  returns."*
- *"Retries are only safe when the operation is idempotent or the workflow has a
  recorded result it can reuse."*
- *"Failures usually come from weak state management, memory, retries, observability and
  governance — not the model itself."*

This product is **already 70% of the way there by accident**, because every agent reads
and rewrites the `.dsproj`:

| Durable-execution primitive | Already present | To add |
|---|---|---|
| Checkpoint after each step | Each phase writes `.dsproj` (atomic `QSaveFile`) | A `phase` marker in the file so the orchestrator knows where it stopped |
| Resume after crash | Last successful `.dsproj` survives a force-quit | Read `phase`, continue from there instead of restarting |
| Idempotency | Agents rebuild native state from `.dsproj` each run; DigiKey token cache; "already in BOM" checks | Idempotency keys for *external side-effects* (a DigiKey order, a file download) so a retry doesn't duplicate |
| Observability | stdout streamed to chat; daily JSONL chat log | A structured phase/▸status/▸cost event log |
| Governance / budget | — | Per-design token-spend cap and a stop button (the LLM path can run 15–20 calls) |

**Library-based, not infrastructure.** DBOS's pitch — *"write standard functions, no
external orchestrator or job queue"* — is exactly right for a **desktop product**. We
must **not** ship Temporal/Step-Functions/a daemon. The orchestrator is an embedded
state machine in the Qt app; the `.dsproj` is the durable store; `QProcess` runs the
agents. No server.

---

## Reference architecture for the embedded orchestrator

```
┌───────────────────────────────────────────────────────────────────┐
│  Qt app = the orchestrator (deterministic state machine, C++)     │
│                                                                   │
│  routeInput ──► INTENT CLASSIFIER (routing pattern, 1 LLM call)   │
│                      │                                            │
│        ┌─────────────┼─────────────┐                             │
│        ▼             ▼             ▼                             │
│     "build"       "fix/run"      "ask"                            │
│        │             │             │                             │
│        ▼             ▼             ▼                             │
│   DESIGN WORKFLOW   specialist    Q&A advisor                     │
│   (prompt chain)    agent         (single call)                  │
│        │                                                         │
│   ┌────┴─────────────────────────────────────────────┐          │
│   │ phase machine, each phase = checkpoint to .dsproj │          │
│   │  intent ▸ vendor∥ ▸ datasheet∥ ▸ netlist          │          │
│   │            ▸ VERIFY (evaluator-optimizer loop)    │          │
│   │            ▸ place ▸ route ▸ DRC                  │          │
│   │  on fail: retry(step, idempotency-key, backoff)   │          │
│   │  on crash: resume from .dsproj `phase`            │          │
│   │  budget guard + cancel between every phase        │          │
│   └───────────────────────────────────────────────────┘          │
│        │                                                         │
│   agents run as QProcess; .dsproj is the state bus;             │
│   file-watcher reload repaints canvas live                      │
└───────────────────────────────────────────────────────────────────┘
```

Properties this buys (all from the research as production must-haves):
- **Deterministic & reproducible** — same inputs, same phase path, every run.
- **Crash-tolerant** — `.dsproj` phase marker = resume point.
- **Transparent** — each phase visibly named in the UI (Anthropic principle #2).
- **Cheap to debug** — a state machine, not an opaque autonomous loop.
- **Governed** — budget cap + cancel between phases; no runaway spend.

---

## What's already true vs. what to build

**Already true (don't rebuild):**
- `.dsproj`-as-state-bus + atomic writes + file-watcher reload → durable checkpoints, live UI.
- Agents as async `QProcess`; UI thread freed (Task 2).
- Parallel vendor search (`aggregator.py`).
- LLM-proposes / deterministic-engine-disposes split (DRC, route, place are typed code).

**To build, in dependency order:**
1. **Intent classifier (routing)** — the single highest-leverage gap; "build X" reaches the design workflow instead of Q&A.
2. **Phase marker + resume** in `.dsproj` — turns the accidental checkpointing into real durable execution.
3. **Verification loop (evaluator-optimizer)** — stops silent cascade failure from hallucinated nets (Task 6).
4. **Retry + idempotency keys** for external side-effects (orders, downloads).
5. **Budget guard + structured phase/cost event log** — governance + observability.

The embedded state machine itself is small — the pipeline is linear. The intelligence
is already in the agents; the orchestrator just sequences, checkpoints, verifies, and
guards them.

---

## The one principle to carry forward

The research is unanimous and it contradicts the instinct to build something clever:

> **Don't build an autonomous agent. Build a deterministic workflow with LLM calls at
> named steps, checkpointed to the file you already write, with a verifier gate and a
> budget guard.**

Simplicity, transparency, and a deterministic backbone — not autonomy — are what make
agent systems survive contact with a real user.

---

## Sources

- [Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) — workflows vs agents; the five patterns (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer); simplicity/transparency principles; when NOT to use agents
- [DBOS — Durable Execution for Crashproof AI Agents](https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents) — checkpoint-per-step, idempotency, library-based (no external orchestrator) durable execution
- [Inngest — Durable Execution: Key to Harnessing AI Agents in Production](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents) — retries, resumption, multi-point failure handling
- [Morph — LLM Workflows: Patterns, Tools & Production Architecture (2026)](https://www.morphllm.com/llm-workflows) — deterministic backbone + intelligence at specific steps
- [beam.ai — 6 Multi-Agent Orchestration Patterns for Production (2026)](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production) — sequential workflows, maker-checker loops, cascade-failure risk
- [MLflow — Building Production-Ready AI Agents in 2026](https://mlflow.org/articles/building-production-ready-ai-agents-in-2026/) — observability/governance as the real production bottleneck
- [Durable Execution for LLM Agents 2026: Temporal + LangGraph](https://appscale.blog/en/blog/durable-execution-llm-agents-temporal-langgraph-checkpointing-2026) — checkpointing patterns; LangGraph state-machine model
- [AgentsRoom](https://agentsroom.dev/ai-agent-orchestrator) — desktop app that manages agent subprocesses with live status (active/idle/done/error) — a reference for the QProcess management + status UI
