#!/usr/bin/env python3
"""Deterministically join remaining net islands with minimum-distance chords.

This is a bounded fallback after obstacle-aware routing.  It changes only nets
explicitly named by the caller and never suppresses DRC; the resulting chords
must still pass the native clearance engine before release.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from repair_net_connectivity import terminal_groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--nets", required=True)
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    selected = {value.strip() for value in args.nets.split(",") if value.strip()}
    net_table = {int(item["id"]): item for item in data.get("net_table", [])}
    classes = {int(item["id"]): item for item in data.get("net_classes", [])}
    layers = int(data.get("copper_layers", 4))
    results = []
    for net_id, net in net_table.items():
        if net.get("name") not in selected:
            continue
        cls = classes.get(int(net.get("class", net.get("class_id", 0))), classes.get(0, {}))
        width = float(cls.get("trace_width_mm", .2))
        before = len(terminal_groups(data, net_id))
        added_traces = added_vias = 0
        while True:
            groups = terminal_groups(data, net_id)
            if len(groups) <= 1:
                break
            choices = []
            for left in range(len(groups)):
                for right in range(left + 1, len(groups)):
                    for a in groups[left]:
                        for b in groups[right]:
                            choices.append((math.hypot(a[0] - b[0], a[1] - b[1]), a, b))
            _, a, b = min(choices, key=lambda item: item[0])
            ax, ay, al = a; bx, by, bl = b
            data.setdefault("traces", []).append({
                "ax_mm": round(ax, 4), "ay_mm": round(ay, 4),
                "bx_mm": round(bx, 4), "by_mm": round(by, 4),
                "w_mm": width, "net": net_id, "layer": al, "pour": False})
            added_traces += 1
            if al != bl:
                data.setdefault("vias", []).append({
                    "x_mm": round(bx, 4), "y_mm": round(by, 4),
                    "dia_mm": max(.6, float(cls.get("via_dia_mm", .6))),
                    "drill_mm": max(.3, float(cls.get("via_drill_mm", .3))),
                    "net": net_id, "from": min(al, bl), "to": max(al, bl),
                    "via_type": "through"})
                added_vias += 1
            # Guard against malformed coordinates that fail to merge.
            after_groups = terminal_groups(data, net_id)
            if len(after_groups) >= len(groups):
                raise RuntimeError(f"{net.get('name')} direct stitch did not merge islands")
        results.append({"net": net.get("name"), "islands_before": before,
                        "islands_after": len(terminal_groups(data, net_id)),
                        "traces_added": added_traces, "vias_added": added_vias})
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": all(item["islands_after"] <= 1 for item in results),
                      "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
