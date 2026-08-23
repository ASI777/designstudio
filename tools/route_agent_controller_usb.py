#!/usr/bin/env python3
"""Install the acceptance controller's USB 2.0 differential route.

The controlled run stays on F.Cu over the continuous In1.GND plane, uses
45-degree bends, has no signal vias, and only necks down at the 0.2 mm-wide
USON ESD terminals.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    r = math.radians(degrees)
    return x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)


def pad_world(fp: dict, pad: dict) -> tuple[float, float]:
    x = float(pad.get("x_mm", 0)); y = float(pad.get("y_mm", 0))
    if int(fp.get("side", 0)) == 1:
        y = -y
    x, y = rotate(x, y, float(fp.get("rot_deg", 0)))
    return float(fp["x_mm"]) + x, float(fp["y_mm"]) + y


def pad(data: dict, ref: str, name: str) -> tuple[dict, dict]:
    fp = next(item for item in data["footprints"] if item["ref"] == ref)
    return fp, next(item for item in fp["pads"] if item["name"] == name)


def escape(fp: dict, item: dict, run: float) -> tuple[float, float]:
    local_x = float(item["x_mm"]); local_y = float(item["y_mm"])
    body_w = float(fp["body_w_mm"]); body_h = float(fp["body_h_mm"])
    dx = body_w / 2 - abs(local_x); dy = body_h / 2 - abs(local_y)
    if dx <= dy:
        target_x = math.copysign(body_w / 2 + run, local_x if local_x else 1)
        target_y = local_y
    else:
        target_x = local_x
        target_y = math.copysign(body_h / 2 + run, local_y if local_y else 1)
    if int(fp.get("side", 0)) == 1:
        target_y = -target_y
    target_x, target_y = rotate(target_x, target_y, float(fp.get("rot_deg", 0)))
    return float(fp["x_mm"]) + target_x, float(fp["y_mm"]) + target_y


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--clear-all", action="store_true",
                        help="start from footprints only before installing the pair")
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    ids = {item["name"]: int(item["id"]) for item in data["net_table"]}
    plus = ids["USB_D+"]; minus = ids["USB_D-"]
    cc1 = ids["CC1"]; cc2 = ids["CC2"]
    owned_nets = {plus, minus, cc1, cc2}
    data["traces"] = ([] if args.clear_all else
                      [t for t in data.get("traces", [])
                       if int(t.get("net", -1)) not in owned_nets])
    data["vias"] = ([] if args.clear_all else
                    [v for v in data.get("vias", [])
                     if int(v.get("net", -1)) not in owned_nets])
    traces: list[dict] = []; vias: list[dict] = []

    def trace(a: tuple[float, float], b: tuple[float, float], net: int, layer: int,
              width: float = .22, neck: bool = False) -> None:
        if math.dist(a, b) < 1e-6:
            return
        item = {"ax_mm": round(a[0], 4), "ay_mm": round(a[1], 4),
                "bx_mm": round(b[0], 4), "by_mm": round(b[1], 4),
                "w_mm": width, "net": net, "layer": layer, "pour": False}
        if neck:
            item["min_width_override_mm"] = width
        traces.append(item)

    def polyline(points: list[tuple[float, float]], net: int,
                 width: float = .22, neck_segments: set[int] | None = None,
                 layer: int = 0) -> None:
        neck_segments = neck_segments or set()
        for index, (a, b) in enumerate(zip(points, points[1:])):
            segment_width = .15 if index in neck_segments else width
            trace(a, b, net, layer, segment_width, index in neck_segments)

    def via(point: tuple[float, float], net: int) -> None:
        vias.append({"x_mm": round(point[0], 4), "y_mm": round(point[1], 4),
                     "dia_mm": .60, "drill_mm": .30, "net": net,
                     "from": 0, "to": int(data["copper_layers"]) - 1,
                     "via_type": "through"})

    j1p_contacts = sorted((pad_world(*pad(data, "J1", number))
                           for number in ("4", "13")))
    j1n_contacts = sorted((pad_world(*pad(data, "J1", number))
                           for number in ("5", "12")))
    u2p = pad_world(*pad(data, "U2", "5"))
    u2n = pad_world(*pad(data, "U2", "1"))
    u1p = pad_world(*pad(data, "U1", "14"))
    u1n = pad_world(*pad(data, "U1", "13"))

    def path_length(points: list[tuple[float, float]]) -> float:
        return sum(math.dist(a, b) for a, b in zip(points, points[1:]))

    # With the opposing Type-C row physically mirrored, each duplicate USB2
    # contact is now collinear.  The pair routes directly between connector
    # and controller; U2 is a shunt protector on two short package neck-downs.
    if abs(j1p_contacts[0][1] - j1p_contacts[1][1]) > 1e-4 \
            or abs(j1n_contacts[0][1] - j1n_contacts[1][1]) > 1e-4:
        raise ValueError("Type-C duplicate D+/D- contacts are not physically aligned")
    p_contact = max(j1p_contacts)
    n_contact = max(j1n_contacts)

    # Main pair: 0.62 mm centre pitch = 0.22 mm copper + 0.40 mm gap.
    # The ESP32 escape and connector approach use only 45-degree bends.  U2 is
    # rotated so its outer IO pads straddle the pair: D+ remains on the left
    # corridor, while D- widens briefly to the right-side shunt pad.
    p_corridor_x = u2p[0]
    n_corridor_x = p_corridor_x + .62
    n_shoulder = (121.90, u1n[1])
    p_shoulder = (121.90, u1p[1])
    n_entry = (n_corridor_x, n_shoulder[1] + (n_corridor_x - n_shoulder[0]))
    p_entry = (p_corridor_x, p_shoulder[1] + (p_corridor_x - p_shoulder[0]))
    n_exit = (n_corridor_x, n_contact[1] - (n_corridor_x - n_contact[0]))
    p_exit = (p_corridor_x, p_contact[1] - (p_corridor_x - p_contact[0]))
    p_local_x = u2p[0] - .60
    n_local_x = u2n[0] + .60
    p_widen = p_corridor_x - p_local_x
    n_widen = n_local_x - n_corridor_x
    if p_widen <= 0 or n_widen <= 0:
        raise ValueError("rotated ESD array does not straddle the USB pair")
    p_esd_top = (p_local_x, u2p[1] - 1.0)
    p_esd_bottom = (p_local_x, u2p[1] + 1.0)
    n_esd_top = (n_local_x, u2n[1] - 1.0)
    n_esd_bottom = (n_local_x, u2n[1] + 1.0)
    p_pre = (p_corridor_x, p_esd_top[1] - p_widen)
    p_post = (p_corridor_x, p_esd_bottom[1] + p_widen)
    n_pre = (n_corridor_x, n_esd_top[1] - n_widen)
    n_post = (n_corridor_x, n_esd_bottom[1] + n_widen)
    p_main = [u1p, p_shoulder, p_entry, p_pre, p_esd_top,
              p_esd_bottom, p_post, p_exit, p_contact]
    n_main = [u1n, n_shoulder, n_entry, n_pre, n_esd_top,
              n_esd_bottom, n_post, n_exit, n_contact]

    # The D+ member is geometrically shorter because the controller and
    # connector pin pitches differ and D- widens through U2.  Add one smooth
    # leftward trombone while preserving the 0.62 mm base pitch.
    p_total = math.dist(*j1p_contacts) + path_length(p_main)
    n_total = math.dist(*j1n_contacts) + path_length(n_main)
    extra = n_total - p_total
    if extra < 0:
        raise ValueError("USB pair geometry unexpectedly makes D+ longer")
    tune_start = (p_corridor_x, 42.6)
    tune_end = (p_corridor_x, 44.6)
    if not (p_post[1] < tune_start[1] < tune_end[1] < p_exit[1]):
        raise ValueError("USB length-tuning section is outside the pair corridor")
    half_vertical = (tune_end[1] - tune_start[1]) / 2
    lateral = math.sqrt((half_vertical + extra / 2) ** 2
                        - half_vertical ** 2)
    tune_peak = (p_corridor_x - lateral,
                 (tune_start[1] + tune_end[1]) / 2)
    if tune_peak[0] - .11 - .25 <= 0:
        raise ValueError("USB length tuning violates the board-edge setback")
    p_main = [u1p, p_shoulder, p_entry, p_pre, p_esd_top,
              p_esd_bottom, p_post, tune_start, tune_peak,
              tune_end, p_exit, p_contact]

    polyline(j1p_contacts, plus)
    polyline(j1n_contacts, minus)
    polyline(p_main, plus)
    polyline(n_main, minus)
    polyline([u2p, (p_local_x, u2p[1])], plus)
    polyline([u2n, (n_local_x, u2n[1])], minus)

    # Both Type-C configuration channels are reserved with the USB breakout so
    # the later power/signal routers cannot close their only legal exits.
    cc1_start = pad_world(*pad(data, "J1", "3"))
    cc2_start = pad_world(*pad(data, "J1", "14"))
    cc1_end = pad_world(*pad(data, "R1", "1"))
    cc2_end = pad_world(*pad(data, "R2", "1"))
    # The CC contacts are through-hole, so their low-speed pull-down routes
    # leave on B.Cu.  This avoids crossing the top-layer controlled pair.
    cc1_via = (124.0, cc1_end[1])
    bottom = int(data["copper_layers"]) - 1
    polyline([cc1_start, (124.8, cc1_start[1] + (124.8 - cc1_start[0])),
              (124.8, cc1_via[1] - .8), cc1_via],
             cc1, width=.20, layer=bottom)
    via(cc1_via, cc1)
    polyline([cc1_via, cc1_end], cc1, width=.20)
    polyline([cc2_start, cc2_end], cc2, width=.20)

    # The configuration lines are also ESD-protected through U2's two spare IO
    # channels (previously left unconnected at the receptacle).  Both taps are
    # routed so they cannot cross each other or the controlled pair:
    #   CC1 hugs the pair corridor's east edge on B.Cu and reaches J1.3 from
    #     the east; its F.Cu stub slips south of the D- spur, between the
    #     D- diagonal and the U2 NC pads.
    #   CC2 loops around the connector's east side on B.Cu and reaches J1.14
    #     from the west, never sharing a channel with CC1.
    u2_cc1 = pad_world(*pad(data, "U2", "2"))
    u2_cc2 = pad_world(*pad(data, "U2", "4"))
    # CC1: the F.Cu stub slips south of the D- spur and between the D-
    # diagonal and the U2 NC pads, then B.Cu straight to the J1.3 pad.
    cc1_hop = (124.6, 40.2)
    polyline([u2_cc1, (u2_cc1[0], 40.5), cc1_hop], cc1, width=.20)
    via(cc1_hop, cc1)
    polyline([cc1_hop, (cc1_hop[0], cc1_start[1]), cc1_start],
             cc1, width=.20, layer=bottom)
    # CC2: the J1 pad columns and the VBUS escape are impenetrable between
    # y=47.5 and y=52.5, so the tap loops around them on B.Cu and joins the
    # existing F.Cu CC2 run at a merging via west of the VBUS backbone.
    cc2_hop = (123.3, 39.9)
    cc2_join = (115.0, 48.725)
    polyline([u2_cc2, cc2_hop], cc2, width=.20)
    via(cc2_hop, cc2)
    polyline([cc2_hop, (125.3, 39.9), (125.3, 53.0), (115.0, 53.0), cc2_join],
             cc2, width=.20, layer=bottom)
    via(cc2_join, cc2)

    def length(net: int) -> float:
        return sum(math.hypot(t["bx_mm"] - t["ax_mm"], t["by_mm"] - t["ay_mm"])
                   for t in traces if t["net"] == net)

    data["traces"].extend(traces); data["vias"].extend(vias)
    data.setdefault("routing_provenance", []).append({
        "kind": "usb-differential-pair", "generator": "tools/route_agent_controller_usb.py",
        "width_mm": .22, "gap_mm": .40, "reference_dielectric_mm": .10,
        "plus_length_mm": round(length(plus), 4), "minus_length_mm": round(length(minus), 4)})
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "traces": len(traces), "vias": len(vias),
                      "plus_length_mm": round(length(plus), 4),
                      "minus_length_mm": round(length(minus), 4)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
