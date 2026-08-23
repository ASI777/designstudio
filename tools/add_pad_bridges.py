#!/usr/bin/env python3
"""Add deterministic same-net pad-to-pad copper bridges."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path


def pad_world(fp: dict, pad: dict) -> tuple[float, float]:
    angle = math.radians(float(fp.get("rot_deg", 0.0)))
    x = float(pad.get("x_mm", 0.0))
    y = float(pad.get("y_mm", 0.0))
    if int(fp.get("side", 0)) == 1:
        y = -y
    return (
        float(fp["x_mm"]) + x * math.cos(angle) - y * math.sin(angle),
        float(fp["y_mm"]) + x * math.sin(angle) + y * math.cos(angle),
    )


def parse_bridge(text: str) -> tuple[str, str, str, int, float]:
    parts = text.split(":")
    if len(parts) != 5:
        raise argparse.ArgumentTypeError(
            "bridge must be REF:PAD_A:PAD_B:LAYER:WIDTH_MM")
    try:
        return parts[0], parts[1], parts[2], int(parts[3]), float(parts[4])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "bridge layer/width must be numeric") from exc


def trace_key(trace: dict) -> tuple:
    a = (round(float(trace["ax_mm"]), 6), round(float(trace["ay_mm"]), 6))
    b = (round(float(trace["bx_mm"]), 6), round(float(trace["by_mm"]), 6))
    return (int(trace["net"]), int(trace["layer"]), min(a, b), max(a, b),
            round(float(trace["w_mm"]), 6))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--bridge", action="append", type=parse_bridge,
                        required=True,
                        metavar="REF:PAD_A:PAD_B:LAYER:WIDTH_MM")
    parser.add_argument("--required-current", type=float, default=None,
                        help="allocated branch current in amperes")
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    footprints = {str(fp.get("ref")): fp for fp in data.get("footprints", [])}
    existing = {trace_key(trace) for trace in data.get("traces", [])}
    added = []
    for ref, first_name, second_name, layer, width in args.bridge:
        fp = footprints.get(ref)
        if fp is None:
            raise SystemExit(f"unknown footprint: {ref}")
        pads = {str(pad.get("name")): pad for pad in fp.get("pads", [])}
        if first_name not in pads or second_name not in pads:
            raise SystemExit(
                f"{ref}: unknown pad {first_name!r} or {second_name!r}")
        first, second = pads[first_name], pads[second_name]
        net = int(first.get("net", -1))
        if net < 0 or int(second.get("net", -1)) != net:
            raise SystemExit(f"{ref}: bridge pads must share an assigned net")
        if layer < 0 or layer >= int(data.get("copper_layers", 0)):
            raise SystemExit(f"{ref}: bridge layer is outside the stackup")
        if width <= 0:
            raise SystemExit("bridge width must be positive")
        a = pad_world(fp, first)
        b = pad_world(fp, second)
        item = {
            "ax_mm": round(a[0], 6), "ay_mm": round(a[1], 6),
            "bx_mm": round(b[0], 6), "by_mm": round(b[1], 6),
            "w_mm": width, "layer": layer, "net": net, "pour": False,
            "min_width_override_mm": width,
        }
        if args.required_current is not None:
            if args.required_current <= 0:
                raise SystemExit("required current must be positive")
            item["required_current_a"] = args.required_current
        key = trace_key(item)
        if key not in existing:
            data.setdefault("traces", []).append(item)
            existing.add(key)
            added.append({"ref": ref, "pads": [first_name, second_name],
                          "net": net, "layer": layer, "width_mm": width})

    data.setdefault("routing_release_operations", []).append({
        "tool": "tools/add_pad_bridges.py",
        "fixed_pad_bridges": added,
    })
    temporary = args.project.with_suffix(args.project.suffix + ".bridges.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, args.project)
    print(json.dumps({"ok": True, "added": added}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
