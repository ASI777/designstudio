#!/usr/bin/env python3
"""subsystem_placer_agent.py — importance-weighted, subsystem-aware PCB placement.

Implements the "net importance → sub-systems → systems" flow:

  1. Build a weighted component graph. Edge weight between two components =
     sum over shared nets of (net_importance / net_fanout).  Power/GND nets are
     down-weighted (they touch everything) and normalised by fanout so a 40-pad
     GND net does not glue the whole board into one blob.  High-speed nets
     (DDR/USB/diff/clock) are up-weighted so tightly-coupled blocks stay together.

  2. Detect sub-systems by weighted label propagation (community detection,
     stdlib only — no networkx).  Each component adopts the weighted-majority
     label of its neighbours until convergence.

  3. Floor-plan: place each sub-system as a tight internal grid (island), then
     arrange the islands left-to-right ordered by inter-cluster coupling so the
     most strongly-connected blocks sit adjacent (signal flows across the board).

  4. Write component x_mm / y_mm back to the .dsproj and resize the board to fit.

Usage:  python3 subsystem_placer_agent.py <project.dsproj> [--draft|--optimized]

The draft mode deliberately skips substrate legalization. It is useful for the
first placement pass, where the optimizer is allowed to discover the layout
before mechanical limits are applied. The default optimized mode retains the
fail-closed placement gate used before routing and release checks.
"""
from __future__ import annotations
import json
import math
import os
import sys
from collections import defaultdict

from placement_constraints import apply_constraints

# ── Net importance weights ────────────────────────────────────────────────────
W_POWER     = 0.15   # GND / power rails — they connect everything; keep weak
W_HIGHSPEED = 1.00   # DDR / USB / diff pairs / clocks — keep these blocks tight
W_SIGNAL    = 0.50   # ordinary signals

HIGHSPEED_HINTS = ("DDR", "DQ", "DQS", "USB", "DP", "DM", "CLK", "CK_", "_CK",
                   "LVDS", "TX", "RX", "TD_", "RD_", "PCIE", "MIPI", "HDMI",
                   "SERDES", "XCVR", "REFCLK")
POWER_HINTS     = ("GND", "VSS", "VCC", "VDD", "VBUS", "VIN", "VBAT", "3V3",
                   "5V", "1V8", "1V2", "2V5", "VPP", "VDDQ", "VREF", "IN_12V",
                   "V1P8", "VM", "AVDD", "DVDD", "PGND", "AGND")


def net_weight(name: str) -> float:
    u = (name or "").upper()
    if any(h in u for h in POWER_HINTS):
        return W_POWER
    if any(h in u for h in HIGHSPEED_HINTS):
        return W_HIGHSPEED
    return W_SIGNAL


# ── Graph construction ─────────────────────────────────────────────────────────
def build_graph(fps: list[dict], nets: dict[int, str]):
    """Return (adj, comp_refs).  adj[a][b] = accumulated edge weight."""
    # net id -> set of component indices touching it
    net_comps: dict[int, set[int]] = defaultdict(set)
    for ci, fp in enumerate(fps):
        for pad in fp.get("pads", []):
            nid = pad.get("net", -1)
            if nid >= 0:
                net_comps[nid].add(ci)

    adj: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for nid, comps in net_comps.items():
        comps = list(comps)
        fanout = len(comps)
        if fanout < 2:
            continue
        w = net_weight(nets.get(nid, "")) / fanout    # normalise by fanout
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a, b = comps[i], comps[j]
                adj[a][b] += w
                adj[b][a] += w
    return adj


def prune_weak_edges(adj, keep_frac: float = 0.35):
    """Keep, per node, only edges within keep_frac of that node's strongest edge.
    This removes the weak fanout-normalised power-rail 'glue' that otherwise
    merges every block into one giant community, exposing the real sub-systems."""
    pruned: dict[int, dict[int, float]] = defaultdict(dict)
    for v, nbrs in adj.items():
        if not nbrs:
            continue
        wmax = max(nbrs.values())
        thresh = wmax * keep_frac
        for u, w in nbrs.items():
            if w >= thresh:
                pruned[v][u] = w
    # symmetrise — keep an edge if either endpoint kept it
    sym: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for v in pruned:
        for u, w in pruned[v].items():
            sym[v][u] = w
            sym[u][v] = max(sym[u][v], w)
    return sym


# ── Community detection (weighted label propagation) ──────────────────────────
def detect_subsystems(n: int, adj) -> dict[int, int]:
    label = {i: i for i in range(n)}
    order = list(range(n))
    for _ in range(50):                      # iterate to convergence (capped)
        changed = False
        # deterministic order keeps results reproducible
        for v in order:
            if not adj.get(v):
                continue
            # weighted vote over neighbour labels
            score: dict[int, float] = defaultdict(float)
            for u, w in adj[v].items():
                score[label[u]] += w
            best = max(score.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if label[v] != best:
                label[v] = best
                changed = True
        if not changed:
            break
    return label


def apply_explicit_groups(fps: list[dict], labels: dict[int, int]) -> dict[int, int]:
    """Project-declared functional groups override inferred communities."""
    group_labels: dict[str, int] = {}
    next_label = len(fps)
    for i, fp in enumerate(fps):
        placement = fp.get("placement", {})
        group = str(placement.get("functional_group", "")).strip().casefold()
        if not group:
            continue
        if group not in group_labels:
            group_labels[group] = next_label
            next_label += 1
        labels[i] = group_labels[group]
    return labels


# ── Floor-planning ─────────────────────────────────────────────────────────────
def comp_extent(fp: dict) -> tuple[float, float]:
    """True component extent (mm): the bounding box of its PADS (with sizes) AND
    its silkscreen BODY outline, plus a small courtyard. Using pad CENTERS only
    (or ignoring the body) lets large-body parts overlap their neighbours and
    overflow the board edge."""
    xs, ys = [], []
    courtyard = fp.get("courtyard_pts", [])
    if len(courtyard) >= 3:
        xs.extend(float(point[0]) for point in courtyard)
        ys.extend(float(point[1]) for point in courtyard)
    for pad in fp.get("pads", []):
        pw = pad.get("w_mm", pad.get("width_mm", 0.5))
        ph = pad.get("h_mm", pad.get("height_mm", 0.5))
        px = pad.get("x_mm", 0.0); py = pad.get("y_mm", 0.0)
        xs += [px - pw / 2, px + pw / 2]; ys += [py - ph / 2, py + ph / 2]
    bw = fp.get("body_w_mm", 0.0); bh = fp.get("body_h_mm", 0.0)
    if bw > 0 and bh > 0:
        bcx = fp.get("body_cx_mm", 0.0); bcy = fp.get("body_cy_mm", 0.0)
        xs += [bcx - bw / 2, bcx + bw / 2]; ys += [bcy - bh / 2, bcy + bh / 2]
    if not xs:
        return (3.0, 3.0)
    w = (max(xs) - min(xs)) + 1.0          # courtyard halo
    h = (max(ys) - min(ys)) + 1.0
    return (max(w, 2.0), max(h, 2.0))


# ── Placement spacing (force-directed) ───────────────────────────────────────
# Technique: the spring-electrical / force-directed model (Fruchterman–Reingold
# 1991; the classic analytic-placement family in VLSI, Quinn & Breuer). Nets act
# as springs that PULL connected parts together (minimising wire length); every
# pair of parts REPELS (size-aware) to avoid pile-ups. A separation-constraint
# pass (Dwyer et al.) then removes any residual overlap. Finally each part is
# ROTATED to face its connections, cutting trace turn-arounds. The board is sized
# to the resulting layout's bounding box — no fixed area-inflation factor.
GAP = 0.8          # routing channel between component courtyards (mm)
MARGIN = 4.0       # board edge margin (mm)


def _eff(ext: tuple[float, float], rot: int) -> tuple[float, float]:
    """Extent with rotation applied (w,h swap at 90/270)."""
    return (ext[1], ext[0]) if rot in (90, 270) else ext


def _seed(fps, labels, ext) -> list[list[float]]:
    """Seed positions cluster-by-cluster in a loose grid so connected parts start
    near each other and the force-directed pass converges quickly."""
    clusters: dict[int, list[int]] = defaultdict(list)
    for i, lb in labels.items():
        clusters[lb].append(i)
    pos = [[0.0, 0.0] for _ in range(len(fps))]
    cx = 0.0
    for mem in clusters.values():
        k = max(1, int(math.ceil(math.sqrt(len(mem)))))
        cell = max((max(ext[i]) for i in mem), default=3.0) + 2.0
        for idx, i in enumerate(mem):
            pos[i] = [cx + (idx % k) * cell, (idx // k) * cell]
        cx += k * cell + 4.0
    return pos


def _force_directed(pos, ext, rot, adj, iters=400):
    # Compact variant (avoids the classic FR "explosion" on sparse graphs):
    #   • net springs PULL connected parts together (linear / Hooke),
    #   • repulsion is SHORT-RANGE — it only prevents courtyard overlap, it does
    #     not push the whole graph apart,
    #   • mild gravity toward the centroid keeps disconnected parts and the
    #     overall layout tight.
    n = len(pos)
    avg = sum(max(w, h) for w, h in ext) / max(1, n)
    step = avg + 2.0
    for _ in range(iters):
        cx = sum(p[0] for p in pos) / n
        cy = sum(p[1] for p in pos) / n
        disp = [[0.0, 0.0] for _ in range(n)]
        # short-range repulsion (overlap avoidance only)
        for i in range(n):
            ewi, ehi = _eff(ext[i], rot[i])
            for j in range(i + 1, n):
                ewj, ehj = _eff(ext[j], rot[j])
                dx = pos[i][0] - pos[j][0]; dy = pos[i][1] - pos[j][1]
                d = math.hypot(dx, dy) or 0.01
                minsep = max((ewi + ewj) / 2, (ehi + ehj) / 2) + GAP
                if d < minsep * 1.25:
                    fr = (minsep * 1.25 - d) * 3.0
                    ux, uy = dx / d, dy / d
                    disp[i][0] += ux * fr; disp[i][1] += uy * fr
                    disp[j][0] -= ux * fr; disp[j][1] -= uy * fr
        # attraction — net springs (linear), weighted by net importance
        for i in adj:
            for j, w in adj[i].items():
                if j <= i:
                    continue
                dx = pos[i][0] - pos[j][0]; dy = pos[i][1] - pos[j][1]
                d = math.hypot(dx, dy) or 0.01
                fa = d * 0.18 * (0.5 + 0.5 * min(1.0, w))
                ux, uy = dx / d, dy / d
                disp[i][0] -= ux * fa; disp[i][1] -= uy * fa
                disp[j][0] += ux * fa; disp[j][1] += uy * fa
        # gravity toward the centroid (keeps the layout compact & connected)
        for i in range(n):
            disp[i][0] += (cx - pos[i][0]) * 0.04
            disp[i][1] += (cy - pos[i][1]) * 0.04
        # apply with cooling
        for i in range(n):
            dl = math.hypot(*disp[i]) or 1.0
            mv = min(dl, step)
            pos[i][0] += disp[i][0] / dl * mv
            pos[i][1] += disp[i][1] / dl * mv
        step = max(0.4, step * 0.97)


def _rot_xy(lx, ly, r):
    if r == 90:   return (-ly, lx)
    if r == 180:  return (-lx, -ly)
    if r == 270:  return (ly, -lx)
    return (lx, ly)


def _rotate(fps, pos, ext, rot, net_pads):
    """Orient EVERY component (2-pin and multi-pin) to minimise trace turns:
    for each candidate orientation (0/90/180/270) compute where the component's
    connected pads land and how far each is from the centroid of the rest of its
    net; pick the orientation with the shortest total — so each connected pin
    ends up on the side facing where its net goes."""
    for i in range(len(fps)):
        my = []                       # (local x, local y, target x, target y)
        for pad in fps[i].get("pads", []):
            nid = pad.get("net", -1)
            if nid < 0:
                continue
            others = [pos[j] for (j, _, _) in net_pads.get(nid, []) if j != i]
            if not others:
                continue
            tx = sum(o[0] for o in others) / len(others)
            ty = sum(o[1] for o in others) / len(others)
            my.append((pad.get("x_mm", 0.0), pad.get("y_mm", 0.0), tx, ty))
        if not my:
            continue
        best_r, best_cost = rot[i], 1e18
        anchor = str(fps[i].get("placement", {}).get("edge_anchor", "")).lower()
        inward = {"left": (1.0, 0.0), "right": (-1.0, 0.0),
                  "top": (0.0, 1.0), "bottom": (0.0, -1.0)}.get(anchor)
        for r in (0, 90, 180, 270):
            cost = 0.0
            for lx, ly, tx, ty in my:
                rx, ry = _rot_xy(lx, ly, r)
                cost += math.hypot(pos[i][0] + rx - tx, pos[i][1] + ry - ty)
            # External connectors and test points should face the board
            # interior. This is a soft preference; the net-distance score
            # still wins when the topology strongly prefers another rotation.
            if inward:
                facing = sum(_rot_xy(lx, ly, r)[0] * inward[0]
                             + _rot_xy(lx, ly, r)[1] * inward[1]
                             for lx, ly, _, _ in my) / len(my)
                cost -= 2.0 * facing
            if cost < best_cost - 1e-6:
                best_cost, best_r = cost, r
        rot[i] = best_r


def _free(p, i, pos, ext, rot):
    """True if component i centred at p overlaps no other (with GAP clearance)."""
    ewi, ehi = _eff(ext[i], rot[i])
    for j in range(len(pos)):
        if j == i:
            continue
        ewj, ehj = _eff(ext[j], rot[j])
        if (abs(p[0] - pos[j][0]) < (ewi + ewj) / 2 + GAP and
                abs(p[1] - pos[j][1]) < (ehi + ehj) / 2 + GAP):
            return False
    return True


def _compact(pos, ext, rot, iters=140):
    """Greedy compaction toward the layout centroid. Each part is slid in X and Y
    INDEPENDENTLY (and diagonally) as far as it can without overlapping — sliding
    on a single axis lets a part settle into a gap a diagonal move would miss,
    so the board densifies without sacrificing the force-directed relative order."""
    n = len(pos)
    for _ in range(iters):
        cx = sum(p[0] for p in pos) / n
        cy = sum(p[1] for p in pos) / n
        for i in range(n):
            # diagonal pull, then independent X then Y slides
            dx, dy = cx - pos[i][0], cy - pos[i][1]
            d = math.hypot(dx, dy) or 0.0
            if d > 0.1:
                mv = min(1.0, d)
                trial = [pos[i][0] + dx / d * mv, pos[i][1] + dy / d * mv]
                if _free(trial, i, pos, ext, rot):
                    pos[i] = trial
            for axis in (0, 1):
                tgt = cx if axis == 0 else cy
                if abs(tgt - pos[i][axis]) < 0.1:
                    continue
                step = min(1.0, abs(tgt - pos[i][axis])) * (1 if tgt > pos[i][axis] else -1)
                trial = pos[i][:]
                trial[axis] += step
                if _free(trial, i, pos, ext, rot):
                    pos[i] = trial


def _resolve_overlaps(pos, ext, rot, iters=80):
    """Separation-constraint pass: push apart any overlapping bounding boxes along
    their axis of least overlap until none remain."""
    n = len(pos)
    for _ in range(iters):
        moved = False
        for i in range(n):
            ewi, ehi = _eff(ext[i], rot[i])
            for j in range(i + 1, n):
                ewj, ehj = _eff(ext[j], rot[j])
                dx = pos[j][0] - pos[i][0]; dy = pos[j][1] - pos[i][1]
                ox = (ewi + ewj) / 2 + GAP - abs(dx)
                oy = (ehi + ehj) / 2 + GAP - abs(dy)
                if ox > 0 and oy > 0:
                    moved = True
                    if ox <= oy:
                        s = (ox / 2 + 0.01) * (1 if dx >= 0 else -1)
                        pos[i][0] -= s; pos[j][0] += s
                    else:
                        s = (oy / 2 + 0.01) * (1 if dy >= 0 else -1)
                        pos[i][1] -= s; pos[j][1] += s
        if not moved:
            break


def place(fps: list[dict], labels: dict[int, int], adj) -> tuple[float, float]:
    n = len(fps)
    ext = [comp_extent(fps[i]) for i in range(n)]
    rot = [int(fps[i].get("rot_deg", 0)) % 360 for i in range(n)]

    # Pin-level net membership: net id → [(component, local x, local y) …], so the
    # rotation step can orient each component toward where each of its pins' net
    # actually goes.
    net_pads: dict[int, list] = defaultdict(list)
    for i in range(n):
        for pad in fps[i].get("pads", []):
            nid = pad.get("net", -1)
            if nid >= 0:
                net_pads[nid].append((i, pad.get("x_mm", 0.0), pad.get("y_mm", 0.0)))

    pos = _seed(fps, labels, ext)
    _force_directed(pos, ext, rot, adj)
    _rotate(fps, pos, ext, rot, net_pads)     # orient every part toward its nets
    _resolve_overlaps(pos, ext, rot)          # guarantee no overlaps
    _compact(pos, ext, rot)                   # densify toward the centroid
    _resolve_overlaps(pos, ext, rot)          # final safety

    # Normalise to the top-left margin and size the board to the layout's extent.
    minx = min(pos[i][0] - _eff(ext[i], rot[i])[0] / 2 for i in range(n))
    miny = min(pos[i][1] - _eff(ext[i], rot[i])[1] / 2 for i in range(n))
    max_x = max_y = 0.0
    for i in range(n):
        ew, eh = _eff(ext[i], rot[i])
        fps[i]["x_mm"] = round(pos[i][0] - minx + MARGIN, 3)
        fps[i]["y_mm"] = round(pos[i][1] - miny + MARGIN, 3)
        fps[i]["rot_deg"] = rot[i]
        max_x = max(max_x, fps[i]["x_mm"] + ew / 2)
        max_y = max(max_y, fps[i]["y_mm"] + eh / 2)

    # Give edge-anchored interfaces a deterministic first-pass location. The
    # constraint legalizer repeats this decision for optimized placement, but
    # draft mode must also make the intended edge ownership visible.
    layout_w = max_x + 2 * MARGIN
    layout_h = max_y + 2 * MARGIN
    for i, fp in enumerate(fps):
        anchor = str(fp.get("placement", {}).get("edge_anchor", "")).lower()
        if anchor not in ("left", "right", "top", "bottom"):
            continue
        ew, eh = _eff(ext[i], rot[i])
        if anchor == "left":
            fp["x_mm"] = round(MARGIN + ew / 2, 3)
        elif anchor == "right":
            fp["x_mm"] = round(layout_w - MARGIN - ew / 2, 3)
        elif anchor == "top":
            fp["y_mm"] = round(MARGIN + eh / 2, 3)
        else:
            fp["y_mm"] = round(layout_h - MARGIN - eh / 2, 3)
    return max_x + MARGIN, max_y + MARGIN


def main(path: str, mode: str = "optimized") -> int:
    with open(path) as f:
        data = json.load(f)

    fps  = data.get("footprints", [])
    nets = {n["id"]: n["name"] for n in data.get("net_table", [])}
    if not fps:
        print("[subsystem-placer] no footprints — nothing to place")
        return 0

    print(f"[subsystem-placer] {len(fps)} components, {len(nets)} nets")
    original_positions = [(float(fp.get("x_mm", 0)), float(fp.get("y_mm", 0)),
                           float(fp.get("rot_deg", 0))) for fp in fps]
    placement_state = data.get("placement_state") or {}
    auto_size_board = placement_state.get("substrate_sizing") in (
        "expanded_to_component_courtyards", "routing_optimized_auto_size"
    ) or placement_state.get("mechanical_reconciliation") == "pending"
    fixed_board = not auto_size_board and bool(data.get("board_outline_pts") or data.get("board_cutouts")
                       or data.get("rule_areas")
                       or any(fp.get("placement", {}).get("locked", False)
                              or fp.get("placement", {}).get("edge_anchor", "")
                              for fp in fps))
    original_board = (float(data.get("board_width_mm", 100)),
                      float(data.get("board_height_mm", 80)))
    # A datasheet build explicitly marks its substrate auto-sized. In that phase
    # the mechanical contract is retained as a target only: placement and routing
    # determine the PCB dimensions first, and enclosure reconciliation follows.
    # Older/finalized documents without that marker keep their locked behavior.
    contract_board = (data.get("mechanical_contract") or {}).get("board", {})
    contract_points = contract_board.get("outline_pts", [])
    if len(contract_points) >= 3 and not auto_size_board:
        xs = [float(point[0]) for point in contract_points]
        ys = [float(point[1]) for point in contract_points]
        data["board_outline_pts"] = contract_points
        original_board = (max(xs) - min(xs), max(ys) - min(ys))
        data["board_width_mm"], data["board_height_mm"] = original_board
        fixed_board = True
    adj = build_graph(fps, nets)
    clustering_adj = prune_weak_edges(adj)         # finer communities
    labels = apply_explicit_groups(fps, detect_subsystems(len(fps), clustering_adj))

    n_clusters = len(set(labels.values()))
    sizes = defaultdict(int)
    for lb in labels.values():
        sizes[lb] += 1
    top = sorted(sizes.values(), reverse=True)[:6]
    print(f"[subsystem-placer] detected {n_clusters} sub-systems "
          f"(largest: {top})")

    # Name the clusters by their most common ref-prefix block for the log.
    by_label: dict[int, list[str]] = defaultdict(list)
    for ci, lb in labels.items():
        by_label[lb].append(fps[ci].get("ref", "?"))
    for lb, refs in sorted(by_label.items(), key=lambda kv: -len(kv[1]))[:6]:
        sample = ", ".join(sorted(refs)[:6])
        print(f"    sub-system {lb}: {len(refs)} parts — {sample}"
              + (" …" if len(refs) > 6 else ""))

    board_w, board_h = place(fps, labels, adj)
    if fixed_board:
        data["board_width_mm"], data["board_height_mm"] = original_board
    else:
        # Never make a provisional substrate smaller than the current draft;
        # grow it whenever routing-oriented placement needs more room. Replacing
        # the stale outline with this rectangle guarantees the displayed FR-4
        # surface and the legalization boundary describe the same board.
        board_w = max(board_w, original_board[0]) if auto_size_board else board_w
        board_h = max(board_h, original_board[1]) if auto_size_board else board_h
        data["board_width_mm"] = round(board_w, 2)
        data["board_height_mm"] = round(board_h, 2)
        data["board_outline_pts"] = [
            [0, 0], [data["board_width_mm"], 0],
            [data["board_width_mm"], data["board_height_mm"]],
            [0, data["board_height_mm"]],
        ]

    if mode == "draft":
        report = apply_constraints(data, original_positions)
        if not report["ok"]:
            print("[subsystem-placer] draft cannot be contained by the substrate", file=sys.stderr)
            for error in report["errors"]:
                print(f"    {error}", file=sys.stderr)
            return 2
        data["placement_state"] = {
            "mode": "draft",
            "status": "awaiting_optimization",
            "reason": "substrate containment passed; routing optimization remains pending",
            "substrate_containment": "passed",
            "substrate_sizing": "expanded_to_component_courtyards" if auto_size_board else "fixed",
            "mechanical_reconciliation": "pending" if auto_size_board else "not_required",
        }
        print("[subsystem-placer] wrote substrate-contained draft placement; routing optimization deferred")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
        return 0

    if mode != "optimized":
        print(f"[subsystem-placer] unsupported placement mode: {mode}", file=sys.stderr)
        return 2

    report = apply_constraints(data, original_positions)
    if not report["ok"]:
        print("[subsystem-placer] placement is unsatisfiable; project left unchanged", file=sys.stderr)
        for error in report["errors"]:
            print(f"    {error}", file=sys.stderr)
        return 2
    data["placement_state"] = {
        "mode": "optimized",
        "status": "legalization_passed",
        "reason": "connectivity, rotation, edge ownership, overlap clearance, and substrate containment optimized",
        "substrate_sizing": "routing_optimized_auto_size" if auto_size_board else "fixed",
        "mechanical_reconciliation": "pending" if auto_size_board else "not_required",
        "mechanical_target_mm": placement_state.get("mechanical_target_mm"),
        "moved": report["moved"],
        "board_mm": [data["board_width_mm"], data["board_height_mm"]],
    }
    if fixed_board:
        print(f"[subsystem-placer] legalized on fixed {original_board[0]:.1f} × "
              f"{original_board[1]:.1f} mm board; moved {report['moved']} parts")
    elif auto_size_board:
        print(f"[subsystem-placer] placed and legalized on routing-optimized "
              f"{board_w:.1f} × {board_h:.1f} mm substrate; mechanical reconciliation pending")
    else:
        print(f"[subsystem-placer] placed and legalized; board resized to "
              f"{board_w:.1f} × {board_h:.1f} mm")

    # Atomic write with the final state marker included.
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)
    print("[subsystem-placer] wrote placement to project")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: subsystem_placer_agent.py <project.dsproj> [--draft|--optimized]")
        sys.exit(1)
    p = sys.argv[1]
    if not os.path.exists(p):
        print(f"error: file not found: {p}")
        sys.exit(1)
    mode = "draft" if "--draft" in sys.argv[2:] else "optimized"
    sys.exit(main(p, mode))
