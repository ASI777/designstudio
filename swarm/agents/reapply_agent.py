"""Re-apply the saved design onto an existing project using the CURRENT footprint
generators — so footprint/board improvements (IC IPC patterns, connector library,
board sizing) reflect WITHOUT re-running datasheet extraction or netlisting.

Reads the design from the project's <name>.harness-result.json sidecar
(components + netlist), regenerates every footprint with today's generators, and
clears stale routing so the board can be re-routed (which applies router fixes
like the via-in-pad keepout).

Usage:  python3 reapply_agent.py <project.dsproj>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from swarm.memory.apply_to_board import apply


def main(project: str) -> int:
    p = Path(project)
    sidecar = p.with_suffix(".harness-result.json")
    if not sidecar.exists():
        print("No saved design (.harness-result.json) found for this project — "
              "the footprints can only be regenerated from a harness build. "
              "Run /build to (re)create the design.", flush=True)
        return 1
    try:
        d = json.loads(sidecar.read_text())
    except Exception as e:
        print(f"Could not read saved design: {e}", flush=True)
        return 1

    comps = d.get("components", [])
    net = d.get("netlist", [])
    if not comps:
        print("Saved design has no components — nothing to re-apply.", flush=True)
        return 1

    # Preserve the existing board's layer count.
    layers = None
    try:
        layers = json.loads(p.read_text()).get("copper_layers")
    except Exception:
        pass

    res = apply(project, comps, net, layers=layers)
    print(f"Re-applied with the current footprint library: "
          f"{res['footprints']} footprints, {res['pads']} pads, {res['nets']} nets, "
          f"{layers or res.get('layers','?')} layers, "
          f"board {res['board_mm'][0]}×{res['board_mm'][1]} mm.", flush=True)
    print("Footprints regenerated (IC IPC / connector / passive) and routing "
          "cleared — re-routing now applies the via-in-pad keepout.", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: reapply_agent.py <project.dsproj>", flush=True)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
