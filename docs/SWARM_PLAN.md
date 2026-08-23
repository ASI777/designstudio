# Agentic Swarm Plan — schematic_designer Self-Improvement Workflow

**Reference architecture:** Seven-Layer Model for AI Computing (IEEE/FIRST magazine, 2026)
`Physical → Link → Neural Network → Control → Agent → Orchestration → Application`

---

## Overview

A swarm of three cooperating AI runtimes — **Claude (via MCP)**, **a local model on
local hardware**, and a custom **Anti-Gravity MCP server** — continuously reads the
roadmap (`ROADMAP-ADVANCED.md`, `ROADMAP-SIMULATION.md`, `ROADMAP-HIGHEND.md`),
decomposes work, writes code, runs tests, and merges improvements into the
schematic_designer codebase.  Each layer from the seven-layer model has a precise
mapping here.

---

## Layer 1 — Physical Layer

**Confirmed hardware (as of 2026-06-21):**

| Resource | Spec | Swarm role |
|----------|------|-----------|
| GPU | NVIDIA RTX 4050 Laptop, **6 GB VRAM** (5.76 GB free), 192 GB/s BW, CUDA 8.9 | Runs local model entirely on-GPU |
| CPU | Intel i5-13420H, 8 cores / 16 threads, 4.6 GHz | Parallel `cmake -j12`, `dotnet test` |
| RAM | 14 GB DDR5, ~8 GB available | Agent processes, build toolchain |
| Storage | NVMe 136 GB, 114 GB free | Codebase, model weights, build cache |

**No CPU offload.** The GPU bandwidth (192 GB/s) gives ~44 tok/s for a 7B Q4 model.
Offloading even 2 layers to DDR5 (~60 GB/s) creates a bottleneck that drops throughput
below 10 tok/s — not practical for a swarm loop.  Keep 100 % of model weights on GPU.

---

## Layer 2 — Link Layer: Three MCP Servers

Three MCP servers form the communication fabric between agents and tools.

### 2a. Claude MCP Server
Wraps the Anthropic API. Exposes Claude Opus 4.8 (architect/reviewer) and
Claude Sonnet 4.6 (implementer) as callable tools. Handles auth, retries,
streaming. Standard `@anthropic-ai/mcp-server-claude` or equivalent.

### 2b. Local Model MCP Server
Bridges Ollama's REST API into MCP. Every agent can call it the same way they
call Claude — identical tool signature, different cost and latency.
- `POST /api/chat` → `local_model.generate(prompt, context)`
- Exposes model capability tags: `[code-gen, code-review, test-gen, docs]`

### 2c. Anti-Gravity MCP Server (`swarm/antigravity/`)
The coordination backbone — "anti-gravity" means agents coordinate without being
pulled to a single bottleneck. This is a custom Node.js / Python MCP server you
build once. It exposes these tools to every agent:

```
ag_task_push(title, type, priority, context_json)  → task_id
ag_task_claim(agent_id, capability_filter)          → task | null
ag_task_complete(task_id, result_json, patch_path)  → ok
ag_task_fail(task_id, reason)                       → ok (re-queues with backoff)
ag_context_read(key)                                → value
ag_context_write(key, value)                        → ok
ag_broadcast(event_type, payload)                   → ok  (pub/sub for agents)
ag_result_aggregate(task_ids[])                     → merged_result
```

Internal storage: SQLite for the task queue, Redis or a JSON file for shared
context. The queue is pull-based (agents claim work) so no central dispatcher is
a single point of failure — any agent can die and the others keep running.

### 2d. Supporting MCP Servers (standard, not custom)
- **`mcp-server-filesystem`** — read/write source files inside the repo
- **`mcp-server-git`** — `git diff`, `git add`, `git commit`, branch management
- **`mcp-server-shell`** — run `cmake`, `dotnet test`, `ctest` and capture output
- **`mcp-server-fetch`** — fetch IEEE papers, datasheet URLs referenced in docs

---

## Layer 3 — Neural Network Layer: Model Assignment Policy

**Local model: `qwen2.5-coder:7b-instruct-q4_K_M`**

The RTX 4050's 6 GB VRAM (5.76 GB usable) allows exactly one model tier:
7B parameters at Q4_K_M quantization. Benchmarked fit:

| Candidate | VRAM | Verdict |
|-----------|------|---------|
| **Qwen2.5-Coder-7B Q4_K_M** | **4.4 GB** | **CHOSEN — best coding 7B, 128k ctx, function calling** |
| DeepSeek-Coder-6.7B Q4_K_M | 4.2 GB | Good C++ but newer Qwen2.5-Coder beats it |
| Phi-4-mini 3.8B Q4_K_M | 2.6 GB | Too weak for code generation at this scale |
| Llama-3.1-8B Q4_K_M | 4.9 GB | Not code-specialized |
| Qwen2.5-Coder-14B Q4_K_M | 8.2 GB | Does not fit — TOO BIG |

Throughput: **~44 tokens/sec** on this GPU. Expected task latency: 5–30 s per patch.

**Full routing table (all four models):**

| Model | Tier | When to use |
|-------|------|-------------|
| Claude Opus 4.8 | High reasoning | Novel algorithms (router, field solver, push-and-shove), physics math validation, DRC rule design, cross-file architectural decisions |
| Claude Sonnet 4.6 | Balanced | C# WPF implementation, complex C++ refactors, multi-file code review |
| **Local: Qwen2.5-Coder-7B** | **Low cost / fast** | **See task table below** |
| Claude Haiku 4.5 | Ultra-fast | File indexing, one-line status checks, Anti-Gravity queue polling |

Every model receives the same system prompt (content of `CLAUDE.md` + the
relevant ROADMAP section) so context is uniform regardless of which runtime executes.

### Local model task table

Tasks assigned to `qwen2.5-coder:7b` (no Claude API call needed):

| Task type | Examples from this codebase |
|-----------|----------------------------|
| **C++ boilerplate stubs** | New `DcStatus` codes in `c_api.h`, struct definitions, getter stubs in `pcb_model.cpp` |
| **xUnit / ctest scaffolding** | New `PhaseX Tests.cs` skeleton, `SECTION` blocks in `test_core.cpp` |
| **JSON schema edits** | `component.schema.json` new fields, `.dsproj` format version bumps |
| **Parametric footprint math** | `ParametricFootprints.cs` — IPC-7351 pad geometry calculations |
| **Documentation** | `CHANGELOG.md` entries, inline `///` XML doc comments |
| **Build log triage** | Parse `cmake --build` / `dotnet test` stderr, classify errors by file+line |
| **First-pass code review** | Naming conventions, missing null checks, obvious C API status-code skips |
| **Roadmap decomposition (simple items)** | Breaking "add BackdrillTo field" into a file-level task list |
| **Anti-Gravity queue ops** | `ag_task_claim`, `ag_context_read/write`, `ag_broadcast` calls |

---

## Layer 4 — Control Layer: Router & Quality Gate

A lightweight Python process (`swarm/router.py`) that:

1. **Classifies** each task from the Anti-Gravity queue by complexity:
   - Token estimate of the required context + output → selects model tier
   - C++ core tasks → prefer local model first, escalate to Sonnet on failure
   - Physics/math tasks → always Opus

2. **Enforces quality gates** before `ag_task_complete` is called:
   - C++ changes: `cmake --build core/build && ctest --test-dir core/build`
   - C# changes: `dotnet build DesignStudio.sln && dotnet test tests/DesignStudio.Tests/`
   - Patch is rejected and re-queued if tests fail; router records the failure
     reason in shared context so the next agent doesn't repeat the same mistake

3. **Cost cap**: tracks Claude API spend via the response headers; if the hourly
   budget is exceeded, all Opus/Sonnet tasks are held and local model runs solo.

---

## Layer 5 — Agent Layer: Specialized Agents

Seven persistent agent processes (`swarm/agents/`), each an infinite loop of
`ag_task_claim → work → ag_task_complete`. They share no state except through
Anti-Gravity MCP.

### Agent A — Roadmap Decomposer (Claude Opus)
Runs on a schedule (or when `ag_broadcast(new_roadmap_item)` fires).
Reads the three ROADMAP files, identifies the next unimplemented phase item,
and pushes a tree of sub-tasks into the Anti-Gravity queue with dependencies.

Example decomposition for roadmap item `1.1 Spatial Index`:
```
[TASK] core/spatial.cpp: define QuadTree<T> template              → code-gen
[TASK] c_api.h: expose dc_spatial_* query API                     → code-gen
[TASK] pcb_model.cpp: integrate spatial index into mutators        → code-gen
[TASK] test_core.cpp: spatial-index correctness + perf regression  → test-gen
[TASK] ROADMAP-ADVANCED.md: mark 1.1 complete                     → docs
```

### Agent B — C++ Core Engineer (Local model → Sonnet escalation)
Claims `[code-gen, cpp]` tasks. Reads the target file(s), generates the patch,
applies it via `mcp-server-filesystem`, runs the build+ctest gate. On gate
failure, records the error and the diff in `ag_context_write` for Agent E.

### Agent C — C# / WPF Engineer (Claude Sonnet)
Claims `[code-gen, csharp]` tasks. Same loop. Uses `mcp-server-filesystem` to
edit `app/DesignStudio/**/*.cs`, runs `dotnet test` as its gate. Understands
the `BoardDocument.RebuildNative()` invariant from CLAUDE.md and never breaks
the single-source-of-truth contract.

### Agent D — Test Writer (Claude Haiku / local model)
Claims `[test-gen]` tasks. Reads the feature being added (from context written
by B or C), generates xUnit or ctest cases, ensures they fail before the
feature and pass after (TDD contract enforced by Agent E's gate).

### Agent E — Reviewer (Claude Sonnet)
Claims `[review]` tasks auto-created whenever Agent B or C completes a patch.
Reads the diff via `mcp-server-git diff`, checks:
- Coordinate unit consistency (nanometres throughout)
- C API boundary rules (no C++ types crossing, status codes checked)
- `BoardDocument.RebuildNative()` not bypassed
- Net id stability (no index-equals-id assumptions)
Posts findings as `ag_context_write(review_<task_id>, findings)`. If critical
findings exist, calls `ag_task_push(fix_review_findings, ...)` before approving.

### Agent F — Physics & SI Specialist (Claude Opus)
Claims `[physics, si, drc]` tasks. Reads `docs/physics-engine.md` and the
relevant IEEE references (fetched via `mcp-server-fetch`). Validates that
algorithm implementations match the cited equations — Hammerstad–Jensen, Cohn
AGM, IPC-2221, Onderdonk. Never merges a physics formula without citing the
source in a comment.

### Agent G — Documentation & Schema (local model)
Claims `[docs, schema]` tasks. Updates `CHANGELOG.md`, `CLAUDE.md`,
`docs/*.md`, and `docs/datasheet-extractor/component.schema.json` to reflect
completed work. Runs no build gate (docs-only changes), but the Roadmap
Decomposer validates correctness on its next read.

---

## Layer 6 — Orchestration Layer

The **Anti-Gravity MCP server is itself the orchestrator** — there is no single
"master" agent. Instead, the orchestration emerges from the queue:

```
Roadmap Decomposer  →  task queue  →  Engineers (B, C, D)
                                   →  Quality Gate (E, F)
                                   →  Docs (G)
                                   ↓
                            ag_broadcast(phase_complete)
                                   ↓
                        Roadmap Decomposer picks next phase
```

The shared context store (`ag_context_*`) acts as the collective memory:
- `ag_context_read("current_phase")` → what roadmap phase is active
- `ag_context_read("failing_tests")` → list of tests that are red
- `ag_context_read("board_version")` → `BoardDocument.Version` at last extraction
- `ag_context_read("budget_spent_usd")` → API cost this session

When two agents generate conflicting patches for the same file, Anti-Gravity
detects the conflict (same file appears in two incomplete tasks), picks the
higher-priority one, and re-queues the other with `[blocked_by: task_id]`.

---

## Layer 7 — Application Layer

The output: an improved `schematic_designer` codebase.

The swarm never pushes to `main` directly. Each completed phase lands on a
branch (`swarm/phase-1.1-spatial-index`). Agent E reviews the accumulated
diff. If all gates are green, the branch is ready for human review via `gh pr
create`. The human merges; Anti-Gravity picks up the merge event and the
Roadmap Decomposer advances to the next item.

---

## File Layout for the Swarm System

```
schematic_designer/
└── swarm/
    ├── antigravity/
    │   ├── server.py          # Anti-Gravity MCP server (FastMCP or mcp-python)
    │   ├── queue.db           # SQLite task queue (auto-created)
    │   ├── context.json       # Shared context store
    │   └── config.yaml        # Capability tags, budget caps, model assignments
    ├── agents/
    │   ├── base_agent.py      # Claim → work → complete loop + gate runner
    │   ├── decomposer.py      # Agent A
    │   ├── cpp_engineer.py    # Agent B
    │   ├── csharp_engineer.py # Agent C
    │   ├── test_writer.py     # Agent D
    │   ├── reviewer.py        # Agent E
    │   ├── physics_sme.py     # Agent F
    │   └── docs_agent.py      # Agent G
    ├── router.py              # Control-layer classifier + quality gate
    ├── mcp_clients/
    │   ├── claude_client.py   # Wraps Anthropic SDK
    │   ├── local_client.py    # Wraps Ollama REST
    │   └── tools.py           # filesystem, git, shell, fetch MCP wrappers
    └── prompts/
        ├── system_base.md     # CLAUDE.md content + invariants injected into every agent
        ├── decomposer.md
        ├── cpp_engineer.md
        ├── reviewer.md
        └── physics_sme.md
```

---

## Build / Run Sequence

```bash
# 1. Start the local model
ollama serve &
ollama pull qwen2.5-coder:32b

# 2. Start Anti-Gravity MCP server
cd swarm/antigravity && python server.py --port 8765

# 3. Start supporting MCP servers (filesystem, git, shell)
npx @modelcontextprotocol/server-filesystem ${DESIGNSTUDIO_REPO} &
npx @modelcontextprotocol/server-git ${DESIGNSTUDIO_REPO} &

# 4. Start agents (each in its own process / tmux pane)
python swarm/agents/decomposer.py      # reads roadmap, pushes tasks
python swarm/agents/cpp_engineer.py    # claims cpp tasks
python swarm/agents/csharp_engineer.py
python swarm/agents/test_writer.py
python swarm/agents/reviewer.py
python swarm/agents/physics_sme.py
python swarm/agents/docs_agent.py

# 5. Optionally run the router as a sidecar
python swarm/router.py --watch
```

---

## Phased Rollout

| Phase | What gets built | Expected swarm output |
|-------|-----------------|-----------------------|
| **0** | Anti-Gravity server + base_agent.py + claude_client + local_client | Infrastructure only, no codebase changes |
| **1** | Decomposer + one engineer agent (C++), test writer, reviewer | Roadmap items 1.1 (spatial index) + 3.0 (polygon planes) implemented and tested |
| **2** | Add physics SME + C# engineer | Roadmap items 2.1 (2D field solver) + 3.1 (IR-drop) |
| **3** | Full seven-agent swarm | Roadmap phases 1–3 automated end-to-end |
| **4** | Human review becomes the only bottleneck | PRs arriving faster than one human can review — add a second reviewer agent |

---

## Key Design Decisions

**Why pull-based queue, not push?**  
Any agent can crash and restart without losing work — the task stays in the
queue until `ag_task_complete` or `ag_task_fail` is called. Central dispatchers
are the gravity the "anti-gravity" metaphor is designed to avoid.

**Why rebuild native before every call?**  
This is already the `BoardDocument` invariant. The swarm respects it: no agent
patches a caching layer around `RebuildNative()`. Every generated C# patch that
touches `BoardDocument` gets an explicit reviewer check for this.

**Why local model first for C++?**  
The C++ tasks are high-volume (lots of boilerplate structs, fill functions,
test stubs). Running them locally saves API cost for the tasks that genuinely
need Opus-level reasoning (algorithms, physics, DRC rule design).

**Why no shared mutable state between agents except through Anti-Gravity?**  
Agents writing files concurrently → merge conflicts. Anti-Gravity serializes
patches to the same file via the `[blocked_by]` mechanism. The filesystem is
append-only from the swarm's perspective until a patch is committed.
