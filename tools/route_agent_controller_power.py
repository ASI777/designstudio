#!/usr/bin/env python3
"""Route the controller power domains and stitch SMD ground pads to In1.GND.

The signal router deliberately reserves the six supply nets.  This pass uses
the same native obstacle-aware router, but applies package-scale neckdowns,
current-sized trunks, and one legal through-via escape per SMD ground terminal.
The continuous ground fill is generated separately after these stitches exist.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "swarm" / "agents"))

from bga_reroute_agent import (  # type: ignore
    build_net_pad_map,
    declare_route_fns,
    fine_pitch_escape_planner,
    greedy_spanning_tree,
    pad_world_xy,
    route_connection,
    trace_identity,
)
from drc_agent import _load_lib, build_board, nm  # type: ignore


POWER_NAMES = ("VBAT", "VSYS", "VBUS", "+5V_LED_RAW", "+5V_LED",
               "+3V3", "L1", "L2", "LED_BOOST_SW")
# VSYS/VBAT carry both downstream converters at once (3.3 V/1 A + 5 V/1 A
# worst case ≈ 2.4 A), so their trunks and budgets are sized for 2.5 A; the
# previous 0.60 mm / 1.5 A sizing screened with only ~10% margin on VSYS.
POWER_WIDTH_MM = {"VBUS": .60, "VBAT": 1.00, "VSYS": 1.00,
                  "+5V_LED_RAW": .60, "+5V_LED": .50, "+3V3": .50,
                  "L1": .80, "L2": .80, "LED_BOOST_SW": .80}
POWER_CURRENT_A = {"VBUS": 1.50, "VBAT": 2.50, "VSYS": 2.50,
                   "+5V_LED_RAW": 1.00, "+5V_LED": 1.00, "+3V3": 1.00,
                   "L1": 1.50, "L2": 1.50, "LED_BOOST_SW": 1.50}


def annotate(traces: list[dict], current: float) -> None:
    for trace in traces:
        trace["min_width_override_mm"] = float(trace["w_mm"])
        trace["required_current_a"] = round(current, 4)


def route_spanning_with_alternates(lib, board, net_id: int,
                                   points: list[tuple[float, float, int]],
                                   width: float, current: float) -> tuple[list[dict], list[dict], int, int]:
    """Build a legal tree, trying longer alternate edges when a corridor is blocked."""
    parent = list(range(len(points)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    candidates = []
    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            ax, ay, _ = points[left]; bx, by, _ = points[right]
            candidates.append((math.hypot(ax - bx, ay - by), left, right))
    candidates.sort()
    traces: list[dict] = []; vias: list[dict] = []
    successes = failures = 0
    for distance, left, right in candidates:
        aroot, broot = root(left), root(right)
        if aroot == broot:
            continue
        sx, sy, sl = points[left]; gx, gy, gl = points[right]
        tr, vi = route_connection(lib, board, net_id, sx, sy, sl, gx, gy, gl,
                                  nm(width), nm(.20), nm(.70), nm(.35))
        local_current = current
        if not tr and not vi:
            # A 0.60/0.30 mm via is still within the project/fabricator floor
            # and often clears dense power packages where the preferred
            # 0.70/0.35 mm trunk via cannot.
            tr, vi = route_connection(lib, board, net_id, sx, sy, sl, gx, gy, gl,
                                      nm(width), nm(.20), nm(.60), nm(.30))
        if not tr and not vi and distance <= 10.0:
            # Two or more adjacent IC pins share the load.  Their short merge
            # neck is intentionally narrower than the full-current trunk.
            local_width = min(width, .30)
            tr, vi = route_connection(lib, board, net_id, sx, sy, sl, gx, gy, gl,
                                      nm(local_width), nm(.20), nm(.60), nm(.30))
            local_current = current / 2.0
        if not tr and not vi:
            failures += 1
            continue
        annotate(tr, local_current)
        traces.extend(tr); vias.extend(vi)
        parent[broot] = aroot; successes += 1
        if successes == len(points) - 1:
            break
    remaining = len({root(index) for index in range(len(points))})
    return traces, vias, successes, max(remaining - 1, 0)


def install_vbus_connector_escape(lib, board, data: dict, vbus: int) -> tuple[list[dict], tuple[float, float, int]]:
    connector = next(fp for fp in data.get("footprints", []) if fp.get("ref") == "J1")
    layer = int(data.get("copper_layers", 4)) - 1
    points = sorted((pad_world_xy(connector, pad) for pad in connector.get("pads", [])
                     if int(pad.get("net", -1)) == vbus))
    xs = sorted({round(point[0], 4) for point in points})
    ys = sorted({round(point[1], 4) for point in points})
    if len(xs) != 2 or len(ys) != 2:
        raise RuntimeError("J1 VBUS escape expects two rows of two contacts")
    # Join each same-net VBUS row first, then connect the rows outside the
    # left contact column.  A vertical connection through either contact
    # column would cut directly through the intervening CC/data/GND drills.
    escape_x = round(xs[0] - 2.325, 4)
    segments = [
        (xs[0], ys[0], xs[1], ys[0]),
        (xs[0], ys[1], xs[1], ys[1]),
        (escape_x, ys[0], xs[0], ys[0]),
        (escape_x, ys[0], escape_x, ys[1]),
        (escape_x, ys[1], xs[0], ys[1]),
    ]
    traces: list[dict] = []
    for ax, ay, bx, by in segments:
        trace = {"ax_mm": ax, "ay_mm": ay, "bx_mm": bx, "by_mm": by,
                 "w_mm": .60, "layer": layer, "net": vbus, "pour": False,
                 "min_width_override_mm": .60, "required_current_a": 1.50}
        trace_id = lib.dc_trace_add(board, nm(ax), nm(ay), nm(bx), nm(by),
                                    nm(.60), layer, vbus, 0)
        if trace_id:
            lib.dc_trace_min_width_set(board, trace_id, nm(.60))
        traces.append(trace)
    return traces, (escape_x, ys[1], layer)


def install_boost_switch_bridge(
        lib, board, data: dict, net_id: int,
        escape_by_net: dict[int, list[tuple[float, float, int]]]
) -> tuple[list[dict], tuple[float, float, int]]:
    """Close the intentionally short boost-IC-to-inductor switch loop."""
    starts = escape_by_net.get(net_id, [])
    if len(starts) != 1:
        raise RuntimeError("LED boost switch node expects one IC escape")
    inductor = next(fp for fp in data.get("footprints", [])
                    if fp.get("ref") == "L2")
    target_pad = next(pad for pad in inductor.get("pads", [])
                      if int(pad.get("net", -1)) == net_id)
    tx, ty = pad_world_xy(inductor, target_pad)
    start = starts[0]
    if start[2] != 0:
        raise RuntimeError("LED boost switch escape must remain on F.Cu")
    item = {"ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
            "bx_mm": round(tx, 4), "by_mm": round(ty, 4),
            "w_mm": .60, "layer": 0, "net": net_id, "pour": False,
            "min_width_override_mm": .60, "required_current_a": 1.50}
    trace_id = lib.dc_trace_add(board, nm(start[0]), nm(start[1]),
                                nm(tx), nm(ty), nm(.60), 0, net_id, 0)
    if not trace_id:
        raise RuntimeError("failed to install LED boost switch bridge")
    lib.dc_trace_min_width_set(board, trace_id, nm(.60))
    return [item], (tx, ty, 0)


def install_fixed_signal_bridges(
        lib, board, data: dict, name_to_id: dict[str, int],
        escape_by_net: dict[int, list[tuple[float, float, int]]]
) -> tuple[list[dict], list[dict]]:
    """Install placement-defined local links before any global domain route."""
    added: list[dict] = []
    added_vias: list[dict] = []
    layers = int(data.get("copper_layers", 4))

    def pad_for(fp: dict, net_id: int) -> tuple[float, float]:
        pad = next((item for item in fp.get("pads", [])
                    if int(item.get("net", -1)) == net_id), None)
        if pad is None:
            raise RuntimeError(
                f"{fp.get('ref')} has no pad for staged net {net_id}")
        return pad_world_xy(fp, pad)

    def install(start: tuple[float, float], finish: tuple[float, float],
                net_id: int, layer: int) -> None:
        trace_id = lib.dc_trace_add(
            board, nm(start[0]), nm(start[1]), nm(finish[0]), nm(finish[1]),
            nm(.20), layer, net_id, 0)
        if not trace_id:
            raise RuntimeError(f"failed to stage local signal net {net_id}")
        lib.dc_trace_min_width_set(board, trace_id, nm(.20))
        added.append({
            "ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
            "bx_mm": round(finish[0], 4), "by_mm": round(finish[1], 4),
            "w_mm": .20, "layer": layer, "net": net_id, "pour": False,
            "min_width_override_mm": .20,
        })

    footprints = {str(fp.get("ref")): fp for fp in data.get("footprints", [])}

    # The COL pull-up and filter capacitor signal pads face one another.  Join
    # each pair before global matrix routing so a neighboring column cannot
    # claim the short RC corridor first.
    for index in range(4):
        net_id = name_to_id[f"COL{index}"]
        install(pad_for(footprints[f"R{14 + index}"], net_id),
                pad_for(footprints[f"C{33 + index}"], net_id),
                net_id, 0)

    # Alternating 0/180 LED orientation makes every within-row DOUT/DIN pair
    # collinear.  These short daisy-chain links are fixed local topology on
    # the emitters' own layer (top side since the south-facing re-placement);
    # row-wrap links remain available to the global router.
    for index in range(1, 13):
        left = footprints[f"LED{index}"]
        right = footprints[f"LED{index + 1}"]
        if abs(float(left["y_mm"]) - float(right["y_mm"])) > 1e-6:
            continue
        net_id = name_to_id[f"LED_CHAIN_{index}"]
        start = pad_for(left, net_id)
        finish = pad_for(right, net_id)
        if abs(start[1] - finish[1]) > 1e-4:
            raise RuntimeError(
                f"LED_CHAIN_{index} pads are not collinear after placement")
        install(start, finish, net_id, 0)

    # U5 and U6 are one local LED-power control group.  Join their staged
    # LED_ENABLE package exits while the board is still sparse; the global
    # signal router then only has to reach this already-connected group.
    led_enable = name_to_id["LED_ENABLE"]
    enable_endpoints = sorted(set(escape_by_net.get(led_enable, [])))
    if len(enable_endpoints) != 2:
        raise RuntimeError("LED_ENABLE local group expects two package exits")
    start, finish = enable_endpoints
    traces, vias = route_connection(
        lib, board, led_enable,
        start[0], start[1], start[2],
        finish[0], finish[1], finish[2],
        nm(.20), nm(.20), nm(.60), nm(.30))
    if not traces and not vias:
        raise RuntimeError(
            f"failed to stage U5-U6 LED_ENABLE bridge: "
            f"{start!r} -> {finish!r}")
    added.extend(traces)
    added_vias.extend(vias)

    # U5's timing capacitor is a local analog network.  Route from the staged
    # package exit directly into the nearby, inward-facing C13 signal pad
    # before global traffic can occupy that short corridor.
    led_ct = name_to_id["LED_CT"]
    ct_endpoints = sorted(set(escape_by_net.get(led_ct, [])))
    if len(ct_endpoints) != 1:
        raise RuntimeError("LED_CT local group expects one package exit")
    ct_start = ct_endpoints[0]
    ct_target = pad_for(footprints["C13"], led_ct)
    traces, vias = route_connection(
        lib, board, led_ct,
        ct_start[0], ct_start[1], ct_start[2],
        ct_target[0], ct_target[1], 0,
        nm(.20), nm(.20), nm(.60), nm(.30))
    if not traces and not vias:
        raise RuntimeError(
            f"failed to stage U5-C13 LED_CT bridge: "
            f"{ct_start!r} -> {ct_target!r}")
    added.extend(traces)
    added_vias.extend(vias)
    return added, added_vias


def install_local_power_bridges(
        lib, board, data: dict, name_to_id: dict[str, int],
        escape_by_net: dict[int, list[tuple[float, float, int]]]
) -> tuple[list[dict], list[dict]]:
    """Join package pins that belong to one rail outside their pin fields."""
    added: list[dict] = []
    added_vias: list[dict] = []

    def closest(net_id: int, point: tuple[float, float]) -> tuple[float, float, int]:
        candidates = escape_by_net.get(net_id, [])
        if not candidates:
            raise RuntimeError(f"missing fine-pitch endpoint for net {net_id}")
        return min(candidates, key=lambda item:
                   math.hypot(item[0] - point[0], item[1] - point[1]))

    def install(points: list[tuple[float, float]], net_id: int,
                widths: list[float], currents: list[float]) -> None:
        for index, (start, finish) in enumerate(zip(points, points[1:])):
            width = widths[index]; current = currents[index]
            item = {"ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
                    "bx_mm": round(finish[0], 4), "by_mm": round(finish[1], 4),
                    "w_mm": width, "layer": 0, "net": net_id, "pour": False,
                    "min_width_override_mm": width,
                    "required_current_a": current}
            trace_id = lib.dc_trace_add(
                board, nm(start[0]), nm(start[1]), nm(finish[0]), nm(finish[1]),
                nm(width), 0, net_id, 0)
            if not trace_id:
                raise RuntimeError(f"failed to install local bridge for net {net_id}")
            lib.dc_trace_min_width_set(board, trace_id, nm(width))
            added.append(item)

    # U6.3 is only 0.5 mm from LED_ENABLE.  Extend just its narrow VSYS neck
    # before allowing the 0.60 mm trunk to widen.  Extending every U6 fanout
    # would push the opposite-side ground endpoint into the boost-inductor
    # land, so the taper contract is deliberately per pin/net.
    vsys = name_to_id["VSYS"]
    u6 = next(fp for fp in data["footprints"] if fp.get("ref") == "U6")
    u6_vsys_pad = next(pad for pad in u6["pads"]
                       if int(pad.get("net", -1)) == vsys)
    u6_vsys = pad_world_xy(u6, u6_vsys_pad)
    vsys_start = closest(vsys, u6_vsys)
    dx = vsys_start[0] - u6_vsys[0]
    dy = vsys_start[1] - u6_vsys[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        raise RuntimeError("U6 VSYS escape has no outward direction")
    vsys_extended = (vsys_start[0] + .80 * dx / length,
                     vsys_start[1] + .80 * dy / length,
                     vsys_start[2])
    install([(vsys_start[0], vsys_start[1]), vsys_extended],
            vsys, [.15], [.35])
    escape_by_net[vsys] = [
        vsys_extended if point == vsys_start else point
        for point in escape_by_net.get(vsys, [])
    ]

    # The buck-boost switch nodes are a placement-defined local loop, not
    # board-wide signals.  Neck down only for the QFN land, then widen directly
    # into the adjacent inductor pads.  Removing the synthetic endpoints after
    # this bridge prevents the generic tree router from taking a long detour.
    u4 = next(fp for fp in data["footprints"] if fp.get("ref") == "U4")
    l1_part = next(fp for fp in data["footprints"] if fp.get("ref") == "L1")
    for switch_name in ("L1", "L2"):
        switch_net = name_to_id[switch_name]
        u4_pad = next(pad for pad in u4["pads"]
                      if int(pad.get("net", -1)) == switch_net)
        switch_start = closest(switch_net, pad_world_xy(u4, u4_pad))
        inductor_pad = next(pad for pad in l1_part["pads"]
                            if int(pad.get("net", -1)) == switch_net)
        switch_target = pad_world_xy(l1_part, inductor_pad)
        sx, sy = switch_start[0], switch_start[1]
        dx = switch_target[0] - sx
        dy = switch_target[1] - sy
        length = math.hypot(dx, dy)
        if length < 1e-6:
            raise RuntimeError(f"{switch_name} switch escape has no length")
        # Preserve the narrow QFN neck until it has cleared the neighboring
        # VSYS/+3V3 fanouts; widening after only 0.60 mm blocks those adjacent
        # supply pins even though the inductor placement itself is legal.
        neck_length = min(1.50, length)
        neck_end = (sx + neck_length * dx / length,
                    sy + neck_length * dy / length)
        install([(sx, sy), neck_end, switch_target], switch_net,
                [.15, .80], [.35, 1.50])
        escape_by_net[switch_net] = [
            point for point in escape_by_net.get(switch_net, [])
            if point != switch_start
        ]

    # U6.6 exits toward the inductor side; bend below the IC and enter C15's
    # broad raw-rail land without crossing the adjacent switch node.
    raw = name_to_id["+5V_LED_RAW"]
    u6_raw_pad = next(pad for pad in u6["pads"] if int(pad.get("net", -1)) == raw)
    u6_raw = pad_world_xy(u6, u6_raw_pad)
    raw_start = closest(raw, u6_raw)
    c15 = next(fp for fp in data["footprints"] if fp.get("ref") == "C15")
    c15_raw_pad = next(pad for pad in c15["pads"] if int(pad.get("net", -1)) == raw)
    raw_target = pad_world_xy(c15, c15_raw_pad)
    raw_entry = (raw_target[0], raw_target[1] - .60)
    raw_points = [(raw_start[0], raw_start[1]),
                  (raw_start[0], raw_start[1] + .80),
                  (raw_start[0] + 1.0, raw_start[1] + 1.80),
                  (raw_target[0] - 1.0, raw_target[1] - 1.60),
                  raw_entry]
    install(raw_points, raw, [.15, .60, .60, .60],
            [.35, 1.0, 1.0, 1.0])
    escape_by_net[raw] = [point for point in escape_by_net.get(raw, [])
                          if point != raw_start]

    # U5.5/U5.6 are parallel outputs of the load switch.  Join their two
    # bottom-edge escapes before the board-wide LED trunk fans out.
    led = name_to_id["+5V_LED"]
    u5 = next(fp for fp in data["footprints"] if fp.get("ref") == "U5")
    led_pads = [pad for pad in u5["pads"] if int(pad.get("net", -1)) == led]
    led_ends = [closest(led, pad_world_xy(u5, pad)) for pad in led_pads]
    led_ends = sorted(set(led_ends), key=lambda point: (point[0], point[1]))
    if len(led_ends) != 2:
        raise RuntimeError("U5 LED rail bridge expects two distinct escapes")
    install([(led_ends[0][0], led_ends[0][1]),
             (led_ends[1][0], led_ends[1][1])],
            led, [.50], [1.0])
    escape_by_net[led] = [point for point in escape_by_net.get(led, [])
                          if point != led_ends[0]]

    # U4.8 (VINA) sits between two ground pins.  Its ordered fine-pitch escape
    # is legal, but a full-width global route cannot turn inside that channel.
    # Continue straight into the adjacent VSYS inductor land.
    u4_vina_pad = next(pad for pad in u4["pads"]
                       if str(pad.get("name")) == "8")
    vina_start = closest(vsys, pad_world_xy(u4, u4_vina_pad))
    l2 = next(fp for fp in data["footprints"] if fp.get("ref") == "L2")
    l2_vsys_pad = next(pad for pad in l2["pads"]
                       if int(pad.get("net", -1)) == vsys)
    l2_vsys = pad_world_xy(l2, l2_vsys_pad)
    vina_entry = (l2_vsys[0] - .60, l2_vsys[1])
    install([(vina_start[0], vina_start[1]), vina_entry],
            vsys, [.15], [.10])
    escape_by_net[vsys] = [point for point in escape_by_net.get(vsys, [])
                           if point != vina_start]

    # U4.10 is boxed in by ordered perimeter fanouts on F.Cu.  Drop that
    # parallel VOUT pin to B.Cu outside the package, join the nearby output
    # capacitor, then return to F.Cu.  Both vias remain outside component
    # lands and the power plane layers stay untouched.
    v33 = name_to_id["+3V3"]
    u4_vout_pad = next(pad for pad in u4["pads"]
                       if str(pad.get("name")) == "10")
    vout_start = closest(v33, pad_world_xy(u4, u4_vout_pad))
    c4 = next(fp for fp in data["footprints"] if fp.get("ref") == "C4")
    c4_vout_pad = next(pad for pad in c4["pads"]
                       if int(pad.get("net", -1)) == v33)
    vout_target = pad_world_xy(c4, c4_vout_pad)
    via_a = (vout_start[0] + .25, vout_start[1] + 2.75)
    via_b = (vout_target[0] - 1.15, vout_target[1])
    install([(vout_start[0], vout_start[1]), via_a],
            v33, [.50], [1.0])
    install([via_b, vout_target], v33, [.50], [1.0])
    bottom = int(data.get("copper_layers", 4)) - 1
    # A direct via-to-via diagonal crosses the through-hole barrel used by the
    # adjacent U4.9 ground escape even though its top-layer copper appears
    # clear.  Keep the B.Cu bridge in a right-side corridor and use only
    # 45-degree transitions so the route clears every layer-spanning hole.
    bottom_path = [
        via_a,
        (via_a[0] + 2.0, via_a[1] - 2.0),
        (via_a[0] + 2.0, via_b[1] + 1.5),
        (via_b[0] + 2.85, via_b[1]),
        via_b,
    ]
    for start, finish in zip(bottom_path, bottom_path[1:]):
        bottom_item = {"ax_mm": round(start[0], 4),
                       "ay_mm": round(start[1], 4),
                       "bx_mm": round(finish[0], 4),
                       "by_mm": round(finish[1], 4),
                       "w_mm": .50, "layer": bottom, "net": v33,
                       "pour": False, "min_width_override_mm": .50,
                       "required_current_a": 1.0}
        bottom_trace_id = lib.dc_trace_add(
            board, nm(start[0]), nm(start[1]),
            nm(finish[0]), nm(finish[1]),
            nm(.50), bottom, v33, 0)
        if not bottom_trace_id:
            raise RuntimeError("failed to install U4.10 bottom-layer bridge")
        lib.dc_trace_min_width_set(board, bottom_trace_id, nm(.50))
        added.append(bottom_item)
    for point in (via_a, via_b):
        via_id = lib.dc_via_add(board, nm(point[0]), nm(point[1]),
                                nm(.60), nm(.30), v33, 0, bottom)
        if not via_id:
            raise RuntimeError("failed to install U4.10 bridge via")
        added_vias.append({"x_mm": round(point[0], 4),
                           "y_mm": round(point[1], 4), "dia_mm": .60,
                           "drill_mm": .30, "net": v33, "from": 0,
                           "to": bottom, "via_type": "through"})
    escape_by_net[v33] = [point for point in escape_by_net.get(v33, [])
                          if point != vout_start]
    return added, added_vias


def ground_terminals(data: dict, ground: int,
                     escape_by_net: dict[int, list[tuple[float, float, int]]],
                     ground_escape_starts: set[tuple[float, float, int]],
                     already_stitched: set[tuple[float, float, int]]) -> list[tuple[float, float, int, str]]:
    terminals: list[tuple[float, float, int, str]] = []
    layers = int(data.get("copper_layers", 4))
    for fp in data.get("footprints", []):
        layer = layers - 1 if int(fp.get("side", 0)) == 1 else 0
        for pad in fp.get("pads", []):
            if int(pad.get("net", -1)) != ground or pad.get("th", False):
                continue
            x, y = pad_world_xy(fp, pad)
            key = (round(x, 4), round(y, 4), layer)
            if key in ground_escape_starts or key in already_stitched:
                continue
            terminals.append((x, y, layer, fp["ref"]))
    terminals.extend((x, y, layer, "fine-pitch") for x, y, layer in
                     escape_by_net.get(ground, [])
                     if (round(x, 4), round(y, 4), layer) not in already_stitched)
    return terminals


def install_module_thermal_array(
        lib, board, data: dict, ground: int
) -> tuple[list[dict], set[tuple[float, float, int]]]:
    """Stitch the ESP32-S3-WROOM-1 exposed thermal pad (pin 41) to the planes.

    The module datasheet's recommended land pattern calls for vias in the
    thermal pad.  The previous design connected the 6x6 mm EP with a single
    0.20 mm trace to a via ~15 mm away, which is inadequate for the radio's
    RF return and the 0.65 W thermal path.  Install a 3x3 through-via array
    on a 1.75 mm grid centred on the pad (via-in-pad is the datasheet's own
    recommendation for this module).
    """
    fp = next((item for item in data.get("footprints", [])
               if item.get("ref") == "U1"), None)
    if fp is None:
        return [], set()
    ep = next((pad for pad in fp.get("pads", [])
               if str(pad.get("name")) == "41"
               and int(pad.get("net", -1)) == ground), None)
    if ep is None:
        return [], set()
    cx, cy = pad_world_xy(fp, ep)
    layers = int(data.get("copper_layers", 4))
    vias: list[dict] = []
    for ox in (-1.75, 0.0, 1.75):
        for oy in (-1.75, 0.0, 1.75):
            x, y = round(cx + ox, 4), round(cy + oy, 4)
            via_id = lib.dc_via_add(board, nm(x), nm(y), nm(.60), nm(.30),
                                    ground, 0, layers - 1)
            if not via_id:
                raise RuntimeError("failed to install U1 thermal-pad array via")
            vias.append({"x_mm": x, "y_mm": y, "dia_mm": .60, "drill_mm": .30,
                         "net": ground, "from": 0, "to": layers - 1,
                         "via_type": "through"})
    stitched = {(round(cx, 4), round(cy, 4), 0)}
    return vias, stitched


def install_special_ground_escapes(
        lib, board, data: dict, ground: int
) -> tuple[list[dict], list[dict], set[tuple[float, float, int]]]:
    """Install the ESD-array ground escape inside its verified pair corridor.

    A generic radial escape can cross either USB member because U2 sits
    between them.  Both ground pads are instead joined in package coordinates
    and continued to one through via below the array.  The coordinates remain
    placement-derived, so moving or rotating U2 cannot leave stale copper.
    """
    fp = next((item for item in data.get("footprints", [])
               if item.get("ref") == "U2"), None)
    if fp is None:
        return [], [], set()
    pads = sorted((pad_world_xy(fp, pad) for pad in fp.get("pads", [])
                   if int(pad.get("net", -1)) == ground),
                  key=lambda point: (point[1], point[0]))
    if len(pads) != 2:
        raise RuntimeError("U2 ground escape expects exactly two ground pads")
    upper, lower = pads
    via_point = (lower[0], lower[1] + .90)
    layer = 0
    layers = int(data.get("copper_layers", 4))
    segments = [(upper, lower), (lower, via_point)]
    traces: list[dict] = []
    for start, finish in segments:
        item = {"ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
                "bx_mm": round(finish[0], 4), "by_mm": round(finish[1], 4),
                "w_mm": .15, "layer": layer, "net": ground, "pour": False,
                "min_width_override_mm": .15, "required_current_a": .10}
        trace_id = lib.dc_trace_add(
            board, nm(start[0]), nm(start[1]), nm(finish[0]), nm(finish[1]),
            nm(.15), layer, ground, 0)
        if not trace_id:
            raise RuntimeError("failed to install U2 ground escape trace")
        lib.dc_trace_min_width_set(board, trace_id, nm(.15))
        traces.append(item)
    via_id = lib.dc_via_add(board, nm(via_point[0]), nm(via_point[1]),
                            nm(.50), nm(.25), ground, 0, layers - 1)
    if not via_id:
        raise RuntimeError("failed to install U2 ground escape via")
    vias = [{"x_mm": round(via_point[0], 4),
             "y_mm": round(via_point[1], 4), "dia_mm": .50,
             "drill_mm": .25, "net": ground, "from": 0,
             "to": layers - 1, "via_type": "through"}]
    stitched = {(round(point[0], 4), round(point[1], 4), 0)
                for point in pads}

    # The two GND contacts at the cable-end corners of the USB-C receptacle
    # can fall between the raster strokes of an otherwise continuous plane.
    # Join them to the adjacent plated shell tab on In1.GND. All three
    # terminals are through-hole, so this creates a short, mechanically robust
    # connector return without a via-in-pad or a trace through the signal row.
    connector = next((item for item in data.get("footprints", [])
                      if item.get("ref") == "J1"), None)
    if connector is not None:
        ground_by_name = {
            str(pad.get("name")): pad_world_xy(connector, pad)
            for pad in connector.get("pads", [])
            if int(pad.get("net", -1)) == ground
        }
        required = {"1", "16", "M1"}
        if not required.issubset(ground_by_name):
            raise RuntimeError("J1 ground bridge pin contract changed")
        bridge_layer = layers - 1
        for pin_name in ("1", "16"):
            start = ground_by_name[pin_name]
            finish = ground_by_name["M1"]
            trace_id = lib.dc_trace_add(
                board, nm(start[0]), nm(start[1]), nm(finish[0]), nm(finish[1]),
                nm(.25), bridge_layer, ground, 0)
            if not trace_id:
                raise RuntimeError(
                    f"failed to install J1.{pin_name} shell-ground bridge")
            lib.dc_trace_min_width_set(board, trace_id, nm(.25))
            traces.append({
                "ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
                "bx_mm": round(finish[0], 4), "by_mm": round(finish[1], 4),
                "w_mm": .25, "layer": bridge_layer, "net": ground,
                "pour": False, "min_width_override_mm": .25,
                "required_current_a": .35,
            })
            stitched.add((round(start[0], 4), round(start[1], 4),
                          bridge_layer))

    # Each per-key LED decoupling capacitor's ground pad would otherwise be
    # encircled by the +5V_LED trunk that feeds its VDD pad one courtyard
    # away.  Install a deterministic short escape before the trunks exist so
    # the trunk routes around the stitch instead of walling the pad in.  The
    # via sits on the LED centre row between the two daisy-chain link rows;
    # C28 mirrors its capacitor west of the LED, so its escape mirrors too.
    for index in range(17, 30):
        cap = next((item for item in data.get("footprints", [])
                    if item.get("ref") == f"C{index}"), None)
        if cap is None:
            continue
        gnd_pad = next((pad for pad in cap.get("pads", [])
                        if int(pad.get("net", -1)) == ground), None)
        if gnd_pad is None:
            continue
        px, py = pad_world_xy(cap, gnd_pad)
        if cap["ref"] == "C28":
            # West-side capacitor: an east/west escape along the pad row runs
            # into the sibling VDD pad or the LED courtyard, so drop south to
            # the open channel below the daisy-chain link row instead.
            via_point = (px, py + 1.675)
        else:
            via_point = (px + 1.2, py)
        trace_id = lib.dc_trace_add(
            board, nm(px), nm(py), nm(via_point[0]), nm(via_point[1]),
            nm(.20), 0, ground, 0)
        if not trace_id:
            raise RuntimeError(f"failed to install {cap['ref']} ground escape trace")
        lib.dc_trace_min_width_set(board, trace_id, nm(.20))
        traces.append({
            "ax_mm": round(px, 4), "ay_mm": round(py, 4),
            "bx_mm": round(via_point[0], 4), "by_mm": round(via_point[1], 4),
            "w_mm": .20, "layer": 0, "net": ground, "pour": False,
            "min_width_override_mm": .20, "required_current_a": .35,
        })
        via_id = lib.dc_via_add(board, nm(via_point[0]), nm(via_point[1]),
                                nm(.60), nm(.30), ground, 0, layers - 1)
        if not via_id:
            raise RuntimeError(f"failed to install {cap['ref']} ground escape via")
        vias.append({"x_mm": round(via_point[0], 4),
                     "y_mm": round(via_point[1], 4), "dia_mm": .60,
                     "drill_mm": .30, "net": ground, "from": 0,
                     "to": layers - 1, "via_type": "through"})
        stitched.add((round(px, 4), round(py, 4), 0))

    # U4's right-side ground pins face the large boost-inductor land pattern,
    # so radial trial routing has no legal via site.  Reserve deterministic
    # left/right sites before routing the surrounding supply copper.
    u4 = next((item for item in data.get("footprints", [])
               if item.get("ref") == "U4"), None)
    if u4 is not None:
        ground_pads = {str(pad.get("name")): (pad, pad_world_xy(u4, pad))
                       for pad in u4.get("pads", [])
                       if int(pad.get("net", -1)) == ground}
        if set(ground_pads) != {"3", "7", "9", "11"}:
            raise RuntimeError("U4 ground escape pin contract changed")
        paths = {
            "3": [(ground_pads["3"][1][0] - 1.70,
                   ground_pads["3"][1][1])],
            "7": [(ground_pads["7"][1][0] + 1.70,
                   ground_pads["7"][1][1] - .125),
                  (ground_pads["7"][1][0] + 2.45,
                   ground_pads["7"][1][1] - .875)],
            # U4.9 is between VINA and VOUT.  End at a local via before their
            # common fanout boundary; extending it to x+1.70 mm forces the
            # ground return to cross the adjacent U4.10 escape.
            "9": [(ground_pads["9"][1][0] + 1.30,
                   ground_pads["9"][1][1] + .125)],
            "11": [(ground_pads["11"][1][0],
                    ground_pads["11"][1][1] + 2.0)],
        }
        for name, (_, start) in ground_pads.items():
            width = .40 if name == "11" else .15
            current = .70 if name == "11" else .25
            route = [start, *paths[name]]
            for segment_start, finish in zip(route, route[1:]):
                item = {"ax_mm": round(segment_start[0], 4),
                        "ay_mm": round(segment_start[1], 4),
                        "bx_mm": round(finish[0], 4),
                        "by_mm": round(finish[1], 4),
                        "w_mm": width, "layer": 0, "net": ground,
                        "pour": False, "min_width_override_mm": width,
                        "required_current_a": current}
                trace_id = lib.dc_trace_add(
                    board, nm(segment_start[0]), nm(segment_start[1]),
                    nm(finish[0]), nm(finish[1]), nm(width), 0, ground, 0)
                if not trace_id:
                    raise RuntimeError(
                        f"failed to install U4.{name} ground trace")
                lib.dc_trace_min_width_set(board, trace_id, nm(width))
                traces.append(item)
            finish = route[-1]
            via_id = lib.dc_via_add(board, nm(finish[0]), nm(finish[1]),
                                    nm(.50), nm(.25), ground, 0, layers - 1)
            if not via_id:
                raise RuntimeError(f"failed to install U4.{name} ground via")
            vias.append({"x_mm": round(finish[0], 4),
                         "y_mm": round(finish[1], 4), "dia_mm": .50,
                         "drill_mm": .25, "net": ground, "from": 0,
                         "to": layers - 1, "via_type": "through"})
            stitched.add((round(start[0], 4), round(start[1], 4), 0))

    # R5's ISET exit is staged before power routing and occupies the only
    # generic radial stitch corridor.  Send the opposite ground pad straight
    # down to a placement-derived via outside the resistor courtyard.
    r5 = next((item for item in data.get("footprints", [])
               if item.get("ref") == "R5"), None)
    if r5 is not None:
        ground_pad = next((pad for pad in r5.get("pads", [])
                           if int(pad.get("net", -1)) == ground), None)
        if ground_pad is None:
            raise RuntimeError("R5 ground pad contract changed")
        start = pad_world_xy(r5, ground_pad)
        finish = (start[0], start[1] + 1.50)
        trace_id = lib.dc_trace_add(
            board, nm(start[0]), nm(start[1]), nm(finish[0]), nm(finish[1]),
            nm(.20), 0, ground, 0)
        if not trace_id:
            raise RuntimeError("failed to install R5 ground escape trace")
        lib.dc_trace_min_width_set(board, trace_id, nm(.20))
        traces.append({
            "ax_mm": round(start[0], 4), "ay_mm": round(start[1], 4),
            "bx_mm": round(finish[0], 4), "by_mm": round(finish[1], 4),
            "w_mm": .20, "layer": 0, "net": ground, "pour": False,
            "min_width_override_mm": .20, "required_current_a": .10,
        })
        via_id = lib.dc_via_add(board, nm(finish[0]), nm(finish[1]),
                                nm(.50), nm(.25), ground, 0, layers - 1)
        if not via_id:
            raise RuntimeError("failed to install R5 ground escape via")
        vias.append({"x_mm": round(finish[0], 4),
                     "y_mm": round(finish[1], 4), "dia_mm": .50,
                     "drill_mm": .25, "net": ground, "from": 0,
                     "to": layers - 1, "via_type": "through"})
        stitched.add((round(start[0], 4), round(start[1], 4), 0))
    return traces, vias, stitched


def stitch_ground(lib, board, data: dict, ground: int,
                  terminals: list[tuple[float, float, int, str]]) -> tuple[list[dict], list[dict], list[dict]]:
    traces: list[dict] = []
    vias: list[dict] = []
    failures: list[dict] = []
    layers = int(data.get("copper_layers", 4))
    # Deterministic radial candidate order.  Each successful route changes
    # layers at a through via, thereby touching the plane on In1.GND.
    offsets = ((.8, 0), (-.8, 0), (0, .8), (0, -.8),
               (1.0, 0), (-1.0, 0), (0, 1.0), (0, -1.0),
               (1.2, 0), (-1.2, 0), (0, 1.2), (0, -1.2),
               (1.4, 0), (-1.4, 0), (0, 1.4), (0, -1.4),
               (2.0, 0), (-2.0, 0), (0, 2.0), (0, -2.0),
               (1.4, 1.4), (-1.4, 1.4), (1.4, -1.4), (-1.4, -1.4),
               (3.0, 0), (-3.0, 0), (0, 3.0), (0, -3.0),
               (3.0, 2.0), (-3.0, 2.0), (3.0, -2.0), (-3.0, -2.0))
    # Far-reach candidates are tried last: they let terminals that sit inside
    # a no-via rule area (e.g. the module antenna keepout) reach a legal
    # stitch site beyond its boundary instead of silently failing.
    far_offsets = ((0, 4.5), (0, -4.5), (4.5, 0), (-4.5, 0),
                   (0, 6.0), (0, -6.0), (6.0, 0), (-6.0, 0),
                   (2.5, 6.0), (-2.5, 6.0), (2.5, -6.0), (-2.5, -6.0),
                   (0, 7.5), (0, -7.5), (7.5, 0), (-7.5, 0))
    all_offsets = offsets + far_offsets

    def nearest_ground_pad(x: float, y: float, max_dist: float = 4.5):
        best = None
        for fp in data.get("footprints", []):
            for pad in fp.get("pads", []):
                if int(pad.get("net", -1)) != ground or pad.get("th", False):
                    continue
                px, py = pad_world_xy(fp, pad)
                dist = math.hypot(px - x, py - y)
                if 0.01 < dist < max_dist and (best is None or dist < best[0]):
                    best = (dist, px, py)
        return (best[1], best[2]) if best else (x, y)

    def attempt(x: float, y: float, layer: int, ref: str) -> bool:
        other = layers - 1 if layer == 0 else 0
        for dx, dy in all_offsets:
            gx, gy = x + dx, y + dy
            if gx < 1.0 or gx > float(data["board_width_mm"]) - 1.0:
                continue
            if gy < 1.0 or gy > float(data["board_height_mm"]) - 1.0:
                continue
            tr = []; vi = []
            widths = (.15, .20, .30) if ref == "fine-pitch" else (.20, .30, .50)
            for width in widths:
                tr, vi = route_connection(lib, board, ground, x, y, layer, gx, gy, other,
                                          nm(width), nm(.20), nm(.60), nm(.30))
                if vi:
                    break
            if not vi:
                continue
            # A ground escape serves only its local load; it is not a 2 A trunk.
            branch_current = .08 if ref.startswith("LED") else .35
            annotate(tr, branch_current)
            traces.extend(tr); vias.extend(vi)
            return True
        return False

    for x, y, layer, ref in terminals:
        routed = attempt(x, y, layer, ref)
        if not routed and ref == "fine-pitch":
            # The escape-stub tip can sit in a pocket the A* cannot leave.
            # Retry from the originating package pad one escape-run back,
            # whose fanout geometry normally has a legal exit corridor.
            px, py = nearest_ground_pad(x, y)
            if (px, py) != (x, y):
                routed = attempt(px, py, layer, ref)
        if not routed:
            failures.append({"ref": ref, "x_mm": round(x, 4),
                             "y_mm": round(y, 4), "layer": layer})
    return traces, vias, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    name_to_id = {str(net["name"]): int(net["id"])
                  for net in data.get("net_table", [])}
    missing = [name for name in ("GND", *POWER_NAMES) if name not in name_to_id]
    if missing:
        raise SystemExit(f"missing power nets: {', '.join(missing)}")
    power_ids = {name_to_id[name] for name in ("GND", *POWER_NAMES)}

    # Replay-safe: remove only power copper installed by an earlier power pass.
    data["traces"] = [trace for trace in data.get("traces", [])
                      if int(trace.get("net", -1)) not in power_ids]
    data["vias"] = [via for via in data.get("vias", [])
                    if int(via.get("net", -1)) not in power_ids]

    lib = _load_lib()
    declare_route_fns(lib)
    # Size verification uses the full-current IPC-2221 external-copper curve.
    # Keep these routed trunks on F.Cu/B.Cu; In2 remains available for explicit
    # filled power partitions rather than narrow autorouted branches.
    routing_data = copy.deepcopy(data)
    for net_class in routing_data.get("net_classes", []):
        if int(net_class.get("id", -1)) == 2:
            net_class["allowed_layers"] = [0, int(data.get("copper_layers", 4)) - 1]
    board = build_board(lib, routing_data)
    ground = name_to_id["GND"]
    print("[power-route] staging special ground escapes", flush=True)
    special_traces, special_vias, already_stitched = install_special_ground_escapes(
        lib, board, data, ground)
    data["traces"].extend(special_traces)
    data["vias"].extend(special_vias)
    thermal_vias, thermal_stitched = install_module_thermal_array(lib, board, data, ground)
    data["vias"].extend(thermal_vias)
    already_stitched |= thermal_stitched
    nc_by_id = {int(item["id"]): item for item in data.get("net_classes", [])}
    default_nc = nc_by_id.get(0, {})
    nc_by_net = {int(net["id"]): nc_by_id.get(
        int(net.get("class_id", net.get("class", 0))), default_nc)
        for net in data.get("net_table", [])}

    escape_by_net: dict[int, list[tuple[float, float, int]]] = defaultdict(list)
    escaped_refs: set[str] = set()
    ground_escape_starts: set[tuple[float, float, int]] = set()
    print("[power-route] staging fine-pitch package exits", flush=True)
    for fp in data.get("footprints", []):
        ref = str(fp.get("ref", ""))
        if ref in {"U2", "J1"}:
            continue
        skipped: set[int] = {ground} if ref == "U4" else set()
        spread = 1.5 if ref == "U3" else 1.25
        normal_run = {
            "U3": 2.5, "U4": 2.0, "U2": 1.2,
        }.get(ref, 1.0)
        traces, endpoints = fine_pitch_escape_planner(
            lib, board, fp, nc_by_net, default_nc,
            int(data.get("copper_layers", 4)), skipped, None,
            spread_factor=spread, normal_run_override=normal_run)
        if not traces:
            continue
        for trace in traces:
            trace["required_current_a"] = .35
            if int(trace["net"]) == name_to_id["GND"]:
                ground_escape_starts.add((round(float(trace["ax_mm"]), 4),
                                          round(float(trace["ay_mm"]), 4),
                                          int(trace["layer"])))
        data["traces"].extend(traces)
        escaped_refs.add(fp["ref"])
        for net_id, values in endpoints.items():
            escape_by_net[net_id].extend(values)

    print("[power-route] staging fixed local signal bridges", flush=True)
    fixed_signal_traces, fixed_signal_vias = install_fixed_signal_bridges(
        lib, board, data, name_to_id, escape_by_net)
    data["traces"].extend(fixed_signal_traces)
    data["vias"].extend(fixed_signal_vias)

    print("[power-route] staging compact switch/power bridges", flush=True)
    boost_id = name_to_id["LED_BOOST_SW"]
    boost_traces, boost_hub = install_boost_switch_bridge(
        lib, board, data, boost_id, escape_by_net)
    data["traces"].extend(boost_traces)
    escape_by_net[boost_id] = [boost_hub]
    local_power_traces, local_power_vias = install_local_power_bridges(
        lib, board, data, name_to_id, escape_by_net)
    data["traces"].extend(local_power_traces)
    data["vias"].extend(local_power_vias)

    net_pads = build_net_pad_map(data.get("footprints", []), escaped_refs,
                                 dict(escape_by_net), int(data.get("copper_layers", 4)))
    vbus_escape, vbus_hub = install_vbus_connector_escape(
        lib, board, data, name_to_id["VBUS"])
    data["traces"].extend(vbus_escape)
    # The four connector contacts are already one copper component at the hub;
    # route the rest of VBUS from that single synthetic terminal.
    net_pads[name_to_id["VBUS"]] = [vbus_hub] + [point for point in
        net_pads.get(name_to_id["VBUS"], []) if point[0] > 70.0]
    net_pads[boost_id] = [boost_hub]
    result: dict[str, dict[str, int]] = {}
    for name in POWER_NAMES:
        print(f"[power-route] routing {name}", flush=True)
        net_id = name_to_id[name]
        points = net_pads.get(net_id, [])
        width = POWER_WIDTH_MM[name]
        tr, vi, successes, failures = route_spanning_with_alternates(
            lib, board, net_id, points, width, POWER_CURRENT_A[name])
        data["traces"].extend(tr); data["vias"].extend(vi)
        result[name] = {"edges_ok": successes, "edges_failed": failures}

    terminals = ground_terminals(data, ground, dict(escape_by_net),
                                 ground_escape_starts, already_stitched)
    print(f"[power-route] stitching {len(terminals)} ground terminals", flush=True)
    gtr, gvi, gfail = stitch_ground(lib, board, data, ground, terminals)
    data["traces"].extend(gtr); data["vias"].extend(gvi)
    result["GND"] = {"stitches_ok": len(terminals) - len(gfail),
                     "stitches_failed": len(gfail), "failed_terminals": gfail}

    data["power_routing_provenance"] = {
        "schema": "design-studio.power-routing/1",
        "generator": "tools/route_agent_controller_power.py",
        # Both inner layers are poured as continuous ground reference
        # (tools/pour_net_plane.py runs for layers 1 and 2).  Power is
        # distributed on the outer layers; no layer-2 power island exists.
        "ground_reference_layers": [1, 2],
        "power_distribution_layers": [0, 3],
        "package_neckdowns_current_allocated": True,
        "fixed_local_signal_bridges": len(fixed_signal_traces),
        "fixed_local_signal_vias": len(fixed_signal_vias),
        "results": result,
    }
    # Fine-pitch signal fanouts are staged before power routing so every
    # domain sees them as obstacles.  A replay or later signal pass may
    # regenerate the same segment; retain one deterministic copy.
    unique_traces: list[dict] = []
    seen_traces: set[tuple] = set()
    for trace in data["traces"]:
        key = trace_identity(trace)
        if key in seen_traces:
            continue
        seen_traces.add(key)
        unique_traces.append(trace)
    data["traces"] = unique_traces
    lib.dc_board_destroy(board)
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    print(json.dumps({"ok": True, "results": result,
                      "traces": len(data["traces"]), "vias": len(data["vias"])},
                     sort_keys=True))
    return 0 if all(not value.get("edges_failed", 0)
                    and not value.get("stitches_failed", 0)
                    for value in result.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
