#!/usr/bin/env python3
"""Move footprints while preserving routed endpoint attachment.

This is intentionally conservative: only trace endpoints or vias exactly at a
pad centre move with the footprint. Copper merely passing near/through the old
placement is never translated. Native connectivity and DRC must be rerun after
the operation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

EPS_MM = 1e-5


def pad_world(fp: dict, pad: dict, x_mm: float | None = None,
              y_mm: float | None = None) -> tuple[float, float]:
    angle = math.radians(float(fp.get("rot_deg", 0.0)))
    local_x = float(pad.get("x_mm", 0.0))
    local_y = float(pad.get("y_mm", 0.0))
    if int(fp.get("side", 0)) == 1:
        local_y = -local_y
    origin_x = float(fp["x_mm"]) if x_mm is None else x_mm
    origin_y = float(fp["y_mm"]) if y_mm is None else y_mm
    return (
        origin_x + local_x * math.cos(angle) - local_y * math.sin(angle),
        origin_y + local_x * math.sin(angle) + local_y * math.cos(angle),
    )


def same_point(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= EPS_MM


def parse_move(text: str) -> tuple[str, float, float]:
    parts = text.split(":")
    if len(parts) != 3 or not parts[0]:
        raise argparse.ArgumentTypeError("move must be REF:X_MM:Y_MM")
    try:
        return parts[0], float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("move coordinates must be numbers") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--move", action="append", type=parse_move, required=True,
                        metavar="REF:X_MM:Y_MM")
    args = parser.parse_args()

    data = json.loads(args.project.read_text(encoding="utf-8"))
    footprints = {str(fp.get("ref")): fp for fp in data.get("footprints", [])}
    requested = [item[0] for item in args.move]
    if len(set(requested)) != len(requested):
        raise SystemExit("each footprint may be moved only once")

    moved_endpoints = 0
    moved_vias = 0
    records = []
    for ref, new_x, new_y in args.move:
        fp = footprints.get(ref)
        if fp is None:
            raise SystemExit(f"unknown footprint: {ref}")
        old_x = float(fp["x_mm"])
        old_y = float(fp["y_mm"])
        if same_point((old_x, old_y), (new_x, new_y)):
            continue

        pad_moves: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
        for pad in fp.get("pads", []):
            pad_moves.append((
                int(pad.get("net", -1)),
                pad_world(fp, pad),
                pad_world(fp, pad, new_x, new_y),
            ))

        for trace in data.get("traces", []):
            trace_net = int(trace.get("net", -1))
            for prefix in ("a", "b"):
                point = (float(trace[f"{prefix}x_mm"]),
                         float(trace[f"{prefix}y_mm"]))
                matches = [new for net, old, new in pad_moves
                           if net == trace_net and same_point(point, old)]
                if len(matches) > 1:
                    raise SystemExit(
                        f"{ref}: ambiguous routed endpoint at {point}")
                if matches:
                    trace[f"{prefix}x_mm"] = round(matches[0][0], 6)
                    trace[f"{prefix}y_mm"] = round(matches[0][1], 6)
                    moved_endpoints += 1

        for via in data.get("vias", []):
            via_net = int(via.get("net", -1))
            point = (float(via["x_mm"]), float(via["y_mm"]))
            matches = [new for net, old, new in pad_moves
                       if net == via_net and same_point(point, old)]
            if len(matches) > 1:
                raise SystemExit(f"{ref}: ambiguous via at {point}")
            if matches:
                via["x_mm"] = round(matches[0][0], 6)
                via["y_mm"] = round(matches[0][1], 6)
                moved_vias += 1

        fp["x_mm"] = new_x
        fp["y_mm"] = new_y
        records.append({"ref": ref, "from_mm": [old_x, old_y],
                        "to_mm": [new_x, new_y]})

    history = data.setdefault("placement_state", {}).setdefault(
        "route_preserving_moves", [])
    history.extend(records)
    data["placement_state"]["route_endpoint_attachment_tolerance_mm"] = EPS_MM

    temporary = args.project.with_suffix(args.project.suffix + ".move.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, args.project)
    print(json.dumps({"ok": True, "moves": records,
                      "moved_trace_endpoints": moved_endpoints,
                      "moved_vias": moved_vias}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
