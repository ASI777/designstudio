#!/usr/bin/env python3
"""Add explicit same-net ground stitching and a digestable inner-plane zone."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def world(fp: dict, pad: dict) -> tuple[float, float]:
    angle = math.radians(float(fp.get("rot_deg", 0)))
    px, py = float(pad.get("x_mm", 0)), float(pad.get("y_mm", 0))
    return (float(fp["x_mm"]) + px * math.cos(angle) - py * math.sin(angle),
            float(fp["y_mm"]) + px * math.sin(angle) + py * math.cos(angle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--layer", type=int, default=1)
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    ground = next((int(net["id"]) for net in data.get("net_table", [])
                   if str(net.get("name", "")).upper() == "GND"), None)
    if ground is None:
        raise SystemExit("project has no GND net")
    width, height = float(data["board_width_mm"]), float(data["board_height_mm"])
    data["copper_zones"] = [zone for zone in data.get("copper_zones", [])
                            if not (int(zone.get("net", -1)) == ground
                                    and int(zone.get("layer", -1)) == args.layer)]
    data["copper_zones"].append({
        "id": 9001, "name": "GND stitched plane", "net": ground, "layer": args.layer,
        "clearance_mm": 0.35, "min_island_area_mm2": 2.0,
        "require_connection": True, "source": "designstudio-ground-stitcher",
        "source_revision": "1.0.0",
        "pts": [[0.5, 0.5], [width - 0.5, 0.5],
                [width - 0.5, height - 0.5], [0.5, height - 0.5]]
    })
    existing = {(round(float(v["x_mm"]), 4), round(float(v["y_mm"]), 4),
                 int(v.get("net", -1))) for v in data.get("vias", [])}
    added = 0
    terminals: list[tuple[float, float, float]] = []
    for fp in data.get("footprints", []):
        if int(fp.get("side", 0)) != 0:
            continue
        for pad in fp.get("pads", []):
            if int(pad.get("net", -1)) != ground or pad.get("th", False):
                if int(pad.get("net", -1)) == ground and pad.get("th", False):
                    x, y = world(fp, pad)
                    local_x = float(pad.get("x_mm", 0))
                    escape_x = float(fp["x_mm"]) + math.copysign(
                        float(fp.get("body_w_mm", 2)) / 2 + 2.5,
                        local_x if abs(local_x) > 1e-6 else 1.0)
                    terminals.append((x, y, escape_x))
                continue
            x, y = world(fp, pad)
            local_x = float(pad.get("x_mm", 0))
            escape_x = float(fp["x_mm"]) + math.copysign(
                float(fp.get("body_w_mm", 2)) / 2 + 2.5,
                local_x if abs(local_x) > 1e-6 else 1.0)
            terminals.append((x, y, escape_x))
            key = (round(x, 4), round(y, 4), ground)
            if key in existing:
                continue
            data.setdefault("vias", []).append({"x_mm": round(x, 4), "y_mm": round(y, 4),
                                                "dia_mm": 0.6, "drill_mm": 0.3,
                                                "net": ground, "from": 0,
                                                "to": int(data.get("copper_layers", 4)) - 1,
                                                "via_type": "through"})
            existing.add(key); added += 1
    # A deterministic inner-layer backbone makes connectivity explicit to the
    # native island solver; the zone remains the manufacturing plane intent.
    for policy in data.get("layer_policies", []):
        if int(policy.get("layer", -1)) == args.layer:
            policy["role"] = "mixed"
            policy["allow_routing"] = True
    bus_y = 4.0
    branch_width = 1.0
    for trace in data.get("traces", []):
        if int(trace.get("net", -1)) == ground and not trace.get("pour", False):
            trace["w_mm"] = max(float(trace.get("w_mm", 0.0)), branch_width)
    if terminals:
        x_values = sorted({round(escape_x, 4) for _, _, escape_x in terminals})
        data.setdefault("traces", []).append({
            "ax_mm": x_values[0], "ay_mm": bus_y,
            "bx_mm": x_values[-1], "by_mm": bus_y,
            "w_mm": branch_width, "net": ground, "layer": args.layer, "pour": False})
        for x, y, escape_x in terminals:
            data["traces"].append({"ax_mm": round(x, 4), "ay_mm": round(y, 4),
                                   "bx_mm": round(escape_x, 4), "by_mm": round(y, 4),
                                   "w_mm": branch_width, "net": ground,
                                   "layer": args.layer, "pour": False})
            data["traces"].append({"ax_mm": round(escape_x, 4), "ay_mm": round(y, 4),
                                   "bx_mm": round(escape_x, 4), "by_mm": bus_y,
                                   "w_mm": branch_width, "net": ground,
                                   "layer": args.layer, "pour": False})
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "ground_net": ground, "zone_layer": args.layer,
                      "stitch_vias_added": added}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
