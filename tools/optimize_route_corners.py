#!/usr/bin/env python3
"""Replace true two-segment 90-degree PCB bends with deterministic miters."""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import os
from pathlib import Path

EPS = 1e-7


def point_key(x: float, y: float) -> tuple[float, float]:
    return round(float(x), 6), round(float(y), 6)


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


def point_in_pad(point: tuple[float, float], fp: dict, pad: dict) -> bool:
    cx, cy = pad_world(fp, pad)
    angle = -math.radians(float(fp.get("rot_deg", 0.0)))
    dx, dy = point[0] - cx, point[1] - cy
    local_x = dx * math.cos(angle) - dy * math.sin(angle)
    local_y = dx * math.sin(angle) + dy * math.cos(angle)
    return (abs(local_x) <= float(pad.get("w_mm", 0.0)) / 2 + EPS
            and abs(local_y) <= float(pad.get("h_mm", 0.0)) / 2 + EPS)


def endpoint(trace: dict, prefix: str) -> tuple[float, float]:
    return point_key(trace[f"{prefix}x_mm"], trace[f"{prefix}y_mm"])


def set_endpoint(trace: dict, prefix: str, point: tuple[float, float]) -> None:
    trace[f"{prefix}x_mm"] = round(point[0], 6)
    trace[f"{prefix}y_mm"] = round(point[1], 6)


def other_endpoint(trace: dict, corner: tuple[float, float]
                   ) -> tuple[str, tuple[float, float]]:
    if endpoint(trace, "a") == corner:
        return "a", endpoint(trace, "b")
    if endpoint(trace, "b") == corner:
        return "b", endpoint(trace, "a")
    raise ValueError("trace does not terminate at corner")


def find_corner(data: dict, skip: set[tuple] | None = None
                ) -> tuple[int, int, tuple[float, float]] | None:
    traces = data.get("traces", [])
    skip = skip or set()
    incidence: dict[tuple[int, int, float, float], list[int]] = (
        collections.defaultdict(list))
    for index, trace in enumerate(traces):
        if trace.get("pour", False) or int(trace.get("net", -1)) < 0:
            continue
        net = int(trace["net"])
        layer = int(trace["layer"])
        for point in (endpoint(trace, "a"), endpoint(trace, "b")):
            incidence[(net, layer, point[0], point[1])].append(index)

    for (net, layer, x, y), indices in sorted(incidence.items()):
        if len(indices) != 2 or (net, layer, x, y) in skip:
            continue
        corner = (x, y)
        if any(point_in_pad(corner, fp, pad)
               for fp in data.get("footprints", [])
               for pad in fp.get("pads", [])
               if int(pad.get("net", -1)) == net):
            continue
        first = traces[indices[0]]
        second = traces[indices[1]]
        _, a = other_endpoint(first, corner)
        _, b = other_endpoint(second, corner)
        va = (a[0] - x, a[1] - y)
        vb = (b[0] - x, b[1] - y)
        dot = va[0] * vb[0] + va[1] * vb[1]
        cross = va[0] * vb[1] - va[1] * vb[0]
        if abs(dot) <= EPS and abs(cross) > EPS:
            return indices[0], indices[1], corner
    return None


def interpolate_from_corner(corner: tuple[float, float],
                            other: tuple[float, float],
                            distance: float) -> tuple[float, float]:
    dx, dy = other[0] - corner[0], other[1] - corner[1]
    length = math.hypot(dx, dy)
    return corner[0] + distance * dx / length, corner[1] + distance * dy / length


def miter_once(data: dict, first_index: int, second_index: int,
               corner: tuple[float, float], setback_mm: float) -> None:
    traces = data["traces"]
    first = traces[first_index]
    second = traces[second_index]
    if (int(first["net"]) != int(second["net"])
            or int(first["layer"]) != int(second["layer"])):
        raise ValueError("corner traces do not share net/layer")
    if abs(float(first["w_mm"]) - float(second["w_mm"])) > EPS:
        raise ValueError("corner traces do not share width")

    first_prefix, first_other = other_endpoint(first, corner)
    second_prefix, second_other = other_endpoint(second, corner)
    first_length = math.dist(corner, first_other)
    second_length = math.dist(corner, second_other)

    setback = min(setback_mm, first_length * 0.25, second_length * 0.25)
    if setback <= EPS:
        raise ValueError("corner is too short to miter")
    first_trim = interpolate_from_corner(corner, first_other, setback)
    second_trim = interpolate_from_corner(corner, second_other, setback)
    set_endpoint(first, first_prefix, first_trim)
    set_endpoint(second, second_prefix, second_trim)
    diagonal = copy.deepcopy(first)
    set_endpoint(diagonal, "a", first_trim)
    set_endpoint(diagonal, "b", second_trim)
    diagonal.pop("uuid", None)
    traces.append(diagonal)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--setback", type=float, default=0.30)
    parser.add_argument("--max-corners", type=int, default=10000)
    args = parser.parse_args()
    if args.setback <= 0 or args.max_corners <= 0:
        raise SystemExit("setback and max-corners must be positive")

    data = json.loads(args.project.read_text(encoding="utf-8"))
    optimized = 0
    unmiterable: set[tuple] = set()
    while optimized < args.max_corners:
        found = find_corner(data, unmiterable)
        if found is None:
            break
        first_index, second_index, corner = found
        traces = data["traces"]
        # A corner joining segments of different widths cannot take a
        # constant-width miter; skip it instead of crashing the whole pass.
        if abs(float(traces[first_index]["w_mm"])
               - float(traces[second_index]["w_mm"])) > EPS:
            unmiterable.add((int(traces[first_index]["net"]),
                             int(traces[first_index]["layer"]),
                             corner[0], corner[1]))
            continue
        try:
            miter_once(data, *found, args.setback)
        except ValueError:
            unmiterable.add((int(traces[first_index]["net"]),
                             int(traces[first_index]["layer"]),
                             corner[0], corner[1]))
            continue
        optimized += 1
    remaining = find_corner(data, unmiterable)
    if remaining is not None:
        raise SystemExit("corner optimization limit reached")

    data.setdefault("routing_release_operations", []).append({
        "tool": "tools/optimize_route_corners.py",
        "setback_mm": args.setback,
        "optimized_corners": optimized,
        "skipped_unmiterable_corners": len(unmiterable),
        "remaining_right_angle_corners": 0,
    })
    temporary = args.project.with_suffix(args.project.suffix + ".corners.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, args.project)
    print(json.dumps({"ok": True, "optimized_corners": optimized,
                      "skipped_unmiterable_corners": len(unmiterable),
                      "remaining_right_angle_corners": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
