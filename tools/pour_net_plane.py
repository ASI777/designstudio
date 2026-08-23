#!/usr/bin/env python3
"""Generate clearance-aware native pour strokes and terminal stitching for a net."""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "swarm" / "agents"))
from drc_agent import _load_lib, build_board, nm  # type: ignore
from bga_reroute_agent import pad_world_xy  # type: ignore


class DcPourStroke(ctypes.Structure):
    _fields_ = [("ax", ctypes.c_int64), ("ay", ctypes.c_int64),
                ("bx", ctypes.c_int64), ("by", ctypes.c_int64),
                ("width", ctypes.c_int64)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--net", required=True)
    parser.add_argument("--layer", type=int, required=True)
    parser.add_argument("--line-width", type=float, default=1.0)
    parser.add_argument("--clearance", type=float, default=0.35)
    parser.add_argument("--stitch-only", action="store_true")
    parser.add_argument("--no-stitch", action="store_true")
    parser.add_argument("--allow-via-in-pad", action="store_true",
                        help="permit stitch vias centred on SMD pads (wicking/tombstone risk)")
    parser.add_argument("--replace-routes", action="store_true",
                        help="remove redundant non-pour routes for this plane net")
    args = parser.parse_args()
    data = json.loads(args.project.read_text(encoding="utf-8"))
    net_id = next((int(item["id"]) for item in data.get("net_table", [])
                   if str(item.get("name", "")) == args.net), None)
    if net_id is None:
        raise SystemExit(f"project has no {args.net} net")
    layers = int(data.get("copper_layers", 4))
    # Treat nearby vias as duplicates too: a second via 0.01 mm from an
    # existing one is still a duplicate (and a drill-to-drill violation).
    def has_via_near(x: float, y: float) -> bool:
        return any(math.hypot(float(v["x_mm"]) - x, float(v["y_mm"]) - y) < 0.4
                   for v in data.get("vias", []) if int(v.get("net", -1)) == net_id)
    vias_added = 0
    skipped_small_pad = 0
    for fp in ([] if args.no_stitch else data.get("footprints", [])):
        for pad in fp.get("pads", []):
            if int(pad.get("net", -1)) != net_id or pad.get("th", False):
                continue
            # Never drill a small SMD pad for stitching: the 0.3 mm drill in a
            # <=1.2 mm pad wicks solder and tombstones passives.  Large pads
            # (module thermal pads, connectors) still get centre stitches.
            if not args.allow_via_in_pad and min(float(pad.get("w_mm", 1)),
                                                 float(pad.get("h_mm", 1))) < 1.2:
                skipped_small_pad += 1
                continue
            x, y = pad_world_xy(fp, pad)
            if has_via_near(x, y):
                continue
            data.setdefault("vias", []).append({
                "x_mm": round(x, 4), "y_mm": round(y, 4), "dia_mm": 0.6,
                "drill_mm": 0.3, "net": net_id, "from": 0, "to": layers - 1,
                "via_type": "through"})
            vias_added += 1
    if args.stitch_only:
        args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        print(json.dumps({"ok": True, "net": args.net,
                          "stitch_vias_added": vias_added, "stitch_only": True},
                         sort_keys=True))
        return 0
    # Remove an older pour for the same net/layer before rebuilding the board.
    data["traces"] = [trace for trace in data.get("traces", [])
                      if not (int(trace.get("net", -1)) == net_id
                              and ((trace.get("pour", False)
                                    and int(trace.get("layer", -1)) == args.layer)
                                   or (args.replace_routes
                                       and not trace.get("pour", False))))]
    lib = _load_lib()
    lib.dc_pour_run.restype = ctypes.c_int32
    lib.dc_pour_run.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                                ctypes.c_int64, ctypes.c_int64,
                                ctypes.c_int64, ctypes.c_int64,
                                ctypes.c_int64, ctypes.c_int64]
    lib.dc_pour_zone_run.restype = ctypes.c_int32
    lib.dc_pour_zone_run.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                     ctypes.c_int64]
    lib.dc_pour_get.restype = ctypes.c_int32
    lib.dc_pour_get.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                ctypes.POINTER(DcPourStroke)]
    board = build_board(lib, data)
    zone = next((item for item in data.get("copper_zones", [])
                 if int(item.get("net", -1)) == net_id
                 and int(item.get("layer", -1)) == args.layer), None)
    if zone is not None:
        count = lib.dc_pour_zone_run(board, int(zone["id"]), nm(args.line_width))
    else:
        count = lib.dc_pour_run(board, net_id, args.layer, nm(args.line_width),
                                nm(args.clearance), nm(0.5), nm(0.5),
                                nm(float(data["board_width_mm"]) - 0.5),
                                nm(float(data["board_height_mm"]) - 0.5))
    if count < 0:
        lib.dc_board_destroy(board)
        raise SystemExit(f"native pour failed with status {count}")
    for index in range(count):
        stroke = DcPourStroke()
        if lib.dc_pour_get(board, index, ctypes.byref(stroke)) != 0:
            lib.dc_board_destroy(board)
            raise SystemExit("native pour result retrieval failed")
        data.setdefault("traces", []).append({
            "ax_mm": round(stroke.ax / 1_000_000, 6),
            "ay_mm": round(stroke.ay / 1_000_000, 6),
            "bx_mm": round(stroke.bx / 1_000_000, 6),
            "by_mm": round(stroke.by / 1_000_000, 6),
            "w_mm": round(stroke.width / 1_000_000, 6),
            "net": net_id, "layer": args.layer, "pour": True})
    lib.dc_board_destroy(board)
    if zone is None:
        data.setdefault("copper_zones", []).append({
            "id": 9100 + net_id, "name": f"{args.net} native poured plane",
            "net": net_id, "layer": args.layer, "clearance_mm": args.clearance,
            "min_island_area_mm2": 2.0, "require_connection": True,
            "source": "designstudio-native-pour", "source_revision": "1.0.0",
            "pts": [[0.5, 0.5], [float(data["board_width_mm"]) - 0.5, 0.5],
                    [float(data["board_width_mm"]) - 0.5,
                     float(data["board_height_mm"]) - 0.5],
                    [0.5, float(data["board_height_mm"]) - 0.5]]})
    args.project.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "net": args.net, "layer": args.layer,
                      "strokes": count, "stitch_vias_added": vias_added}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
