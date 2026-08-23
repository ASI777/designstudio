#!/usr/bin/env python3
"""Layout-optimizer agent.

Reads a .dsproj, repositions footprints with a force-directed pass that shortens
the *signal* ratsnest (high-fanout power/ground nets are ignored so everything
doesn't collapse onto GND), avoids component overlap, keeps parts on-board, and
pins the largest component as an anchor. Writes the .dsproj back only if the
total connection length improves — the app's live-reload watcher then refreshes
the canvas.

Usage: layout_optimizer_agent.py <project.dsproj>
"""
from __future__ import annotations
import json
import math
import sys

POWER_NAMES = ("GND", "VDD", "VSS", "VCC", "VEE")
MAX_FANOUT = 6      # nets touching more than this many parts are treated as power
ITERS = 400
STEP = 0.15         # mm per iteration nudge


def is_power(name: str, degree: int) -> bool:
    n = (name or "").upper()
    if any(n == p or n.startswith(p) for p in POWER_NAMES):
        return True
    return degree > MAX_FANOUT


def footprint_radius(fp) -> float:
    """Approx keep-out radius from the pad extent (mm)."""
    xs, ys = [], []
    for p in fp.get("pads", []):
        xs.append(abs(p.get("x_mm", 0)) + p.get("w_mm", 0.6) / 2)
        ys.append(abs(p.get("y_mm", 0)) + p.get("h_mm", 0.25) / 2)
    if not xs:
        return 1.0
    return max(0.5, math.hypot(max(xs), max(ys)))


def net_to_components(d):
    """net id -> set of footprint indices that have a pad on it."""
    nets = {}
    for i, fp in enumerate(d["footprints"]):
        for p in fp.get("pads", []):
            nid = p.get("net", -1)
            if nid is not None and nid >= 0:
                nets.setdefault(nid, set()).add(i)
    return nets


def ratsnest_length(d, signal_nets):
    """Sum, over signal nets, of each part's distance to the net's centroid."""
    total = 0.0
    for nid, members in signal_nets.items():
        pts = [(d["footprints"][i]["x_mm"], d["footprints"][i]["y_mm"]) for i in members]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        total += sum(math.hypot(x - cx, y - cy) for x, y in pts)
    return total


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("error: no project path given"); return 1
    path = sys.argv[1].strip()
    d = json.load(open(path))
    fps = d["footprints"]
    n = len(fps)
    if n < 3:
        print("Nothing to optimize (need at least 3 components)."); return 0

    id2name = {t["id"]: t["name"] for t in d.get("net_table", [])}
    nets = net_to_components(d)
    signal_nets = {nid: m for nid, m in nets.items()
                   if len(m) >= 2 and not is_power(id2name.get(nid, ""), len(m))}
    print(f"{n} components, {len(signal_nets)} signal nets considered "
          f"(of {len(nets)} total).")

    if not signal_nets:
        print("No signal nets to optimize — only power/ground connectivity present.")
        return 0

    W, H = d["board_width_mm"], d["board_height_mm"]
    radius = [footprint_radius(fp) for fp in fps]
    # Anchor = the component with the most pads (the big FPGA/BGA stays put).
    anchor = max(range(n), key=lambda i: len(fps[i].get("pads", [])))
    pos = [[fp["x_mm"], fp["y_mm"]] for fp in fps]

    before = ratsnest_length(d, signal_nets)

    for _ in range(ITERS):
        force = [[0.0, 0.0] for _ in range(n)]
        # attraction along signal nets (toward net centroid)
        for nid, members in signal_nets.items():
            cx = sum(pos[i][0] for i in members) / len(members)
            cy = sum(pos[i][1] for i in members) / len(members)
            for i in members:
                force[i][0] += (cx - pos[i][0]) * 0.10
                force[i][1] += (cy - pos[i][1]) * 0.10
        # repulsion when keep-outs overlap
        for i in range(n):
            for j in range(i + 1, n):
                dx = pos[i][0] - pos[j][0]
                dy = pos[i][1] - pos[j][1]
                dist = math.hypot(dx, dy) or 1e-3
                mind = radius[i] + radius[j] + 0.5
                if dist < mind:
                    push = (mind - dist)
                    ux, uy = dx / dist, dy / dist
                    force[i][0] += ux * push; force[i][1] += uy * push
                    force[j][0] -= ux * push; force[j][1] -= uy * push
        # integrate
        for i in range(n):
            if i == anchor:
                continue
            pos[i][0] += max(-STEP, min(STEP, force[i][0] * STEP))
            pos[i][1] += max(-STEP, min(STEP, force[i][1] * STEP))
            pos[i][0] = max(radius[i], min(W - radius[i], pos[i][0]))
            pos[i][1] = max(radius[i], min(H - radius[i], pos[i][1]))

    for i, fp in enumerate(fps):
        fp["x_mm"] = round(pos[i][0], 4)
        fp["y_mm"] = round(pos[i][1], 4)
    after = ratsnest_length(d, signal_nets)

    if after >= before - 1e-6:
        print(f"No improvement (before {before:.1f} mm, after {after:.1f} mm) — "
              "leaving placement unchanged.")
        return 0

    json.dump(d, open(path, "w"), separators=(",", ":"))
    pct = 100.0 * (before - after) / before
    print(f"Optimized placement: ratsnest {before:.1f} → {after:.1f} mm "
          f"({pct:.1f}% shorter). Anchored {fps[anchor]['ref']}. Saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
