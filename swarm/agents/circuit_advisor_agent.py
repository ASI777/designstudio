"""
Circuit Advisor Agent
=====================
Wraps docs/circuit-advisor/ADVISOR_PROMPT.md as a live agent that accepts a
circuit-state JSON and returns a validated advice/1 JSON.

Information wired in from docs/:
  • docs/circuit-advisor/ADVISOR_PROMPT.md → system prompt (verbatim, full text)

The six mandatory checks from the prompt are:
  1. Power budget — rails, loads, missing regulators, missing input/output caps
  2. Unconnected pins — classify: work-to-do or missing-part
  3. Interfaces — USB/MIPI/HDMI ESD, terminations, pull-ups, connectors
  4. Electrical compatibility — VIH/VIL levels, drive vs. load, thermal
  5. DRC/ERC findings — identify which part change would fix each
  6. SI/PI sign-off — channel eyes, PDN impedance, DDR timing (from si-pi-advisor.md)

After the LLM produces advice/1, the deterministic SI/PI advisor (sipi_advisor.py)
runs in parallel. The two outputs are merged: deterministic findings take precedence
for SI/PI ops; the LLM is authoritative for add/remove/replace/connections.

Anti-Gravity task type: [circuit_advise]
Model: claude-opus-4-8 (complex multi-constraint reasoning, full context)
"""
import json
import os
import sys
from pathlib import Path
from typing import Optional
import llm_backend

from sipi_advisor import recommend as sipi_recommend
from schema_validator import validate_or_raise, ValidationError

# ── Load the advisor prompt from docs/ ───────────────────────────────────────
_ADVISOR_PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "docs" / "circuit-advisor" / "ADVISOR_PROMPT.md"
)

def _load_advisor_prompt() -> str:
    """Extract the PROMPT section from ADVISOR_PROMPT.md."""
    text = _ADVISOR_PROMPT_PATH.read_text()
    start = text.find("## PROMPT")
    end   = text.find("## END PROMPT")
    if start == -1 or end == -1:
        return text
    return text[start:end].strip()

_SYSTEM_PROMPT = _load_advisor_prompt()

# ── Context scoping — cap large boards to the hot set ────────────────────────
_MAX_CONTEXT_CHARS = 80_000   # ~20k tokens; keep well within 200k Claude context

def _scope_context(state: dict) -> tuple[dict, str]:
    """
    Implement ContextScope.Apply from si-pi-advisor.md:
    Keep all failing nets in full. Cap healthy remainder proportionally.
    Returns (scoped_state, context_note).
    """
    raw = json.dumps(state)
    if len(raw) <= _MAX_CONTEXT_CHARS:
        return state, ""

    # Identify hot-set: failing SI nets, failing PI rails, DDR lanes with issues
    failing_nets: set[str] = set()
    for si in state.get("signal_integrity", []):
        if not si.get("eye_open", True) or not si.get("mask_pass", True):
            failing_nets.add(si.get("net", ""))
    for pi in state.get("power_integrity", []):
        if pi.get("worst_z_mohm", 0) > pi.get("target_mohm", 1):
            failing_nets.add(pi.get("net", ""))
    for lane in state.get("ddr_lanes", []):
        if lane.get("worst_hold_ps", 0) < 0 or lane.get("worst_setup_ps", 0) < 0:
            failing_nets.add(lane.get("lane", ""))
    # Always include unconnected pins (they're small)
    failing_nets |= {p.get("net","") for p in state.get("unconnected_pins", [])}

    # Keep all failing-net components; sample the rest
    all_comps  = state.get("components", [])
    hot_refs   = set()
    for comp in all_comps:
        for pin in comp.get("pins", []):
            if pin.get("net") in failing_nets:
                hot_refs.add(comp.get("ref"))

    hot_comps   = [c for c in all_comps if c.get("ref") in hot_refs]
    cold_comps  = [c for c in all_comps if c.get("ref") not in hot_refs]

    # How many cold components can we afford?
    hot_chars  = len(json.dumps(hot_comps))
    budget     = _MAX_CONTEXT_CHARS - hot_chars - 10_000   # reserve for nets etc.
    cold_keep  = []
    used       = 0
    for c in cold_comps:
        s = len(json.dumps(c))
        if used + s > budget:
            break
        cold_keep.append(c)
        used += s

    dropped = len(cold_comps) - len(cold_keep)
    note = (
        f"Context scoped: {len(hot_comps)} hot components (failing nets) shown in full; "
        f"{len(cold_keep)}/{len(cold_comps)} healthy components included; "
        f"{dropped} healthy components omitted to fit context budget. "
        f"All DRC/ERC findings and unconnected pins are present."
    ) if dropped > 0 else ""

    scoped = dict(state)
    scoped["components"] = hot_comps + cold_keep
    scoped["context_note"] = note
    return scoped, note


# ── Advice validation ─────────────────────────────────────────────────────────
_VALID_OPS = {
    "add","remove","replace","backdrill","move_decap","add_decap",
    "set_eq","length_tune","stackup","reference","set_layers"
}

def _validate_advice(advice: dict) -> list[str]:
    failures = []
    if advice.get("schema") != "design-studio.advice/1":
        failures.append("Wrong schema — must be 'design-studio.advice/1'")
    if not advice.get("summary"):
        failures.append("'summary' is required")
    for i, action in enumerate(advice.get("actions", [])):
        op = action.get("op","")
        if op not in _VALID_OPS:
            failures.append(f"Action[{i}]: unknown op '{op}'")
        if not action.get("reason"):
            failures.append(f"Action[{i}] (op={op}): 'reason' is required")
    return failures


# ── Merge LLM + deterministic SI/PI advice ───────────────────────────────────
def _merge_advice(llm: dict, sipi: dict) -> dict:
    """
    Merge deterministic SI/PI findings with LLM advice.
    Deterministic findings for SI/PI ops take precedence;
    LLM is authoritative for add/remove/replace.
    """
    SI_PI_OPS = {"backdrill","move_decap","add_decap","set_eq","length_tune","stackup","reference","set_layers"}
    llm_actions  = [a for a in llm.get("actions", [])  if a.get("op") not in SI_PI_OPS]
    sipi_actions = [a for a in sipi.get("actions", []) if a.get("op") in SI_PI_OPS]
    llm_sipi     = [a for a in llm.get("actions", [])  if a.get("op") in SI_PI_OPS]

    # Include LLM SI/PI ops only if deterministic found nothing for that net
    det_nets = {a.get("net") for a in sipi_actions}
    llm_sipi_extra = [a for a in llm_sipi if a.get("net") not in det_nets]

    all_actions = sipi_actions + llm_sipi_extra + llm_actions
    all_actions.sort(key=lambda a: a.get("priority", 99))

    merged = dict(llm)
    merged["actions"] = all_actions
    merged["risks"]   = list(set(llm.get("risks",[]) + sipi.get("risks",[])))
    if sipi_actions:
        merged["summary"] += f" ({len(sipi_actions)} deterministic SI/PI action(s) injected.)"
    return merged


# ── Core analysis function ────────────────────────────────────────────────────
def analyze_circuit(
    circuit_state_path: str,
    model: str = "claude-opus-4-8",
    max_retries: int = 2,
) -> dict:
    """
    Run the circuit advisor over a circuit-state JSON file.
    Returns a merged advice/1 dict (LLM + deterministic SI/PI).
    """
    with open(circuit_state_path) as f:
        state = json.load(f)

    scoped_state, context_note = _scope_context(state)
    if context_note:
        print(f"Context scoped: {context_note}")

    # Run deterministic SI/PI advisor in parallel (it's instant)
    sipi_advice = sipi_recommend(state)
    if sipi_advice["actions"]:
        print(f"SI/PI advisor: {len(sipi_advice['actions'])} deterministic finding(s)")

    state_json = json.dumps(scoped_state, indent=2)

    user_msg = (
        "Here is the circuit-state JSON. Analyse it against the design_goal and "
        "respond with ONLY the advice/1 JSON object — no prose outside it.\n\n"
        f"```json\n{state_json}\n```"
    )

    last_failures: list[str] = []
    for attempt in range(max_retries + 1):
        retry_ctx = ""
        if attempt > 0 and last_failures:
            retry_ctx = (
                "\n\nYour previous response failed these validation checks:\n"
                + "\n".join(f"• {f}" for f in last_failures)
                + "\n\nFix ALL issues and re-emit the JSON."
            )

        raw = llm_backend.complete(
            system=_SYSTEM_PROMPT,
            user=user_msg + retry_ctx,
            model=model,
            max_tokens=8192,
        ).strip()
        if raw.startswith("```"):
            raw = raw[raw.find("\n")+1:]
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]
        raw = raw.strip()

        try:
            advice = json.loads(raw)
        except json.JSONDecodeError as e:
            last_failures = [f"JSON parse error: {e}"]
            continue

        failures = _validate_advice(advice)
        if not failures:
            merged = _merge_advice(advice, sipi_advice)
            return merged

        last_failures = failures
        print(f"Attempt {attempt+1}: {len(failures)} validation failure(s) — retrying")

    # If LLM keeps failing, at least return the deterministic findings
    print("LLM advisor failed validation — returning deterministic SI/PI findings only")
    return sipi_advice


# ── Apply advice actions via MCP tools ───────────────────────────────────────
def apply_advice(advice: dict, project_path: str) -> list[str]:
    """
    Apply advice/1 actions to the .dsproj file using the MCP servers.
    Returns list of applied action descriptions.
    """
    applied = []
    for action in advice.get("actions", []):
        op  = action.get("op","")
        mpn = action.get("mpn","")
        net = action.get("net","")
        ref = action.get("for_ref","")

        # These require human confirmation / physical changes
        physical_ops = {"backdrill","set_eq","length_tune","stackup","reference","set_layers"}
        if op in physical_ops:
            applied.append(f"[FLAGGED] {op} on '{net or ref}': {action.get('reason','')[:120]}")
            continue

        # MCP tool calls would go here when the servers are running
        if op == "add":
            applied.append(f"ADD {mpn} — {action.get('reason','')[:80]}")
        elif op == "remove":
            applied.append(f"REMOVE {ref} — {action.get('reason','')[:80]}")
        elif op == "replace":
            applied.append(f"REPLACE {ref} → {mpn} — {action.get('reason','')[:80]}")
        elif op == "add_decap":
            applied.append(f"ADD DECAP on '{net}' ({action.get('value','')}) — {action.get('reason','')[:80]}")
        elif op == "move_decap":
            applied.append(f"MOVE {ref} on '{net}' — {action.get('reason','')[:80]}")

    return applied


# ── Anti-Gravity agent loop ───────────────────────────────────────────────────
def run_as_agent(antigravity_url: str):
    import requests, time
    agent_id = "circuit-advisor-1"
    print(f"CircuitAdvisorAgent starting — connecting to {antigravity_url}")

    while True:
        resp = requests.post(f"{antigravity_url}/task/claim", json={
            "agent_id": agent_id,
            "capability_filter": ["circuit_advise"]
        })
        if resp.status_code == 204:
            time.sleep(10); continue

        task    = resp.json()
        task_id = task["task_id"]
        ctx     = task.get("context_json", {})
        path    = ctx.get("circuit_state_path", "")

        print(f"Claiming task {task_id}: advise {Path(path).name}")
        try:
            result = analyze_circuit(path)
            requests.post(f"{antigravity_url}/task/complete", json={
                "task_id": task_id, "result_json": result
            })
            n_actions = len(result.get("actions",[]))
            n_crit    = sum(1 for a in result.get("actions",[]) if a.get("priority")==1)
            print(f"Task {task_id} complete: {n_actions} actions ({n_crit} critical)")
        except Exception as e:
            requests.post(f"{antigravity_url}/task/fail", json={
                "task_id": task_id, "reason": str(e)
            })
            print(f"Task {task_id} failed: {e}")


# ── MCP tool entry points ─────────────────────────────────────────────────────
def mcp_analyze_circuit(circuit_state_path: str) -> dict:
    return analyze_circuit(circuit_state_path)

def mcp_apply_advice(advice_json: str, project_path: str) -> list[str]:
    advice = json.loads(advice_json) if isinstance(advice_json, str) else advice_json
    return apply_advice(advice, project_path)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python circuit_advisor_agent.py <circuit-state.json> [output.json]")
        sys.exit(1)

    state_path = sys.argv[1]
    out_path   = sys.argv[2] if len(sys.argv) > 2 else None
    model      = os.environ.get("ADVISOR_MODEL", "claude-opus-4-8")

    print(f"Analyzing {state_path} with {model}…")
    result = analyze_circuit(state_path, model=model)

    n_actions = len(result.get("actions", []))
    n_crit    = sum(1 for a in result.get("actions", []) if a.get("priority") == 1)
    print(f"\nSummary: {result.get('summary','')}")
    print(f"Actions: {n_actions} total, {n_crit} critical")

    if out_path:
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Saved → {out_path}")
    else:
        print(json.dumps(result, indent=2))
