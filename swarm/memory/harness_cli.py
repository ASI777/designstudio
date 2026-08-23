#!/usr/bin/env python3
"""CLI bridge — the Qt app drives the design harness via QProcess.

The app spawns:
    python3 swarm/memory/harness_cli.py "<intent>" [--project <path>]
and streams stdout into the chat / activity panel. The harness prints readable
"[harness] …" progress lines as it runs; this wrapper adds a final summary and
writes a result sidecar next to the project so the design is inspectable.

Also supports recording a real-world outcome (called when the user exports/
accepts a design) so the memory learns what shipped:
    python3 swarm/memory/harness_cli.py --outcome <session_id> <accepted|exported|manufactured|rejected>

Run as a plain script (not -m): it adds the repo root to sys.path itself, so the
app's existing runAgent() path works unchanged.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Make `swarm` importable when launched as a bare script from the repo root.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swarm.memory.providers import make_harness, mem


def _build(intent: str, project: str | None) -> int:
    print(f"━━━━ Design harness — building: {intent[:70]} ━━━━", flush=True)
    h = make_harness()
    # The CLI is only ever invoked to BUILD (the app's /build action), so force a
    # build — a prompt like "a 4S Li-ion BMS…" has no build verb and would
    # otherwise misclassify as a question.
    s = h.run(intent, force_class="build")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    print(f"  Status:      {s.status}", flush=True)
    print(f"  Intent:      {s.intent_class}", flush=True)
    print(f"  Components:  {len(s.components)}"
          + ("  (" + ", ".join(c.get('mpn', '?') for c in s.components[:6]) + ")"
             if s.components else ""), flush=True)
    print(f"  Connections: {len(s.netlist)}", flush=True)
    print(f"  Refines:     {s.refine_count}    LLM calls: {s.llm_calls}", flush=True)
    print(f"  Reused:      {s.reused_netlist}    Quality: {s.quality:.2f}", flush=True)
    print(f"  Session:     {s.session_id}", flush=True)
    if getattr(s, "electrical_analysis", None):
        print(f"  Electrical:  {s.electrical_analysis.get('status', 'incomplete')}", flush=True)
        for name, category in s.electrical_analysis.get("categories", {}).items():
            print(f"      {name}: {category.get('status', 'incomplete')}", flush=True)
    if getattr(s, "unrealized", None):
        print(f"  ⚠ COULD NOT SOURCE {len(s.unrealized)} part(s) — design is PARTIAL:",
              flush=True)
        for u in s.unrealized:
            print(f"      • {u['mpn']}  ({u['reason']} on DigiKey or Mouser, "
                  f"no substitute, no package info)", flush=True)
        print(f"    → these parts must be selected manually; the rest of the "
              f"design was built.", flush=True)
    if getattr(s, "footprint_warnings", None):
        print(f"  ⚠ FOOTPRINT EXTRACTION — {len(s.footprint_warnings)} part(s) have "
              f"automatic extraction warnings:", flush=True)
        for w in s.footprint_warnings:
            why = w["issues"][0] if w["issues"] else w["confidence"]
            print(f"      • {w['mpn']}  [{w['confidence']}] — {why}", flush=True)
    if s.status in ("failed", "partial") and s.last_feedback:
        print(f"  Issues:\n    " + s.last_feedback.replace("\n", "\n    "), flush=True)
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)

    # Write a result sidecar next to the project for inspection.
    if project:
        sidecar = Path(project).with_suffix(".harness-result.json")
        try:
            sidecar.write_text(json.dumps({
                "session_id": s.session_id, "status": s.status,
                "quality": s.quality, "components": s.components,
                "netlist": s.netlist, "intent": intent,
                "unrealized": getattr(s, "unrealized", []),
                "footprint_warnings": getattr(s, "footprint_warnings", []),
                "electrical_analysis": getattr(s, "electrical_analysis", {}),
            }, indent=2))
            print(f"  Result written to {sidecar.name}", flush=True)
        except Exception as e:
            print(f"  (could not write result sidecar: {e})", flush=True)

    # Apply-to-board: instantiate footprints + nets onto the project so the canvas
    # shows the design. Only for a usable result with at least one connection.
    if project and s.netlist and s.status in ("complete", "partial"):
        try:
            from swarm.memory.apply_to_board import apply, detect_layers
            layers = detect_layers(intent)          # "4-layer board" → 4
            res = apply(project, s.components, s.netlist, layers=layers,
                        incremental_analysis=getattr(s, "electrical_analysis", {}))
            print(f"  Applied to board: {res['footprints']} footprints, "
                  f"{res['pads']} pads, {res['nets']} nets, "
                  f"{layers} layers, "
                  f"board {res['board_mm'][0]}×{res['board_mm'][1]} mm"
                  + ("  (existing project backed up to .bak)" if res["backed_up"] else ""),
                  flush=True)
            if res.get("unresolved_components"):
                print(f"  ⚠ {len(res['unresolved_components'])} component(s) were not "
                      "placed because their cross-domain binding is incomplete:", flush=True)
                for unresolved in res["unresolved_components"]:
                    print(f"      • {unresolved.get('ref', '?')} "
                          f"{unresolved.get('mpn', '?')}: {unresolved.get('reason', '')}",
                          flush=True)
            from swarm.memory.incremental_design import bind_analysis_to_project
            analysis_path = bind_analysis_to_project(
                getattr(s, "electrical_analysis", {}), project)
            print(f"  Electrical analysis bound to project: {analysis_path.name}", flush=True)
            print("  → open/refresh the project to see it; then run "
                  "/subsystem and /route", flush=True)
        except Exception as e:
            print(f"  (apply-to-board failed: {e})", flush=True)

    # Machine-readable last line for the app to parse if it wants to.
    print("RESULT: " + json.dumps({
        "session_id": s.session_id, "status": s.status,
        "components": len(s.components), "connections": len(s.netlist),
        "quality": round(s.quality, 3),
    }), flush=True)
    return 0 if s.status in ("complete", "modify", "ask") else 1


def _outcome(session_id: str, outcome: str) -> int:
    note = mem().find_by_session(session_id)
    if not note:
        print(f"No design note for session '{session_id}'", flush=True)
        return 1
    before = note.quality
    if not mem().record_outcome(note.id, outcome):
        print(f"Invalid outcome '{outcome}' "
              "(accepted|exported|manufactured|rejected)", flush=True)
        return 1
    print(f"Recorded '{outcome}': quality {before:.2f} → {note.quality:.2f} "
          f"(memory now prefers/avoids this design accordingly)", flush=True)
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if a]
    if "--forget" in args:                       # reset the design memory
        n = mem().forget_all()
        print(f"Design memory cleared — removed {n} cached designs. "
              f"Future builds start fresh.", flush=True)
        return 0
    if "--prune" in args:                         # drop only bad/partial cached designs
        n = mem().prune_bad()
        print(f"Pruned {n} partial/failed/low-quality designs from memory.", flush=True)
        return 0
    if "--outcome" in args:
        i = args.index("--outcome")
        if i + 2 >= len(args):
            print("usage: --outcome <session_id> <outcome>", flush=True); return 1
        return _outcome(args[i + 1], args[i + 2])

    project = None
    if "--project" in args:
        i = args.index("--project")
        project = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    intent = " ".join(args).strip()
    if not intent:
        print("usage: harness_cli.py \"<intent>\" [--project <path>]", flush=True)
        return 1
    return _build(intent, project)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
