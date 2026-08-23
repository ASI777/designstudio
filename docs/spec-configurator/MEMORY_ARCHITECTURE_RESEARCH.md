# Research Brief — From Hand-Authored Archetypes to Learned Memory + Verification

**Date:** 2026-06-23
**Status:** Planning / research artifact — not wired into the app.
**Context:** Follows the spec-configurator drafts (`SPEC_TREE_SCHEMA.md`,
`archetype_robotic_arm.yaml`, `WALKTHROUGH.md`). Addresses the brittleness of
hand-authored archetypes by replacing them with a datasheet-derived memory layer
and a verification loop, grounded in current applied-AI research.

---

## 1. The problem with hand-authored archetypes

A hand-authored archetype tree (e.g. `archetype_robotic_arm.yaml`) is a **template**.
Templates are the classic knowledge-engineering bottleneck:

- Every new application family needs a human to author it.
- The system cannot design anything it was not pre-taught.
- The authored weights/defaults go stale and never learn from real designs.

The research consensus across 2025–2026 is to replace authored templates with
**neuro-symbolic CBR-RAG**: store past designs and component knowledge as
retrievable *cases*, retrieve the closest ones, and *adapt* them — rather than coding
a tree per application. The archetype does not disappear; it is **demoted from
authored code to an emergent, retrieved pattern**.

---

## 2. The three-layer memory architecture

```
┌────────────────────────────────────────────────────────────────┐
│  LAYER A — COMPONENT KNOWLEDGE GRAPH  (from datasheets)         │
│  Built by querying DigiKey → datasheet → extractor → KG nodes   │
│  Nodes: parts, pins, power rails, interfaces, required externals│
│  Edges: "needs decoupling", "drives", "compatible-with"         │
│  ← PCBSchemaGen KG (36 pin-role types) + Document GraphRAG      │
├────────────────────────────────────────────────────────────────┤
│  LAYER B — DESIGN-CASE MEMORY  (self-evolving)                 │
│  Every completed board → a memory note: what parts, what nets,  │
│  what worked, what DRC caught. Auto-links to similar past cases. │
│  ← A-Mem (Zettelkasten notes + auto-link + evolution)          │
├────────────────────────────────────────────────────────────────┤
│  LAYER C — VERIFICATION LOOP  (grounding / anti-hallucination)  │
│  LLM proposes netlist → checked against KG rules + DRC →        │
│  interpretable per-pin feedback → LLM refines → repeat          │
│  ← PCBSchemaGen ERC + subgraph-isomorphism checking            │
└────────────────────────────────────────────────────────────────┘
```

### Layer A — Component KG from datasheets
PCBSchemaGen builds a knowledge graph from IC datasheets encoding **36 pin-role
types and electrical rules**, with **semantic pin-role mappings that decouple vendor
naming from function** (`VDD`/`VCC`/`AVCC` → one role). It **compresses ~16k
datasheet tokens to ~300 tokens per component** by storing the graph, not the prose.
This is "memory from datasheets." DigiKey is the feedstock: query → datasheet URL +
metadata → extract → KG node. The existing `datasheet_extractor_agent.py` already
emits `component/2` JSON, which is the node payload.

### Layer B — Self-evolving design memory
A-Mem stores each experience as a **note** with keywords, tags, contextual
description, embedding, and links to other notes. New notes **auto-link by embedding
similarity**, and adding a note can **evolve older notes** — "without predetermined
schemas." Applied here: every finished board becomes a case that links itself to
similar past boards. After N motor-driver designs, "robotic arm controller" is a
*retrieved cluster of real cases*, not an authored YAML.

### Layer C — Verification (the trust mechanism)
LLM schematic generation normally fails through **hallucinated pin connections**
(invalid pin labels, VCC–GND shorts). PCBSchemaGen's answer is a staged checker
(syntax → ERC → intra-component → inter-component subgraph-isomorphism → semantic)
that returns **the exact pins/nets/rules violated**, which the LLM uses to
self-correct. The repo already owns most of this: `drc.cpp` has 13 rule families.
The KG adds the *connectivity/topology* check that geometric DRC does not cover.

---

## 3. What already exists vs. what is new

| Layer | Research basis | Already in repo | To build |
|---|---|---|---|
| A — Component KG | PCBSchemaGen, GraphRAG | `datasheet_extractor_agent.py`, DigiKey API (working) | Graph store + pin-role normalizer |
| B — Design memory | A-Mem, CBR-RAG | `chat-*.jsonl` logs, circuit-state exports | Note structure + auto-link + retrieval |
| C — Verification | PCBSchemaGen ERC/SI | `drc.cpp` (13 geometric rules) | Topology/ERC check vs KG + refine loop |
| Harness | CircuitLM (multi-agent) | Anti-Gravity queue, swarm agents | Wire the loop through the queue |

Nothing is discarded. The additions are a **graph store + retrieval + a refine loop**;
the archetype tree becomes a *seed cache* for Layer B that the system grows past.

---

## 4. Reframing the configurator

- **Before:** the spec tree *is* the knowledge (authored, brittle).
- **After:** the spec tree is a **cold-start cache**. When memory is empty, fall back
  to the authored robotic-arm tree. As real designs accumulate in Layer B, questions
  are **generated from retrieved cases** ("3 past arm controllers used 48V — confirm?")
  instead of hand-coded. Prompt density still governs question count; the *source* of
  the questions shifts from YAML to retrieved cases.

---

## 5. Staged plan

| Stage | Deliverable | Paper |
|---|---|---|
| 1 | Pin-role normalizer (vendor names → ~36 canonical roles) over existing component/2 JSON | PCBSchemaGen |
| 2 | Component KG store: ingest DigiKey-sourced datasheets → graph nodes/edges | GraphRAG, PCBSchemaGen |
| 3 | Topology/ERC verifier: check `connection_agent` netlists against KG rules, per-pin feedback → refine loop | PCBSchemaGen |
| 4 | Design-case memory: each finished board → A-Mem note, auto-linked, embedded | A-Mem |
| 5 | Retrieval-driven configurator: questions from retrieved cases, archetype YAML as cold-start fallback | CBR-RAG |
| 6 | Route the whole loop through Anti-Gravity as multi-agent | CircuitLM |

**Highest-value single piece:** Stage 3 (topology/ERC verifier) — it stops the system
inventing impossible connections and reuses the DRC-engine philosophy. Stages 1–2 are
the "memory from datasheets" layer and are mostly plumbing over existing code.

---

## Sources

- [PCBSchemaGen: Constraint-Guided Schematic Design via LLM for PCB (2026)](https://arxiv.org/abs/2602.00510) — KG from datasheets, pin-role types, ERC + subgraph-isomorphism verification, subcircuit library reuse
- [CircuitLM: Multi-Agent LLM Framework for Circuit Schematics from NL (2026)](https://arxiv.org/html/2601.04505v1)
- [pcbGPT: Automatic PCB Schematic Synthesis from NL Requirements (2026)](https://arxiv.org/html/2606.01188v1)
- [A-Mem: Agentic Memory for LLM Agents (2025)](https://arxiv.org/abs/2502.12110) — Zettelkasten notes, auto-link, memory evolution
- [Agentic Memory: Unified Long/Short-Term Management (2026)](https://arxiv.org/abs/2601.01885) — memory ops as tool actions
- [Review of Case-Based Reasoning for LLM Agents (2025)](https://arxiv.org/html/2504.06943v1) — neuro-symbolic CBR-RAG replacing templates
- [Document GraphRAG for Manufacturing Domain QA (2025)](https://www.mdpi.com/2079-9292/14/11/2102) — KG built from document structure
- [Survey of LLMs for Electronic Design Automation (2025)](https://dl.acm.org/doi/10.1145/3715324)
