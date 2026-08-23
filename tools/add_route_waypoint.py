#!/usr/bin/env python3
"""Split one exact routed segment through a deterministic waypoint."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path


def parse_spec(text: str) -> tuple[int, int, float, float, float, float, float, float]:
    parts = text.split(":")
    if len(parts) != 8:
        raise argparse.ArgumentTypeError(
            "spec must be NET:LAYER:AX:AY:BX:BY:WAYPOINT_X:WAYPOINT_Y")
    try:
        return (int(parts[0]), int(parts[1]),
                *(float(value) for value in parts[2:]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("spec fields must be numeric") from exc


def point(x: float, y: float) -> tuple[float, float]:
    return round(float(x), 6), round(float(y), 6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--segment", action="append", type=parse_spec,
                        required=True,
                        metavar="NET:LAYER:AX:AY:BX:BY:WX:WY")
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    operations = []
    for net, layer, ax, ay, bx, by, wx, wy in args.segment:
        a, b, waypoint = point(ax, ay), point(bx, by), point(wx, wy)
        matches = []
        for index, trace in enumerate(data.get("traces", [])):
            if int(trace.get("net", -1)) != net \
                    or int(trace.get("layer", -1)) != layer:
                continue
            ta = point(trace["ax_mm"], trace["ay_mm"])
            tb = point(trace["bx_mm"], trace["by_mm"])
            if {ta, tb} == {a, b}:
                matches.append(index)
        if len(matches) != 1:
            raise SystemExit(
                f"expected one matching segment {net}:{layer}:{a}:{b}; "
                f"found {len(matches)}")
        index = matches[0]
        original = data["traces"][index]
        first = copy.deepcopy(original)
        second = copy.deepcopy(original)
        first.pop("uuid", None)
        second.pop("uuid", None)
        first.update({"ax_mm": a[0], "ay_mm": a[1],
                      "bx_mm": waypoint[0], "by_mm": waypoint[1]})
        second.update({"ax_mm": waypoint[0], "ay_mm": waypoint[1],
                       "bx_mm": b[0], "by_mm": b[1]})
        data["traces"][index:index + 1] = [first, second]
        operations.append({"net": net, "layer": layer, "from": [*a, *b],
                           "waypoint_mm": list(waypoint)})

    data.setdefault("routing_release_operations", []).append({
        "tool": "tools/add_route_waypoint.py",
        "operations": operations,
    })
    temporary = args.project.with_suffix(args.project.suffix + ".waypoint.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, args.project)
    print(json.dumps({"ok": True, "operations": operations}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
