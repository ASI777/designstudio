#!/usr/bin/env python3
"""Production MCP server — the design harness exposed to the software suite.

This is how the Qt app (and Claude) drive the runtime orchestrator. Register it
as an stdio MCP server (same pattern as product_server.py); the suite calls these
tools instead of spawning agents ad-hoc.

Tools
  design_build           — run the full workflow for a natural-language intent
  design_status          — fetch a session (resume point, log, result)
  design_record_outcome  — feed back a real user action (accepted/exported/…)
  design_list            — recent sessions
  memory_search          — retrieve similar past designs (quality-weighted)
  memory_stats           — corpus size, links, mean quality
  kg_ingest              — ingest component/2 datasheets into the KG
  kg_stats / kg_component— inspect the Component KG

The non-LLM tools (memory/kg/outcome/status) are fully operational with no
external dependencies. design_build wires the real intent + connection agents as
providers and degrades gracefully (clear failure, never a crash) when the LLM
backends (agy/Claude) or vendor APIs are not configured.

Run:  python3 -m swarm.memory.harness_mcp            # stdio
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Make swarm importable when launched directly.
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from mcp.server.fastmcp import FastMCP

from swarm.memory.providers import kg, mem, make_harness

mcp = FastMCP(
    "design-harness",
    instructions=(
        "Runtime orchestrator for the PCB design suite. design_build runs the "
        "deterministic workflow (intent → components → netlist → verify gate → "
        "store) with a self-improving memory. Feed real outcomes back with "
        "design_record_outcome so the memory learns which designs actually shipped."
    ),
)

def _harness():
    return make_harness()


# ── Design tools ──────────────────────────────────────────────────────────────
@mcp.tool()
def design_build(intent: str) -> str:
    """Run the design workflow for a natural-language intent.

    intent: e.g. "build a BLDC robotic arm controller, 24V, FOC, CAN".
    Returns JSON: session_id, intent_class, status, note_id, quality, refines,
    reused (bool), components, log_tail.
    """
    s = _harness().run(intent)
    return json.dumps({
        "session_id": s.session_id,
        "intent_class": s.intent_class,
        "status": s.status,
        "note_id": s.note_id,
        "quality": round(s.quality, 3),
        "refines": s.refine_count,
        "llm_calls": s.llm_calls,
        "reused": s.reused_netlist,
        "components": s.components,
        "log_tail": s.log[-8:],
    }, indent=2)


@mcp.tool()
def design_status(session_id: str) -> str:
    """Fetch a stored design session (phase, status, log, result)."""
    s = _harness().resume(session_id)
    if not s:
        return json.dumps({"error": f"session '{session_id}' not found"})
    from dataclasses import asdict
    return json.dumps(asdict(s), indent=2)


@mcp.tool()
def design_record_outcome(session_id: str, outcome: str) -> str:
    """Feed a REAL user action back into memory so it learns what shipped.

    outcome: one of accepted | exported | manufactured | rejected.
    Adjusts the design note's quality (manufactured/exported boost; rejected
    penalises), which changes how strongly future designs reuse it.
    """
    note = mem().find_by_session(session_id)
    if not note:
        return json.dumps({"error": f"no design note for session '{session_id}'"})
    before = note.quality
    ok = mem().record_outcome(note.id, outcome)
    if not ok:
        return json.dumps({"error": f"invalid outcome '{outcome}'",
                           "valid": ["accepted", "exported", "manufactured", "rejected"]})
    return json.dumps({"note_id": note.id, "outcome": outcome,
                       "quality_before": round(before, 3),
                       "quality_after": round(note.quality, 3)})


@mcp.tool()
def design_list(limit: int = 10) -> str:
    """List recent design sessions on disk."""
    h = _harness()
    sessions = []
    for f in sorted(h.dir.glob("session_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text())
            sessions.append({"session_id": d["session_id"], "status": d["status"],
                             "intent": d["intent_text"][:60],
                             "quality": d.get("quality", 0)})
        except Exception:
            pass
    return json.dumps({"sessions": sessions}, indent=2)


# ── Memory tools ──────────────────────────────────────────────────────────────
@mcp.tool()
def memory_search(query: str, k: int = 5) -> str:
    """Retrieve similar past designs (quality-weighted)."""
    hits = mem().retrieve(query, k=k)
    return json.dumps({"results": [
        {"note_id": n.id, "content": n.content, "score": round(sc, 3),
         "quality": round(n.quality, 3), "reuse_count": n.reuse_count,
         "corroborations": n.corroborations, "real_outcome": n.real_outcome}
        for n, sc in hits]}, indent=2)


@mcp.tool()
def memory_stats() -> str:
    """Design-memory corpus statistics."""
    m = mem()
    qs = [n.quality for n in m.notes.values()]
    s = m.stats()
    s["mean_quality"] = round(sum(qs) / len(qs), 3) if qs else 0
    s["with_real_outcome"] = sum(1 for n in m.notes.values() if n.real_outcome)
    return json.dumps(s, indent=2)


# ── KG tools ──────────────────────────────────────────────────────────────────
@mcp.tool()
def kg_ingest(directory: str = "") -> str:
    """Ingest component/2 datasheet JSON from a directory into the Component KG."""
    if directory:
        d = directory if Path(directory).is_absolute() else str(_root / directory)
    else:
        from swarm.runtime_paths import component_library_dir
        d = str(component_library_dir())
    added = kg().ingest_dir(d)
    kg().save()
    return json.dumps({"ingested": len(added), "mpns": added[:20],
                       "store": kg().stats()}, indent=2)


@mcp.tool()
def kg_stats() -> str:
    """Component KG statistics (components, pins, role distribution)."""
    return json.dumps(kg().stats(), indent=2)


@mcp.tool()
def kg_component(mpn: str) -> str:
    """Look up one component's structure: pins (with roles), rails, required externals."""
    c = kg().get(mpn)
    if not c:
        near = [m for m in kg().components if mpn.upper() in m.upper()]
        return json.dumps({"error": f"'{mpn}' not in KG", "did_you_mean": near[:5]})
    return json.dumps({
        "mpn": c.mpn, "category": c.category, "prefix": c.prefix,
        "power_pins": [(p.name, p.role) for p in c.power_pins()],
        "ground_pins": [p.name for p in c.ground_pins()],
        "rails": list(c.rails.keys()),
        "required_externals": [{"purpose": e.purpose, "value": e.value,
                                "between": e.connect_between} for e in c.externals],
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
