#!/usr/bin/env python3
"""drc_fix_agent.py — Auto-fix the four root-cause DRC issues.

Fix 1 (1,716 violations): Deduplicate traces and vias.
  The autorouter was run multiple times on the same net without clearing first,
  stacking identical trace segments and vias on top of each other.

Fix 2 (370 violations): Clear stale routes that clash with moved component pads.
  Any trace or via whose midpoint is inside an IC pad's bounding box but on a
  DIFFERENT net triggers PadTraceClearance / ViaPadClearance. These came from
  placement agents repositioning ICs without re-routing.

Fix 3 (9 violations): Create per-footprint net-class clearance entries for U2,
  U4, U7 so their package-standard pad pitches don't violate the board-level
  clearance rule.
  - U2 QFN-20: min inter-pad gap 0.075 mm  → footprint clearance 0.07 mm
  - U4 X2SON:  min inter-pad gap 0.025 mm  → footprint clearance 0.02 mm
  - U7 OSC-SMD-6: min inter-pad gap 0.12mm → footprint clearance 0.10 mm

Fix 4 (informs re-routing): Print the 23 UnconnectedNet islands (U1 power
  domains) as actionable items for the next routing pass.

Usage:
  python3 drc_fix_agent.py <project.dsproj>
"""
from __future__ import annotations
import json, math, os, sys, time
from collections import defaultdict
from typing import Optional

NM_PER_MM = 1_000_000


def pad_board_bbox(fp: dict, p: dict, margin: float = 0.0
                   ) -> tuple[float, float, float, float]:
    rad = math.radians(fp.get("rot_deg", 0))
    bx = fp["x_mm"] + p["x_mm"] * math.cos(rad) - p["y_mm"] * math.sin(rad)
    by = fp["y_mm"] + p["x_mm"] * math.sin(rad) + p["y_mm"] * math.cos(rad)
    hw = p["w_mm"] / 2 + margin
    hh = p["h_mm"] / 2 + margin
    return bx - hw, by - hh, bx + hw, by + hh


# ── Fix 1: deduplicate traces ─────────────────────────────────────────────────

def dedup_traces(traces: list[dict]) -> tuple[list[dict], int]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for t in traces:
        key = (
            round(t.get("ax_mm", 0), 4),
            round(t.get("ay_mm", 0), 4),
            round(t.get("bx_mm", 0), 4),
            round(t.get("by_mm", 0), 4),
            t.get("layer", 0),
            t.get("net", -1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    removed = len(traces) - len(out)
    return out, removed


def dedup_vias(vias: list[dict]) -> tuple[list[dict], int]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for v in vias:
        key = (
            round(v.get("x_mm", 0), 4),
            round(v.get("y_mm", 0), 4),
            v.get("net", -1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    removed = len(vias) - len(out)
    return out, removed


# ── Fix 2: clear stale routes clashing with moved IC pads ────────────────────

def clear_stale_routes(
    traces: list[dict],
    vias:   list[dict],
    fps:    list[dict],
) -> tuple[list[dict], list[dict], int, int]:
    """Remove any trace/via whose midpoint lies inside an IC/transistor pad bbox
    on a DIFFERENT net — these are stale routes from old component positions."""
    ic_pads: list[tuple[dict, dict]] = [
        (fp, p)
        for fp in fps
        if fp["ref"][0] in ("U", "Q")
        for p in fp["pads"]
        if p["net"] >= 0
    ]

    def midpoint_in_pad(mx: float, my: float, net: int) -> bool:
        for fp, p in ic_pads:
            if p["net"] == net:
                continue  # same-net route near pad is fine
            x0, y0, x1, y1 = pad_board_bbox(fp, p, margin=0.0)
            if x0 <= mx <= x1 and y0 <= my <= y1:
                return True
        return False

    new_traces: list[dict] = []
    stale_tr = 0
    for t in traces:
        mx = (t.get("ax_mm", 0) + t.get("bx_mm", 0)) / 2
        my = (t.get("ay_mm", 0) + t.get("by_mm", 0)) / 2
        if midpoint_in_pad(mx, my, t.get("net", -1)):
            stale_tr += 1
        else:
            new_traces.append(t)

    new_vias: list[dict] = []
    stale_v = 0
    for v in vias:
        if midpoint_in_pad(v.get("x_mm", 0), v.get("y_mm", 0), v.get("net", -1)):
            stale_v += 1
        else:
            new_vias.append(v)

    return new_traces, new_vias, stale_tr, stale_v


# ── Fix 3: per-footprint clearance net classes ────────────────────────────────

# Package min pad gaps measured from footprint geometry
FOOTPRINT_CLEARANCE = {
    "U2": 0.07,   # QFN-20: side pads to bottom exposed pad 0.075mm gap
    "U4": 0.02,   # X2SON: exposed thermal pad to signal pads 0.025mm gap
    "U7": 0.10,   # OSC-SMD-6: adjacent pads 0.12mm gap
}

def add_footprint_net_classes(
    net_classes: list[dict],
    net_table:   list[dict],
    fps:         list[dict],
) -> tuple[list[dict], int]:
    """For tightly-pitched packages, create a per-footprint net class with a
    relaxed clearance equal to the actual package pad gap.  Assign all nets
    whose pads live on that footprint to the new class — but only if those nets
    aren't already in a tighter-than-needed class."""
    net_by_id  = {n["id"]: n for n in net_table}
    max_id     = max((nc["id"] for nc in net_classes), default=0)
    added      = 0

    for ref, clearance_mm in FOOTPRINT_CLEARANCE.items():
        fp = next((f for f in fps if f["ref"] == ref), None)
        if not fp:
            continue

        class_name = f"Footprint_{ref}"
        if any(nc["name"] == class_name for nc in net_classes):
            continue  # already exists

        max_id += 1
        new_nc = {
            "clearance_mm":    clearance_mm,
            "diff_pair_gap_mm": 0,
            "id":              max_id,
            "max_skew_mm":     0,
            "microvia":        False,
            "name":            class_name,
            "trace_width_mm":  0.15,
            "via_diameter_mm": 0.6,
            "via_drill_mm":    0.3,
            "z0_ohm":          0,
            "zdiff_ohm":       0,
        }
        net_classes.append(new_nc)

        # Move all nets that have at least one pad on this footprint
        # to the new class, unless they already have a smaller clearance.
        pad_nets = {p["net"] for p in fp["pads"] if p["net"] >= 0}
        for net_id in pad_nets:
            n = net_by_id.get(net_id)
            if not n:
                continue
            cur_nc = next((nc for nc in net_classes if nc["id"] == n.get("class_id", 0)), None)
            if cur_nc and cur_nc["clearance_mm"] <= clearance_mm:
                continue  # already tighter or equal
            n["class_id"] = max_id

        added += 1
        print(f"  Created class '{class_name}' (clearance {clearance_mm}mm) "
              f"for {len(pad_nets)} nets on {ref}")

    return net_classes, added


# ── Fix 4: report UnconnectedNet islands ─────────────────────────────────────

def report_unconnected(data: dict) -> None:
    """Load libdesigncore and run DRC just to print remaining UnconnectedNet.
    Falls back gracefully if the library is not available."""
    try:
        import ctypes
        from pathlib import Path
        here = Path(__file__).resolve()
        lib_path = None
        for d in [here.parent, here.parent.parent, here.parent.parent.parent]:
            for cand in [d / "core/build_linux/libdesigncore.so",
                         d / "core/build/Release/designcore.dll"]:
                if cand.exists():
                    lib_path = str(cand); break
            if lib_path: break
        if not lib_path:
            print("  (libdesigncore not found — skip connectivity check)")
            return

        # Import DRC runner from drc_agent if available
        agent_dir = here.parent
        sys.path.insert(0, str(agent_dir))
        from drc_agent import _load_lib, build_board, DcDrcOptions, DcDrcViolation, RULE_NAMES
        nm_fn = lambda mm: int(round(mm * NM_PER_MM))

        lib = _load_lib()
        h   = build_board(lib, data)
        opt = DcDrcOptions(
            default_clearance  = nm_fn(0.2),
            min_trace_width    = nm_fn(0.1),
            min_drill          = nm_fn(0.10),
            min_annular_ring   = nm_fn(0.05),
            min_drill_to_drill = nm_fn(0.25),
            check_connectivity = 1, check_skew = 0,
            min_microvia_drill = nm_fn(0.075),
            min_microvia_wall  = nm_fn(0.1),
        )
        total = lib.dc_drc_run(h, ctypes.byref(opt))
        net_lut = {n["id"]: n["name"] for n in data.get("net_table", [])}
        by_rule: dict[str, list[str]] = defaultdict(list)
        v = DcDrcViolation()
        for i in range(max(total, 0)):
            if lib.dc_drc_get(h, i, ctypes.byref(v)) == 0:
                by_rule[RULE_NAMES.get(v.rule, "Other")].append(
                    v.message.decode("utf-8", errors="replace"))
        lib.dc_board_destroy(h)

        unconn = by_rule.get("UnconnectedNet", [])
        if unconn:
            print(f"  Remaining UnconnectedNet violations ({len(unconn)}):")
            for msg in sorted(set(unconn)):
                print(f"    {msg}")
        else:
            print("  No UnconnectedNet violations — all nets connected!")

        total_remain = sum(len(v) for v in by_rule.values())
        print(f"\n  Post-fix DRC total: {total_remain} violations")
        print("  Remaining by rule:")
        for rule, viols in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            print(f"    {rule:28s}  {len(viols)}")

    except Exception as e:
        print(f"  (post-fix DRC check failed: {e})")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path: str) -> None:
    print(f"[drc-fix] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps        = data["footprints"]
    traces     = data["traces"]
    vias       = data["vias"]
    net_classes = data["net_classes"]
    net_table  = data["net_table"]

    print(f"[drc-fix] Before: {len(traces)} traces, {len(vias)} vias\n")

    # ── Fix 1: Deduplicate routes ─────────────────────────────────────────────
    print("━━━━ Fix 1: Deduplicate traces and vias ━━━━")
    new_traces, dup_tr = dedup_traces(traces)
    new_vias,   dup_v  = dedup_vias(vias)
    data["traces"] = new_traces
    data["vias"]   = new_vias
    print(f"  Removed {dup_tr} duplicate trace segments  "
          f"({len(new_traces)} remain)")
    print(f"  Removed {dup_v} duplicate vias  "
          f"({len(new_vias)} remain)")
    print(f"  Violations fixed: ~{dup_tr*2 + dup_v*2} "
          f"(TraceTraceClearance + DrillToDrill)")
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.3)

    # ── Fix 2: Clear stale routes ─────────────────────────────────────────────
    print("\n━━━━ Fix 2: Clear stale routes clashing with IC pads ━━━━")
    clean_traces, clean_vias, stale_tr, stale_v = clear_stale_routes(
        data["traces"], data["vias"], fps)
    data["traces"] = clean_traces
    data["vias"]   = clean_vias
    print(f"  Removed {stale_tr} stale trace segments")
    print(f"  Removed {stale_v} stale vias")
    print(f"  Violations fixed: ~{stale_tr + stale_v} "
          f"(PadTraceClearance + ViaPadClearance + ViaTraceClearance)")
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.3)

    # ── Fix 3: Per-footprint clearance net classes ────────────────────────────
    print("\n━━━━ Fix 3: Per-footprint clearance net classes ━━━━")
    data["net_classes"], nc_added = add_footprint_net_classes(
        net_classes, net_table, fps)
    if nc_added == 0:
        print("  All per-footprint classes already exist.")
    print(f"  Violations fixed: up to 9 (PadPadClearance for U2/U4/U7)")
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    time.sleep(0.3)

    # ── Fix 4: Report remaining UnconnectedNet ────────────────────────────────
    print("\n━━━━ Fix 4: Remaining UnconnectedNet (needs routing pass) ━━━━")
    report_unconnected(data)

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  drc_fix_agent complete")
    print(f"  Final: {len(data['traces'])} traces, {len(data['vias'])} vias")
    print(f"  Next step: run Auto Route to connect the remaining open nets")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: drc_fix_agent.py <project.dsproj>"); sys.exit(1)
    if not os.path.exists(sys.argv[1]):
        print(f"error: not found: {sys.argv[1]}"); sys.exit(1)
    main(sys.argv[1])
