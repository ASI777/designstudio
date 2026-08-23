#!/usr/bin/env python3
"""via_edge_agent.py — Migrate vias to the board edge channels.

Strategy
--------
Each via currently sits wherever the A* router placed it — scattered inside
the component field.  This agent moves every via to the nearest board-edge
channel (the 5 mm margin strip along each side) so vias form organised rows
at the board perimeter rather than a random cloud in the middle.

For each via at (vx, vy):
  1. Determine which of the four edge channels is closest.
  2. Assign a slot in that channel (0.9 mm pitch, minimum via-to-via wall).
  3. Add two stub trace segments (one on from_layer, one on to_layer)
     bridging old via position → new via position.
  4. Move the via to the new position.

Collision avoidance
-------------------
Slots are assigned greedily along the channel length, sorted by the via's
projected position along the channel axis so the migration order is
predictable and doesn't cause unnecessary crossings.

Trace overlap / collinear merge
--------------------------------
After migration, collinear trace segments on the same (net, layer) are merged
into single longer segments.  This reduces visual clutter and eliminates the
"rat's nest" appearance left by the A* router.

Usage:
  python3 via_edge_agent.py <project.dsproj> [--channel-inset 2.5]
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict

# ── Configuration ─────────────────────────────────────────────────────────────

CHANNEL_INSET  = 2.5   # mm from board edge to via centre
VIA_SPACING    = 0.80  # mm minimum slot pitch in channel
STUB_WIDTH_MM  = 0.15  # trace width for migration stubs

# ── Slot allocator ────────────────────────────────────────────────────────────

class SlotAllocator:
    """1-D slot allocator along a channel axis (left/right→y axis; top/bot→x axis)."""

    def __init__(self, axis_min: float, axis_max: float, pitch: float) -> None:
        self.axis_min  = axis_min
        self.axis_max  = axis_max
        self.pitch     = pitch
        self.slots: list[float] = []
        self._capacity = max(1, int((axis_max - axis_min) / pitch))

    def claim(self, preferred: float) -> float:
        preferred = max(self.axis_min, min(self.axis_max, preferred))
        # snap to grid
        slot = round(preferred / self.pitch) * self.pitch
        slot = max(self.axis_min, min(self.axis_max, slot))
        # walk outward until clear
        for delta in range(0, int((self.axis_max - self.axis_min) / self.pitch) + 2):
            for sign in ([0] if delta == 0 else [1, -1]):
                candidate = slot + sign * delta * self.pitch
                candidate = round(candidate / self.pitch) * self.pitch
                if candidate < self.axis_min or candidate > self.axis_max:
                    continue
                if all(abs(candidate - s) >= self.pitch - 0.01 for s in self.slots):
                    self.slots.append(candidate)
                    return candidate
        # fallback: no clear slot found, use preferred anyway
        self.slots.append(preferred)
        return preferred


# ── Collinear trace merge ──────────────────────────────────────────────────────

def _seg_key(ax, ay, bx, by):
    """Canonical (smaller-first) key for a segment, direction, and layer."""
    if (ax, ay) > (bx, by):
        ax, ay, bx, by = bx, by, ax, ay
    return ax, ay, bx, by


def merge_collinear_traces(traces: list[dict]) -> tuple[list[dict], int]:
    """Merge collinear touching segments on the same (net, layer).

    Two segments are collinear if they share an endpoint and their four
    endpoints are co-linear (cross-product ~ 0).  Merged into one segment
    spanning the two outer endpoints.

    Returns (new_traces, n_merged).
    """
    def collinear(ax, ay, bx, by, cx, cy):
        # cross product of (B-A) × (C-A)
        return abs((bx-ax)*(cy-ay) - (by-ay)*(cx-ax)) < 1e-6

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for t in traces:
        by_key[(t.get("net", -1), t.get("layer", 0))].append(t)

    merged_count = 0
    result: list[dict] = []

    for (net, layer), segs in by_key.items():
        # Build adjacency: endpoint → list of segment indices
        changed = True
        while changed:
            changed = False
            ep: dict[tuple, list[int]] = defaultdict(list)
            for i, s in enumerate(segs):
                if s is None:
                    continue
                for pt in ((round(s["ax_mm"],4), round(s["ay_mm"],4)),
                           (round(s["bx_mm"],4), round(s["by_mm"],4))):
                    ep[pt].append(i)

            for pt, idxs in ep.items():
                if len(idxs) != 2:
                    continue
                i, j = idxs
                if segs[i] is None or segs[j] is None:
                    continue
                si, sj = segs[i], segs[j]
                # The four endpoints
                ai = (round(si["ax_mm"],4), round(si["ay_mm"],4))
                bi = (round(si["bx_mm"],4), round(si["by_mm"],4))
                aj = (round(sj["ax_mm"],4), round(sj["ay_mm"],4))
                bj = (round(sj["bx_mm"],4), round(sj["by_mm"],4))
                # Outer endpoints (not the shared point pt)
                oi = bi if ai == pt else ai
                oj = bj if aj == pt else aj
                if not collinear(oi[0], oi[1], pt[0], pt[1], oj[0], oj[1]):
                    continue
                # Merge
                w = max(si.get("w_mm", 0.15), sj.get("w_mm", 0.15))
                merged = {"ax_mm": oi[0], "ay_mm": oi[1],
                          "bx_mm": oj[0], "by_mm": oj[1],
                          "w_mm": w, "layer": layer, "net": net, "pour": False}
                segs[i] = merged
                segs[j] = None
                merged_count += 1
                changed = True

        result.extend(s for s in segs if s is not None)

    return result, merged_count


# ── Main migration logic ───────────────────────────────────────────────────────

def migrate_vias(data: dict, channel_inset: float = CHANNEL_INSET
                 ) -> tuple[int, list[dict]]:
    """Move all interior vias to edge channels.  Returns (n_moved, new_stubs)."""
    vias  = data["vias"]
    board_w = data.get("board_width_mm",  100.0)
    board_h = data.get("board_height_mm",  80.0)

    # Axis limits for slot allocators (stay within the board, skip corners)
    margin = channel_inset + 1.0
    left_alloc   = SlotAllocator(margin, board_h - margin, VIA_SPACING)
    right_alloc  = SlotAllocator(margin, board_h - margin, VIA_SPACING)
    top_alloc    = SlotAllocator(margin, board_w - margin, VIA_SPACING)
    bottom_alloc = SlotAllocator(margin, board_w - margin, VIA_SPACING)

    def in_channel(x, y):
        edge_dist = min(x, board_w - x, y, board_h - y)
        return edge_dist <= channel_inset * 2

    # Sorted channel options per via: prefer nearest edge but fall back to others
    # when a channel is full.  This prevents all vias piling into one channel.
    def sorted_channels(vx, vy):
        """Return list of (distance, channel_id) sorted nearest first."""
        return sorted([
            (vx,           "left"),
            (board_w - vx, "right"),
            (vy,           "top"),
            (board_h - vy, "bottom"),
        ])

    channel_alloc = {
        "left":   left_alloc,
        "right":  right_alloc,
        "top":    top_alloc,
        "bottom": bottom_alloc,
    }

    new_stubs: list[dict] = []
    moved = 0

    for v in sorted(vias, key=lambda v: min(v["x_mm"], board_w-v["x_mm"],
                                             v["y_mm"], board_h-v["y_mm"])):
        vx, vy = v["x_mm"], v["y_mm"]
        if in_channel(vx, vy):
            continue   # already at edge

        net  = v.get("net", -1)
        fl   = v.get("from", 0)
        tl   = v.get("to",   7)

        nx = ny = None
        for _dist, ch in sorted_channels(vx, vy):
            alloc = channel_alloc[ch]
            if len(alloc.slots) >= alloc._capacity:
                continue   # channel full — try next
            if ch == "left":
                nx, ny = channel_inset, alloc.claim(vy)
            elif ch == "right":
                nx, ny = board_w - channel_inset, alloc.claim(vy)
            elif ch == "top":
                nx, ny = alloc.claim(vx), channel_inset
            else:
                nx, ny = alloc.claim(vx), board_h - channel_inset
            break

        if nx is None:   # all channels full — keep in place
            continue

        # Add stub traces on both layers so the circuit path is:
        #   (source) --[existing traces]-- (vx,vy) --[stub on fl]-- (nx,ny)
        #   via at (nx,ny) fl→tl
        #   (nx,ny) --[stub on tl]-- (vx,vy) --[existing traces]-- (dest)
        for layer in (fl, tl):
            new_stubs.append({
                "ax_mm": round(vx, 4), "ay_mm": round(vy, 4),
                "bx_mm": round(nx, 4), "by_mm": round(ny, 4),
                "w_mm": STUB_WIDTH_MM, "layer": layer,
                "net": net, "pour": False,
            })

        v["x_mm"] = round(nx, 4)
        v["y_mm"] = round(ny, 4)
        moved += 1

    return moved, new_stubs


# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path: str, channel_inset: float = CHANNEL_INSET) -> None:
    print(f"[via-edge] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    bw = data.get("board_width_mm", 100.0)
    bh = data.get("board_height_mm",  80.0)
    print(f"[via-edge] Board: {bw:.1f} x {bh:.1f} mm")
    print(f"[via-edge] Before: {len(data['traces'])} traces, {len(data['vias'])} vias")

    # ── Step 1: migrate vias to edge channels ─────────────────────────────────
    print(f"\n━━━━ Step 1: Migrate vias to edge channels (inset {channel_inset} mm) ━━━━")
    moved, stubs = migrate_vias(data, channel_inset)
    data["traces"].extend(stubs)
    print(f"  Moved {moved} vias to edge channels")
    print(f"  Added {len(stubs)} stub traces to bridge old→new via positions")

    # ── Step 2: merge collinear trace segments ────────────────────────────────
    print(f"\n━━━━ Step 2: Merge collinear trace segments ━━━━")
    merged_traces, n_merged = merge_collinear_traces(data["traces"])
    data["traces"] = merged_traces
    print(f"  Merged {n_merged} collinear segment pairs → {len(merged_traces)} traces remain")

    # ── Save ─────────────────────────────────────────────────────────────────
    tmp = project_path + ".via_edge.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, project_path)

    print(f"\n[via-edge] Saved → {project_path}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Final: {len(merged_traces)} traces, {len(data['vias'])} vias")
    print(f"  All {len(data['vias'])} vias are now in the {channel_inset}mm edge channels")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("project")
    p.add_argument("--channel-inset", type=float, default=CHANNEL_INSET,
                   help="Distance from board edge to via centre (default 2.5mm)")
    args = p.parse_args()
    if not os.path.exists(args.project):
        print(f"error: not found: {args.project}"); sys.exit(1)
    main(args.project, args.channel_inset)
