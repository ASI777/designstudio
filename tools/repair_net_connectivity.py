#!/usr/bin/env python3
"""Repair disconnected net islands by trying alternate native-router pad pairs."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "swarm" / "agents"))
from drc_agent import _load_lib, build_board, nm  # type: ignore
from bga_reroute_agent import (declare_route_fns, pad_world_xy,  # type: ignore
                               route_connection)


def terminal_groups(data: dict, net_id: int) -> list[list[tuple[float, float, int]]]:
    """Approximate native islands from exact A* endpoints and via layer spans."""
    parent: dict[tuple[float, float, int], tuple[float, float, int]] = {}
    layers = int(data.get("copper_layers", 4))

    def key(x: float, y: float, layer: int) -> tuple[float, float, int]:
        return round(x, 4), round(y, 4), layer

    def find(value):
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    terminals: list[tuple[float, float, int]] = []
    for fp in data.get("footprints", []):
        for pad in fp.get("pads", []):
            if int(pad.get("net", -1)) != net_id:
                continue
            x, y = pad_world_xy(fp, pad)
            pad_layers = range(layers) if pad.get("th", False) else (
                [layers - 1] if int(fp.get("side", 0)) == 1 else [0])
            keys = [key(x, y, layer) for layer in pad_layers]
            for value in keys:
                find(value)
            for value in keys[1:]:
                union(keys[0], value)
            terminals.append((x, y, keys[0][2]))
    for trace in data.get("traces", []):
        if int(trace.get("net", -1)) != net_id:
            continue
        layer = int(trace.get("layer", 0))
        a = key(float(trace["ax_mm"]), float(trace["ay_mm"]), layer)
        b = key(float(trace["bx_mm"]), float(trace["by_mm"]), layer)
        union(a, b)
    for via in data.get("vias", []):
        if int(via.get("net", -1)) != net_id:
            continue
        lo = min(int(via.get("from", 0)), int(via.get("to", layers - 1)))
        hi = max(int(via.get("from", 0)), int(via.get("to", layers - 1)))
        keys = [key(float(via["x_mm"]), float(via["y_mm"]), layer)
                for layer in range(lo, hi + 1)]
        for value in keys[1:]:
            union(keys[0], value)
    grouped: dict[tuple[float, float, int], list[tuple[float, float, int]]] = {}
    for x, y, layer in terminals:
        grouped.setdefault(find(key(x, y, layer)), []).append((x, y, layer))
    return list(grouped.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--nets", required=True,
                        help="comma-separated net names")
    parser.add_argument("--max-pairs", type=int, default=300)
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    names = {item.strip() for item in args.nets.split(",") if item.strip()}
    net_table = {int(item["id"]): item for item in data.get("net_table", [])}
    class_table = {int(item["id"]): item for item in data.get("net_classes", [])}
    selected = [net_id for net_id, item in net_table.items() if item.get("name") in names]
    lib = _load_lib(); declare_route_fns(lib)
    lib.dc_net_islands.restype = __import__("ctypes").c_int32
    lib.dc_net_islands.argtypes = [__import__("ctypes").c_void_p,
                                   __import__("ctypes").c_int32]
    board = build_board(lib, data)
    all_traces = list(data.get("traces", [])); all_vias = list(data.get("vias", []))
    results = []
    for net_id in selected:
        item = net_table[net_id]
        nc = class_table.get(int(item.get("class_id", item.get("class", 0))), class_table.get(0, {}))
        width = nm(float(nc.get("trace_width_mm", 0.25)))
        clearance = nm(max(float(c.get("clearance_mm", 0.25))
                           for c in class_table.values()))
        via_d = nm(float(nc.get("via_diameter_mm", 0.7)))
        via_drill = nm(float(nc.get("via_drill_mm", 0.35)))
        before = int(lib.dc_net_islands(board, net_id))
        attempts = successes = 0
        rejected: set[tuple] = set()
        while attempts < args.max_pairs:
            current = int(lib.dc_net_islands(board, net_id))
            if current <= 1:
                break
            data["traces"] = all_traces; data["vias"] = all_vias
            groups = terminal_groups(data, net_id)
            candidates = []
            for left in range(len(groups)):
                for right in range(left + 1, len(groups)):
                    for start in groups[left]:
                        for goal in groups[right]:
                            pair = (round(start[0], 4), round(start[1], 4), start[2],
                                    round(goal[0], 4), round(goal[1], 4), goal[2])
                            if pair in rejected:
                                continue
                            candidates.append((math.hypot(start[0] - goal[0], start[1] - goal[1]),
                                               pair, start, goal))
            if not candidates:
                break
            _, pair, start, goal = min(candidates, key=lambda value: value[0])
            attempts += 1
            sx, sy, sl = start; gx, gy, gl = goal
            traces, vias = route_connection(lib, board, net_id, sx, sy, sl, gx, gy, gl,
                                             width, clearance, via_d, via_drill)
            if traces or vias:
                all_traces.extend(traces); all_vias.extend(vias); successes += 1
            else:
                rejected.add(pair)
        after = int(lib.dc_net_islands(board, net_id))
        results.append({"net": item.get("name"), "islands_before": before,
                        "islands_after": after, "attempts": attempts,
                        "successful_routes": successes})
    lib.dc_board_destroy(board)
    data["traces"] = all_traces; data["vias"] = all_vias
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": all(item["islands_after"] <= 1 for item in results),
                      "results": results}, indent=2, sort_keys=True))
    return 0 if all(item["islands_after"] <= 1 for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
