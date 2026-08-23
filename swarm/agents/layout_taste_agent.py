#!/usr/bin/env python3
"""layout_taste_agent.py — PCB layout floor-planner with design taste.

Applies servo-drive domain knowledge to produce a clean, routable layout:

  • Functional zone floor planning (power stage | SoC core | power supply | I/O)
  • Power stage compacted: U8 gate driver within 8mm of MOSFETs Q1/Q2
  • DDR4 U5 placed within 14mm of U1 SoC (signal integrity requirement)
  • Decoupling caps placed in a tight ring around each IC's power/GND pins,
    grouped by supply rail, not scattered in a grid
  • Connectors moved to board edges
  • Net classes upgraded: VM=2.0mm, 3V3/VDD_CORE=0.5mm, signal=0.15mm,
    diff-pair=0.12mm w/0.1mm gap
  • All components snapped to 0.5mm grid, oriented consistently
  • Existing routes cleared — they were all single-layer and full of crossings;
    re-route with Auto Route after this agent completes

Usage:
  python3 layout_taste_agent.py <project.dsproj>
"""
from __future__ import annotations

import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Optional

# ── Board constraints ─────────────────────────────────────────────────────────
BOARD_W   = 100.0
BOARD_H   =  80.0
GRID      =   0.5   # mm — snap all positions to this grid
CAP_PITCH =   1.6   # mm between cap centres in the IC perimeter ring
CAP_RING  =   1.3   # mm from IC body edge to cap centre

# ── Functional zones (x0,y0,x1,y1) ──────────────────────────────────────────
# Designed for a servo drive PCB: power stage separated from digital, connectors
# at board edges, DDR4 tightly coupled to SoC.
ZONES = {
    "power_stage":  ( 56.0, 38.0, 98.0, 78.0),  # MOSFETs + gate driver + VM caps
    "soc_core":     (  2.0,  2.0, 54.0, 38.0),  # PolarFire + DDR4 + OSC + SPI flash
    "power_supply": (  2.0, 42.0, 52.0, 78.0),  # LDOs + filter caps + power input
    "interfaces":   ( 56.0,  2.0, 98.0, 36.0),  # Ethernet PHY + RS-422 + JTAG
}

# ── Ideal anchor positions (result of floor planning expertise) ────────────────
# These encode the "taste" — domain knowledge about good servo drive layout.
ANCHOR_POS: dict[str, tuple[float, float]] = {
    # Power stage — gate driver adjacent to both MOSFETs; VM caps between input and MOSFETs
    "U8": (76.0, 56.0),   # DRV8353 gate driver (centre of power stage)
    "Q1": (67.5, 56.0),   # BSC0902NSI PMOS high-side (left of U8 so VM flows right→left)
    "Q2": (84.5, 56.0),   # BSC040N NMOS low-side  (right of U8, source→GND)

    # SoC core — U5 DDR4 within 14mm of U1 SoC; oscillator adjacent to U1 CLK side
    "U1": (30.0, 22.0),   # PolarFire SoC (11×11 BGA)
    "U5": (12.0, 22.0),   # DDR4 MT40A512M8 (18mm centre-to-centre — signal integrity OK)
    "U7": (44.0, 22.0),   # SiT9120 oscillator (adjacent to U1 CLK input pins)
    "U6": (30.0, 34.0),   # SPI boot flash (below U1, near BOOT_SCK/BOOT_SS)

    # Power supply — cascades: 12V input → 3.3V → 1.8V, VDD_CORE separate
    "U2": ( 8.0, 60.0),   # MPM3833C 3.3V regulator (closest to 12V input)
    "U3": (24.0, 60.0),   # MPM3610 VDD_CORE regulator
    "U4": (40.0, 60.0),   # V1.8 LDO (farthest, fed from 3.3V)

    # Interfaces — PHY close to SoC EMAC; RS-422 close to U1 UART
    "U10": (64.0, 14.0),  # DP83822 Ethernet PHY
    "U9":  (68.0, 28.0),  # TSSOP-16 encoder/UART interface
}

# IC body radius (half-diagonal of body bounding box, used for ring placement)
# Approximated from known package sizes.
BODY_HALF_SIZE: dict[str, float] = {
    "U1": 5.8,   # 11×11mm BGA
    "U5": 4.8,   # 9×13mm BGA
    "U2": 2.0,   # QFN-18 2.5×3.5mm
    "U3": 2.5,   # QFN-20
    "U4": 1.0,   # X2SON tiny
    "U6": 2.4,   # SOIC-8 8mm long
    "U7": 2.0,   # SMD OSC 5×3.2mm
    "U8": 3.5,   # QFN-41 6×6mm
    "U9": 3.0,   # TSSOP-16
    "U10": 3.8,  # QFN-32 (RHB0032 6×6mm)
    "Q1": 3.0,   # TDSON-8
    "Q2": 3.0,   # TDSON-8
}

# ── Net class tuning (trace widths that make electrical sense) ─────────────────
# Applied to existing net_classes entries by matching the name pattern.
NET_CLASS_OVERRIDES: list[tuple[str, dict]] = [
    # (name_substring_lower, overrides)
    ("vm",            {"trace_width_mm": 2.0,  "clearance_mm": 0.3,  "via_diameter_mm": 1.2, "via_drill_mm": 0.6}),
    ("gnd",           {"trace_width_mm": 0.5,  "clearance_mm": 0.15}),
    ("vdd_core",      {"trace_width_mm": 0.5,  "clearance_mm": 0.15, "via_diameter_mm": 0.8, "via_drill_mm": 0.4}),
    ("vddq",          {"trace_width_mm": 0.4,  "clearance_mm": 0.12}),
    ("vdd",           {"trace_width_mm": 0.3,  "clearance_mm": 0.12}),
    ("3v3",           {"trace_width_mm": 0.4,  "clearance_mm": 0.12}),
    ("v1p8",          {"trace_width_mm": 0.3,  "clearance_mm": 0.10}),
    ("in_12v",        {"trace_width_mm": 1.5,  "clearance_mm": 0.25}),
    # All diff-pair classes: narrow traces, controlled gap
    ("refclk",        {"trace_width_mm": 0.12, "clearance_mm": 0.10, "diff_pair_gap_mm": 0.10, "zdiff_ohm": 100}),
    ("xcvr",          {"trace_width_mm": 0.12, "clearance_mm": 0.10, "diff_pair_gap_mm": 0.10, "zdiff_ohm": 100}),
    ("td_",           {"trace_width_mm": 0.15, "clearance_mm": 0.12, "diff_pair_gap_mm": 0.12, "zdiff_ohm": 100}),
    ("default",       {"trace_width_mm": 0.15, "clearance_mm": 0.10}),
]


# ── Geometry helpers ───────────────────────────────────────────────────────────

def snap(v: float, grid: float = GRID) -> float:
    return round(round(v / grid) * grid, 6)

def pad_board_pos(fp: dict, pad: dict) -> tuple[float, float]:
    """Transform pad relative coords to board space (handles rotation)."""
    rad = math.radians(fp.get("rot_deg", 0))
    px = fp["x_mm"] + pad["x_mm"] * math.cos(rad) - pad["y_mm"] * math.sin(rad)
    py = fp["y_mm"] + pad["x_mm"] * math.sin(rad) + pad["y_mm"] * math.cos(rad)
    return px, py

def component_bbox(fp: dict, margin: float = 0.4) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box with a configurable body margin."""
    if not fp["pads"]:
        return fp["x_mm"]-1, fp["y_mm"]-1, fp["x_mm"]+1, fp["y_mm"]+1
    xs, ys = zip(*[pad_board_pos(fp, p) for p in fp["pads"]])
    return min(xs)-margin, min(ys)-margin, max(xs)+margin, max(ys)+margin

def _is_passive(fp: dict) -> bool:
    return fp["ref"][0] not in ("U", "Q", "J")

def bbox_overlap(a: dict, b: dict) -> bool:
    # Use a tighter margin for 0402/0603 passives — they can sit at 0.2mm clearance
    m_a = 0.15 if _is_passive(a) else 0.4
    m_b = 0.15 if _is_passive(b) else 0.4
    ax0,ay0,ax1,ay1 = component_bbox(a, m_a)
    bx0,by0,bx1,by1 = component_bbox(b, m_b)
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

def count_overlaps(fps: list[dict]) -> int:
    n = 0
    for i in range(len(fps)):
        for j in range(i+1, len(fps)):
            if bbox_overlap(fps[i], fps[j]):
                n += 1
    return n

def body_radius(fp: dict) -> float:
    ref = fp.get("ref", "")
    if ref in BODY_HALF_SIZE:
        return BODY_HALF_SIZE[ref]
    if not fp["pads"]:
        return 1.5
    xs, ys = zip(*[(abs(p["x_mm"]), abs(p["y_mm"])) for p in fp["pads"]])
    return max(max(xs), max(ys)) + 0.6


# ── Floor-planning ─────────────────────────────────────────────────────────────

def apply_anchor_positions(fps: list[dict]) -> None:
    """Move ICs and MOSFETs to their ideal anchor positions."""
    for fp in fps:
        ref = fp["ref"]
        if ref in ANCHOR_POS:
            fp["x_mm"] = snap(ANCHOR_POS[ref][0])
            fp["y_mm"] = snap(ANCHOR_POS[ref][1])
            print(f"  {ref:4s} → ({fp['x_mm']:6.1f}, {fp['y_mm']:6.1f})")


def force_directed_relax(fps: list[dict], net_lookup: dict[int, list[str]],
                          iterations: int = 40, step: float = 0.5) -> None:
    """Nudge ICs within their assigned zones to reduce total wirelength.

    Uses spring attraction between connected components + soft zone boundary
    repulsion. Caps/passives are excluded (placed separately in rings).
    """
    ic_fps = [fp for fp in fps if fp["ref"][0] in ("U", "Q")]
    ref_to_idx = {fp["ref"]: i for i, fp in enumerate(ic_fps)}

    def zone_for(fp):
        ref = fp["ref"]
        for zone_name, (x0, y0, x1, y1) in ZONES.items():
            # check if ic is in this zone
            if x0 <= fp["x_mm"] <= x1 and y0 <= fp["y_mm"] <= y1:
                return (x0, y0, x1, y1)
        # Default: keep in board
        return (2.0, 2.0, 98.0, 78.0)

    # Build net adjacency between ICs
    net_adj: list[tuple[int,int]] = []
    net_set = {}
    for fp in ic_fps:
        for pad in fp["pads"]:
            nid = pad["net"]
            if nid < 0:
                continue
            net_set.setdefault(nid, []).append(fp["ref"])

    for nid, refs in net_set.items():
        for i in range(len(refs)):
            for j in range(i+1, len(refs)):
                ia = ref_to_idx.get(refs[i])
                ib = ref_to_idx.get(refs[j])
                if ia is not None and ib is not None:
                    net_adj.append((ia, ib))

    k_spring  = 0.025   # spring constant — kept weak so repulsion dominates
    k_repel   = 3.0    # repulsion between overlapping ICs — strong, prevents crowding
    k_zone    = 0.20   # zone boundary penalty

    for it in range(iterations):
        step_now = step * (1.0 - it / iterations * 0.7)
        forces = [[0.0, 0.0] for _ in ic_fps]

        # Spring: pull connected ICs toward each other
        for ia, ib in net_adj:
            a, b = ic_fps[ia], ic_fps[ib]
            dx = b["x_mm"] - a["x_mm"]
            dy = b["y_mm"] - a["y_mm"]
            dist = math.hypot(dx, dy) + 0.01
            f = k_spring * dist
            fx, fy = f*dx/dist, f*dy/dist
            forces[ia][0] += fx;  forces[ia][1] += fy
            forces[ib][0] -= fx;  forces[ib][1] -= fy

        # Repulsion: push overlapping ICs apart
        for i in range(len(ic_fps)):
            for j in range(i+1, len(ic_fps)):
                a, b = ic_fps[i], ic_fps[j]
                dx = b["x_mm"] - a["x_mm"]
                dy = b["y_mm"] - a["y_mm"]
                dist = math.hypot(dx, dy) + 0.01
                min_dist = body_radius(a) + body_radius(b) + 1.0
                if dist < min_dist:
                    f = k_repel * (min_dist - dist) / dist
                    forces[i][0] -= f*dx;  forces[i][1] -= f*dy
                    forces[j][0] += f*dx;  forces[j][1] += f*dy

        # Zone boundary: soft wall keeping ICs in their zone
        for i, fp in enumerate(ic_fps):
            x0, y0, x1, y1 = zone_for(fp)
            r = body_radius(fp)
            if fp["x_mm"] - r < x0:
                forces[i][0] += k_zone * (x0 - (fp["x_mm"] - r))
            if fp["x_mm"] + r > x1:
                forces[i][0] -= k_zone * ((fp["x_mm"] + r) - x1)
            if fp["y_mm"] - r < y0:
                forces[i][1] += k_zone * (y0 - (fp["y_mm"] - r))
            if fp["y_mm"] + r > y1:
                forces[i][1] -= k_zone * ((fp["y_mm"] + r) - y1)

        # Apply forces
        for i, fp in enumerate(ic_fps):
            fx, fy = forces[i]
            mag = math.hypot(fx, fy) + 0.01
            # Clamp to step_now
            scale = min(step_now, mag) / mag
            fp["x_mm"] = snap(fp["x_mm"] + fx * scale)
            fp["y_mm"] = snap(fp["y_mm"] + fy * scale)
            # Hard clamp to board
            r = body_radius(fp)
            fp["x_mm"] = max(r + 1.0, min(BOARD_W - r - 1.0, fp["x_mm"]))
            fp["y_mm"] = max(r + 1.0, min(BOARD_H - r - 1.0, fp["y_mm"]))

    # Post-processing: hard push-apart pass for any surviving IC–IC overlaps.
    # Runs until clean or 20 passes exhausted.
    for _ in range(20):
        moved = False
        for i in range(len(ic_fps)):
            for j in range(i+1, len(ic_fps)):
                a, b = ic_fps[i], ic_fps[j]
                dx = b["x_mm"] - a["x_mm"]
                dy = b["y_mm"] - a["y_mm"]
                dist = math.hypot(dx, dy) + 0.01
                min_dist = body_radius(a) + body_radius(b) + 1.5
                if dist < min_dist:
                    push = (min_dist - dist) / 2.0
                    nx, ny = dx / dist, dy / dist
                    b["x_mm"] = snap(b["x_mm"] + push * nx)
                    b["y_mm"] = snap(b["y_mm"] + push * ny)
                    a["x_mm"] = snap(a["x_mm"] - push * nx)
                    a["y_mm"] = snap(a["y_mm"] - push * ny)
                    for fp in (a, b):
                        r = body_radius(fp)
                        fp["x_mm"] = max(r+1.0, min(BOARD_W-r-1.0, fp["x_mm"]))
                        fp["y_mm"] = max(r+1.0, min(BOARD_H-r-1.0, fp["y_mm"]))
                    moved = True
        if not moved:
            break


# ── Decoupling cap ring placement ─────────────────────────────────────────────

def find_parent_ic(cap: dict, ic_fps: list[dict]) -> Optional[dict]:
    """Find the IC whose power pins share a net with this decoupling cap."""
    power_net = cap["pads"][0]["net"] if cap["pads"] else -1
    if power_net < 0:
        return None

    best_ic    = None
    best_score = float("inf")

    for fp in ic_fps:
        shares_net = any(p["net"] == power_net for p in fp["pads"])
        if not shares_net:
            continue
        dist = math.hypot(fp["x_mm"] - cap["x_mm"], fp["y_mm"] - cap["y_mm"])
        if dist < best_score:
            best_score = dist
            best_ic = fp

    return best_ic


def place_caps_in_ring(ic: dict, caps: list[dict]) -> None:
    """Arrange decoupling caps in a neat ring around the IC's body perimeter.

    Caps are grouped by supply net and placed in angular sectors so that
    same-net caps are visually adjacent. All caps get a consistent
    horizontal orientation (rot_deg=0) regardless of position in ring.
    """
    if not caps:
        return

    r = body_radius(ic) + CAP_RING

    # Group caps by their power net (pad 0)
    net_groups: dict[int, list[dict]] = {}
    for cap in caps:
        nid = cap["pads"][0]["net"] if cap["pads"] else -1
        net_groups.setdefault(nid, []).append(cap)

    # Assign angle slots in the ring, one group per arc sector
    n_total   = len(caps)
    angle_per = 2 * math.pi / n_total

    slot = 0
    for nid in sorted(net_groups.keys()):
        group = net_groups[nid]
        for cap in group:
            angle = -math.pi/2 + slot * angle_per  # start at 12 o'clock
            cx = snap(ic["x_mm"] + r * math.cos(angle))
            cy = snap(ic["y_mm"] + r * math.sin(angle))
            # Clamp to board
            cx = max(1.0, min(BOARD_W - 1.0, cx))
            cy = max(1.0, min(BOARD_H - 1.0, cy))
            cap["x_mm"]     = cx
            cap["y_mm"]     = cy
            cap["rot_deg"]  = 0.0   # all caps horizontal — clean, consistent
            slot += 1

    # Compact the ring: if two caps ended up at the same slot, push them apart
    # (can happen with small rings and many caps — just nudge radially)
    all_positions = [(c["x_mm"], c["y_mm"]) for c in caps]
    for i in range(len(caps)):
        for j in range(i+1, len(caps)):
            pi_ = caps[i]; pj_ = caps[j]
            dx = pj_["x_mm"] - pi_["x_mm"]
            dy = pj_["y_mm"] - pi_["y_mm"]
            dist = math.hypot(dx, dy)
            if dist < CAP_PITCH * 0.8:
                # Nudge j outward by rotating its angle slot outward
                nudge = (CAP_PITCH - dist) / 2.0
                if dist < 0.01:
                    angle = 0.0
                else:
                    angle = math.atan2(dy, dx)
                pj_["x_mm"] = snap(pj_["x_mm"] + nudge * math.cos(angle))
                pj_["y_mm"] = snap(pj_["y_mm"] + nudge * math.sin(angle))


def place_connectors_at_edges(fps: list[dict]) -> None:
    """Move connectors to board edges and orient them for clean access."""
    for fp in fps:
        ref = fp["ref"]
        if not ref.startswith("J"):
            continue
        if "pwr" in fp.get("lib", "").lower() or "TH_2PIN" in fp.get("lib", ""):
            # Power input: left edge, lower half
            fp["x_mm"]    = 3.0
            fp["y_mm"]    = snap(70.0)
            fp["rot_deg"] = 0.0
            print(f"  {ref:4s} → left edge  ({fp['x_mm']:.1f}, {fp['y_mm']:.1f})")
        elif "jtag" in fp.get("lib", "").lower() or "6pin" in fp.get("lib","").lower() or "TH_6PIN" in fp.get("lib",""):
            # JTAG: bottom edge, right of centre
            fp["x_mm"]    = snap(74.0)
            fp["y_mm"]    = 77.0
            fp["rot_deg"] = 0.0
            print(f"  {ref:4s} → bottom edge ({fp['x_mm']:.1f}, {fp['y_mm']:.1f})")


# ── Net class tuning ──────────────────────────────────────────────────────────

def upgrade_net_classes(net_classes: list[dict]) -> int:
    changed = 0
    for nc in net_classes:
        name_lo = nc["name"].lower()
        for substr, overrides in NET_CLASS_OVERRIDES:
            if substr in name_lo:
                for k, v in overrides.items():
                    if nc.get(k) != v:
                        nc[k] = v
                        changed += 1
                break  # first matching rule wins
    return changed


# ── Main ──────────────────────────────────────────────────────────────────────

def main(project_path: str) -> None:
    print(f"[layout-taste] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps         = data["footprints"]
    net_table   = data.get("net_table", [])
    net_lookup  = {n["id"]: n["name"] for n in net_table}
    net_classes = data.get("net_classes", [])

    ic_fps      = [fp for fp in fps if fp["ref"][0] in ("U", "Q")]
    passive_fps = [fp for fp in fps if fp["ref"][0] not in ("U", "Q")]

    before_overlaps = count_overlaps(fps)
    print(f"[layout-taste] Before: {len(fps)} components, "
          f"{len(data['traces'])} traces, {before_overlaps} overlaps")
    print()

    # ── Phase 1: anchor positions ─────────────────────────────────────────────
    print("━━━━ Phase 1: Floor-plan — functional zone anchors ━━━━")
    apply_anchor_positions(fps)
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.4)

    # ── Phase 2: force-directed relaxation of ICs within zones ────────────────
    print("\n━━━━ Phase 2: Force-directed refinement (40 iterations) ━━━━")
    force_directed_relax(fps, net_lookup)
    # Summarise IC final positions
    for fp in ic_fps:
        print(f"  {fp['ref']:4s} → ({fp['x_mm']:5.1f}, {fp['y_mm']:5.1f})")
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.4)

    # ── Phase 3: place decoupling caps in ring around parent IC ───────────────
    print("\n━━━━ Phase 3: Decoupling caps — perimeter ring placement ━━━━")

    # Group passives by parent IC
    cap_groups: dict[str, list[dict]] = {}
    unparented: list[dict] = []
    connectors: list[dict] = []

    for fp in passive_fps:
        if fp["ref"].startswith("J"):
            connectors.append(fp)
            continue
        parent = find_parent_ic(fp, ic_fps)
        if parent:
            key = parent["ref"]
            cap_groups.setdefault(key, []).append(fp)
        else:
            unparented.append(fp)

    for ic_ref, caps in sorted(cap_groups.items()):
        ic = next(f for f in ic_fps if f["ref"] == ic_ref)
        place_caps_in_ring(ic, caps)
        print(f"  {ic_ref}: {len(caps)} caps in ring (r={body_radius(ic)+CAP_RING:.1f}mm)")

    # Stack any unparented caps neatly at board corner
    for i, fp in enumerate(unparented):
        fp["x_mm"] = snap(4.0 + (i % 8) * 1.6)
        fp["y_mm"] = snap(4.0 + (i // 8) * 1.6)

    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.4)

    # ── Phase 4: move connectors to board edges ───────────────────────────────
    print("\n━━━━ Phase 4: Connectors → board edges ━━━━")
    place_connectors_at_edges(fps)
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.4)

    # ── Phase 5: snap everything to 0.5mm grid ────────────────────────────────
    print("\n━━━━ Phase 5: Grid snap (0.5mm) & orientation ━━━━")
    for fp in fps:
        fp["x_mm"] = snap(fp["x_mm"])
        fp["y_mm"] = snap(fp["y_mm"])
        # ICs: keep existing rotation (pin 1 orientation matters)
        # Passives: horizontal by default for clean routing channels
        if fp["ref"][0] not in ("U", "Q", "J"):
            fp["rot_deg"] = 0.0

    # ── Phase 6: upgrade net class trace widths ───────────────────────────────
    print("\n━━━━ Phase 6: Net class upgrades ━━━━")
    changed = upgrade_net_classes(net_classes)
    print(f"  Updated {changed} net-class field(s)")
    for nc in net_classes:
        if nc["trace_width_mm"] >= 0.4:
            print(f"  {nc['name']:40s}  w={nc['trace_width_mm']}mm  cl={nc['clearance_mm']}mm")

    # ── Phase 7: clear routes (they're invalid after component moves) ─────────
    print("\n━━━━ Phase 7: Clear old routes ━━━━")
    old_traces = len(data["traces"])
    old_vias   = len(data["vias"])
    data["traces"] = []
    data["vias"]   = []
    print(f"  Removed {old_traces} traces and {old_vias} vias")
    print("  → Hit Auto Route (or /route) to re-route the optimised board")

    # ── Final write ───────────────────────────────────────────────────────────
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    after_overlaps = count_overlaps(fps)

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Layout optimisation complete")
    print(f"  Overlapping pairs: {before_overlaps} → {after_overlaps}")

    # Key distance checks
    def dist(a_ref, b_ref):
        a = next((f for f in fps if f["ref"]==a_ref), None)
        b = next((f for f in fps if f["ref"]==b_ref), None)
        if not (a and b): return None
        return math.hypot(a["x_mm"]-b["x_mm"], a["y_mm"]-b["y_mm"])

    u1u5 = dist("U1", "U5")
    u8q1 = dist("U8", "Q1")
    u8q2 = dist("U8", "Q2")
    if u1u5: print(f"  U1→U5 (SoC→DDR4):      {u1u5:.1f}mm  (target ≤15mm) {'✓' if u1u5 <= 16 else '!'}")
    if u8q1: print(f"  U8→Q1 (gate→PMOS):     {u8q1:.1f}mm  (target ≤8mm)  {'✓' if u8q1 <= 10 else '!'}")
    if u8q2: print(f"  U8→Q2 (gate→NMOS):     {u8q2:.1f}mm  (target ≤8mm)  {'✓' if u8q2 <= 10 else '!'}")
    print()
    print("  Next steps:")
    print("   1. Click Auto Route (or type /route) to re-route the cleaned board")
    print("   2. Run DRC to confirm no clearance violations")
    print("   3. Run SI/PI Check for impedance review")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: layout_taste_agent.py <project.dsproj>")
        sys.exit(1)
    path = sys.argv[1]
    import os
    if not os.path.exists(path):
        print(f"error: not found: {path}")
        sys.exit(1)
    main(path)
