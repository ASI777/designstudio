#!/usr/bin/env python3
"""board_fit_agent.py — Board compaction + courtyard trace cleanup.

Fix 1 — Courtyard cleanup
  Removes every trace segment whose midpoint falls inside a component
  courtyard (pad bounding-box + 0.25 mm) on a net NOT belonging to that
  component.  Also removes vias under components on different nets.
  These are routes that the A* router threaded between pads of a package
  it should not enter.

Fix 2 — Board auto-fit
  Computes the bounding box of every pad in world coordinates, adds a
  configurable margin (default 5 mm), and:
    • Sets board_width_mm  = span_x + 2*margin
    • Sets board_height_mm = span_y + 2*margin
    • Shifts all footprint, trace, and via coordinates so the leftmost /
      topmost pad lands at (margin, margin).

Usage:
  python3 board_fit_agent.py <project.dsproj> [--margin 5.0]
"""
from __future__ import annotations
import json, math, os, sys
from pathlib import Path

NM_PER_MM = 1_000_000


# ── Geometry helpers ──────────────────────────────────────────────────────────

def pad_world_xy(fp: dict, p: dict) -> tuple[float, float]:
    rad = math.radians(fp.get("rot_deg", 0))
    px, py = p.get("x_mm", 0), p.get("y_mm", 0)
    wx = fp["x_mm"] + px * math.cos(rad) - py * math.sin(rad)
    wy = fp["y_mm"] + px * math.sin(rad) + py * math.cos(rad)
    return wx, wy


def build_courtyards(fps: list[dict], margin: float = 0.25
                     ) -> list[tuple[float, float, float, float, set[int]]]:
    """Return [(x0, y0, x1, y1, net_set), ...] for every footprint."""
    result = []
    for fp in fps:
        pads = fp.get("pads", [])
        if not pads:
            continue
        xs, ys, nets = [], [], set()
        for p in pads:
            wx, wy = pad_world_xy(fp, p)
            hw = p.get("w_mm", 0) / 2
            hh = p.get("h_mm", 0) / 2
            xs += [wx - hw, wx + hw]
            ys += [wy - hh, wy + hh]
            if p.get("net", -1) >= 0:
                nets.add(p["net"])
        result.append((min(xs) - margin, min(ys) - margin,
                        max(xs) + margin, max(ys) + margin,
                        nets))
    return result


# ── Fix 1: remove traces / vias that cross component courtyards ───────────────

def remove_courtyard_violations(
    traces: list[dict],
    vias:   list[dict],
    courtyards: list[tuple],
) -> tuple[list[dict], list[dict], int, int]:
    """Filter out routes whose geometric centre is inside a courtyard on a
    net that does not belong to that component."""

    def inside_foreign_courtyard(x: float, y: float, net: int) -> bool:
        for x0, y0, x1, y1, nets in courtyards:
            if x0 <= x <= x1 and y0 <= y <= y1 and net not in nets:
                return True
        return False

    clean_traces: list[dict] = []
    bad_tr = 0
    for t in traces:
        mx = (t.get("ax_mm", 0) + t.get("bx_mm", 0)) / 2
        my = (t.get("ay_mm", 0) + t.get("by_mm", 0)) / 2
        if inside_foreign_courtyard(mx, my, t.get("net", -1)):
            bad_tr += 1
        else:
            clean_traces.append(t)

    clean_vias: list[dict] = []
    bad_v = 0
    for v in vias:
        if inside_foreign_courtyard(v.get("x_mm", 0), v.get("y_mm", 0),
                                    v.get("net", -1)):
            bad_v += 1
        else:
            clean_vias.append(v)

    return clean_traces, clean_vias, bad_tr, bad_v


# ── Fix 2: board auto-fit ─────────────────────────────────────────────────────

def fit_board(data: dict, margin: float = 5.0) -> tuple[float, float, float, float]:
    """Compute all-pad bounding box, resize the board, and shift everything.

    Returns (old_w, old_h, new_w, new_h).
    """
    fps = data["footprints"]

    # Gather all pad world positions
    all_xs: list[float] = []
    all_ys: list[float] = []
    for fp in fps:
        for p in fp.get("pads", []):
            wx, wy = pad_world_xy(fp, p)
            hw = p.get("w_mm", 0) / 2
            hh = p.get("h_mm", 0) / 2
            all_xs += [wx - hw, wx + hw]
            all_ys += [wy - hh, wy + hh]

    if not all_xs:
        return (data.get("board_width_mm", 100),
                data.get("board_height_mm", 80), 0, 0)

    x_min, x_max = min(all_xs), max(all_xs)
    y_min, y_max = min(all_ys), max(all_ys)

    new_w = round(x_max - x_min + 2 * margin, 2)
    new_h = round(y_max - y_min + 2 * margin, 2)
    old_w = data.get("board_width_mm", 100.0)
    old_h = data.get("board_height_mm", 80.0)

    # Shift so leftmost/topmost pad edge lands at x=margin, y=margin
    dx = margin - x_min
    dy = margin - y_min

    # Apply shift to footprints
    for fp in fps:
        fp["x_mm"] = round(fp["x_mm"] + dx, 4)
        fp["y_mm"] = round(fp["y_mm"] + dy, 4)

    # Apply shift to traces
    for t in data.get("traces", []):
        t["ax_mm"] = round(t["ax_mm"] + dx, 4)
        t["ay_mm"] = round(t["ay_mm"] + dy, 4)
        t["bx_mm"] = round(t["bx_mm"] + dx, 4)
        t["by_mm"] = round(t["by_mm"] + dy, 4)

    # Apply shift to vias
    for v in data.get("vias", []):
        v["x_mm"] = round(v["x_mm"] + dx, 4)
        v["y_mm"] = round(v["y_mm"] + dy, 4)

    data["board_width_mm"]  = new_w
    data["board_height_mm"] = new_h

    return old_w, old_h, new_w, new_h


# ── Post-fit sanity check ─────────────────────────────────────────────────────

def check_placement(data: dict) -> None:
    fps = data["footprints"]
    bw  = data.get("board_width_mm", 100)
    bh  = data.get("board_height_mm", 80)
    off = [f["ref"] for f in fps
           if not (0 <= f["x_mm"] <= bw and 0 <= f["y_mm"] <= bh)]
    if off:
        print(f"  WARNING: {len(off)} components outside new board bounds: {off[:5]}")
    else:
        print(f"  All {len(fps)} components inside {bw:.1f}x{bh:.1f} mm board")

    # Re-check courtyard violations after shift
    courts = build_courtyards(fps)
    violations = 0
    for t in data.get("traces", []):
        mx = (t["ax_mm"] + t["bx_mm"]) / 2
        my = (t["ay_mm"] + t["by_mm"]) / 2
        net = t.get("net", -1)
        for x0, y0, x1, y1, nets in courts:
            if x0 <= mx <= x1 and y0 <= my <= y1 and net not in nets:
                violations += 1
                break
    print(f"  Remaining courtyard trace violations: {violations}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path: str, margin: float = 5.0) -> None:
    print(f"[board-fit] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps    = data["footprints"]
    traces = data.get("traces", [])
    vias   = data.get("vias", [])

    print(f"[board-fit] {len(fps)} footprints, {len(traces)} traces, {len(vias)} vias")
    print(f"[board-fit] Current board: "
          f"{data.get('board_width_mm',100):.1f} x {data.get('board_height_mm',80):.1f} mm")

    # ── Fix 1: courtyard cleanup ──────────────────────────────────────────────
    print("\n━━━━ Fix 1: Remove routes inside component courtyards ━━━━")
    courts = build_courtyards(fps, margin=0.25)
    clean_tr, clean_v, bad_tr, bad_v = remove_courtyard_violations(
        traces, vias, courts)
    data["traces"] = clean_tr
    data["vias"]   = clean_v
    print(f"  Removed {bad_tr} traces and {bad_v} vias that crossed foreign courtyards")
    print(f"  Remaining: {len(clean_tr)} traces, {len(clean_v)} vias")

    # ── Fix 2: board auto-fit ─────────────────────────────────────────────────
    print(f"\n━━━━ Fix 2: Auto-fit board (margin = {margin} mm) ━━━━")
    old_w, old_h, new_w, new_h = fit_board(data, margin)
    w_save = old_w - new_w
    h_save = old_h - new_h
    print(f"  Old: {old_w:.1f} x {old_h:.1f} mm")
    print(f"  New: {new_w:.1f} x {new_h:.1f} mm  "
          f"(saved {w_save:.1f} mm width, {h_save:.1f} mm height)")

    # ── Sanity check ──────────────────────────────────────────────────────────
    print("\n━━━━ Post-fit sanity check ━━━━")
    check_placement(data)

    # ── Save ─────────────────────────────────────────────────────────────────
    tmp = project_path + ".fit.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, project_path)
    print(f"\n[board-fit] Saved → {project_path}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  board_fit_agent complete")
    print(f"  Board: {old_w:.0f}x{old_h:.0f}mm → {new_w:.0f}x{new_h:.0f}mm "
          f"({w_save:.0f}mm shorter)")
    print("  Next step: Re-route to reconnect any segments that were removed")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("project")
    p.add_argument("--margin", type=float, default=5.0,
                   help="Board edge margin in mm (default 5.0)")
    args = p.parse_args()
    if not os.path.exists(args.project):
        print(f"error: not found: {args.project}"); sys.exit(1)
    main(args.project, args.margin)
