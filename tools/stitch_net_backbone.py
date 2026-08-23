#!/usr/bin/env python3
"""Connect every terminal of one net through an explicit routed backbone."""
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
    parser.add_argument("--net", required=True)
    parser.add_argument("--layer", type=int, required=True)
    parser.add_argument("--bus-y", type=float, required=True)
    parser.add_argument("--width", type=float, required=True)
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    net_id = next((int(item["id"]) for item in data.get("net_table", [])
                   if str(item.get("name", "")) == args.net), None)
    if net_id is None:
        raise SystemExit(f"project has no {args.net} net")
    if args.layer < 0 or args.layer >= int(data.get("copper_layers", 2)):
        raise SystemExit("backbone layer is outside the copper stack")
    terminals: list[tuple[float, float, float]] = []
    existing = {(round(float(v["x_mm"]), 4), round(float(v["y_mm"]), 4),
                 int(v.get("net", -1))) for v in data.get("vias", [])}
    vias_added = 0
    for fp in data.get("footprints", []):
        for pad in fp.get("pads", []):
            if int(pad.get("net", -1)) != net_id:
                continue
            x, y = world(fp, pad)
            local_x = float(pad.get("x_mm", 0))
            escape_x = float(fp["x_mm"]) + math.copysign(
                float(fp.get("body_w_mm", 2)) / 2 + 2.5,
                local_x if abs(local_x) > 1e-6 else 1.0)
            terminals.append((x, y, escape_x))
            if not pad.get("th", False):
                key = (round(x, 4), round(y, 4), net_id)
                if key not in existing:
                    data.setdefault("vias", []).append({
                        "x_mm": round(x, 4), "y_mm": round(y, 4),
                        "dia_mm": 0.7, "drill_mm": 0.35, "net": net_id,
                        "from": 0, "to": int(data.get("copper_layers", 4)) - 1,
                        "via_type": "through"})
                    existing.add(key); vias_added += 1
    if len(terminals) >= 2:
        xs = sorted({round(escape_x, 4) for _, _, escape_x in terminals})
        traces = data.setdefault("traces", [])
        traces.append({"ax_mm": xs[0], "ay_mm": args.bus_y,
                       "bx_mm": xs[-1], "by_mm": args.bus_y,
                       "w_mm": args.width, "net": net_id,
                       "layer": args.layer, "pour": False})
        for x, y, escape_x in terminals:
            traces.append({"ax_mm": round(x, 4), "ay_mm": round(y, 4),
                           "bx_mm": round(escape_x, 4), "by_mm": round(y, 4),
                           "w_mm": args.width, "net": net_id,
                           "layer": args.layer, "pour": False})
            traces.append({"ax_mm": round(escape_x, 4), "ay_mm": round(y, 4),
                           "bx_mm": round(escape_x, 4), "by_mm": args.bus_y,
                           "w_mm": args.width, "net": net_id,
                           "layer": args.layer, "pour": False})
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "net": args.net, "net_id": net_id,
                      "terminals": len(terminals), "vias_added": vias_added,
                      "layer": args.layer}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
