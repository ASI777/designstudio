#!/usr/bin/env python3
"""bga_reroute_agent.py — HDI-aware BGA escape + full re-route.

Phase 0: EscapePlanner port (mirrors EscapePlanner.cs)
  For every BGA component (>= 16 pads in a grid):
    • Ring 0-1 (outer 2 rows): straight escape stubs outward on the top layer.
    • Ring 2+: dog-bone microvia at the diagonal grid cell (the only spot that
      clears all four neighbours), then a stub escape on an inner signal layer
      assigned by ring depth.
  Via size is derived from the pitch via HdiPlanner math:
    viaDia = min(pitch/2, smallest_pad),  drill = max(LaserMin, viaDia*0.4)
  The escape endpoints (stub tips) are registered as the "pad" for each BGA
  net so that the A* routing phase never enters the BGA pad field.

Phase 1: A* net routing
  Clear all pre-existing routes, then route non-BGA nets (and BGA nets between
  their escape endpoints) using dc_route_run with 0.1 mm grid.

Usage:
  python3 bga_reroute_agent.py <project.dsproj>
"""
from __future__ import annotations
import ctypes, json, math, sys, os
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
from drc_agent import (
    _load_lib, build_board, nm, DcPadDef, DcDrcViolation, RULE_NAMES,
    drc_options_from_project,
)

NM_PER_MM = 1_000_000
MM_PER_NM  = 1.0 / NM_PER_MM

LASER_DRILL_MIN_MM = 0.10   # minimum laser microvia drill (matches HdiPlanner.LaserDrillMinMm)
MIN_ANNULAR_MM     = 0.05   # HdiPlanner.MinAnnularMm


# ── Routing structs (mirror c_api.h) ──────────────────────────────────────────

class DcRouteRequest(ctypes.Structure):
    _fields_ = [
        ("net_id",          ctypes.c_int32),
        ("sx",              ctypes.c_int64),
        ("sy",              ctypes.c_int64),
        ("start_layer",     ctypes.c_int32),
        ("gx",              ctypes.c_int64),
        ("gy",              ctypes.c_int64),
        ("goal_layer",      ctypes.c_int32),
        ("trace_width",     ctypes.c_int64),
        ("clearance",       ctypes.c_int64),
        ("grid_step",       ctypes.c_int64),
        ("allow_vias",      ctypes.c_int32),
        ("allow_microvia",  ctypes.c_int32),
        ("via_diameter",    ctypes.c_int64),
        ("via_drill",       ctypes.c_int64),
        ("via_cost_mm",     ctypes.c_double),
        ("max_expansions",  ctypes.c_int64),
        ("min_drill_to_drill", ctypes.c_int64),
    ]

class DcRouteRequestV2(ctypes.Structure):
    _fields_ = [
        ("abi_version",       ctypes.c_uint32),
        ("struct_size",       ctypes.c_uint32),
        ("net_id",            ctypes.c_int32),
        ("reserved",          ctypes.c_int32),
        ("sx",                ctypes.c_int64),
        ("sy",                ctypes.c_int64),
        ("start_layer",       ctypes.c_int32),
        ("start_reserved",    ctypes.c_int32),
        ("gx",                ctypes.c_int64),
        ("gy",                ctypes.c_int64),
        ("goal_layer",        ctypes.c_int32),
        ("goal_reserved",     ctypes.c_int32),
        ("trace_width",       ctypes.c_int64),
        ("clearance",         ctypes.c_int64),
        ("min_copper_to_edge", ctypes.c_int64),
        ("grid_step",         ctypes.c_int64),
        ("allow_vias",        ctypes.c_int32),
        ("allow_microvia",    ctypes.c_int32),
        ("via_diameter",      ctypes.c_int64),
        ("via_drill",         ctypes.c_int64),
        ("via_cost_mm",       ctypes.c_double),
        ("max_expansions",    ctypes.c_int64),
        ("min_drill_to_drill", ctypes.c_int64),
    ]

class DcRoutePoint(ctypes.Structure):
    _fields_ = [
        ("x",     ctypes.c_int64),
        ("y",     ctypes.c_int64),
        ("layer", ctypes.c_int32),
        ("_pad",  ctypes.c_int32),
    ]

class DcRouteVia(ctypes.Structure):
    _fields_ = [
        ("x",          ctypes.c_int64),
        ("y",          ctypes.c_int64),
        ("from_layer", ctypes.c_int32),
        ("to_layer",   ctypes.c_int32),
    ]


def declare_route_fns(lib) -> None:
    lib.dc_route_run.restype  = ctypes.c_int32
    lib.dc_route_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(DcRouteRequest)]
    lib.dc_route_run_v2.restype = ctypes.c_int32
    lib.dc_route_run_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(DcRouteRequestV2)]
    lib.dc_route_point_count.restype  = ctypes.c_int32
    lib.dc_route_point_count.argtypes = [ctypes.c_void_p]
    lib.dc_route_get_points.restype  = ctypes.c_int32
    lib.dc_route_get_points.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(DcRoutePoint), ctypes.c_int32]
    lib.dc_route_via_count.restype  = ctypes.c_int32
    lib.dc_route_via_count.argtypes = [ctypes.c_void_p]
    lib.dc_route_get_vias.restype  = ctypes.c_int32
    lib.dc_route_get_vias.argtypes = [ctypes.c_void_p,
                                      ctypes.POINTER(DcRouteVia), ctypes.c_int32]
    lib.dc_trace_add.restype  = ctypes.c_uint64
    lib.dc_trace_add.argtypes = [ctypes.c_void_p,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int32,
                                 ctypes.c_int32, ctypes.c_int32]
    lib.dc_trace_min_width_set.restype = ctypes.c_int32
    lib.dc_trace_min_width_set.argtypes = [ctypes.c_void_p, ctypes.c_uint64,
                                           ctypes.c_int64]
    lib.dc_via_add.restype  = ctypes.c_uint64
    lib.dc_via_add.argtypes = [ctypes.c_void_p,
                               ctypes.c_int64, ctypes.c_int64,
                               ctypes.c_int64, ctypes.c_int64,
                               ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]


# ── Geometry helpers ──────────────────────────────────────────────────────────

def pad_world_xy(fp: dict, p: dict) -> tuple[float, float]:
    rad = math.radians(fp.get("rot_deg", 0))
    px, py = p.get("x_mm", 0), p.get("y_mm", 0)
    if int(fp.get("side", 0)) == 1:
        py = -py
    wx = fp["x_mm"] + px * math.cos(rad) - py * math.sin(rad)
    wy = fp["y_mm"] + px * math.sin(rad) + py * math.cos(rad)
    return wx, wy


def rotate(dx: float, dy: float, rot_deg: float) -> tuple[float, float]:
    rad = math.radians(rot_deg)
    return dx * math.cos(rad) - dy * math.sin(rad), dx * math.sin(rad) + dy * math.cos(rad)


# ── Phase 0: EscapePlanner port (mirrors EscapePlanner.cs) ───────────────────

def hdi_via_params(pitch: float, pad_min: float, nc: dict) -> tuple[float, float]:
    """Compute via diameter and drill from pitch (mirrors HdiPlanner.Derive)."""
    via_dia  = min(pitch * 0.5, pad_min)
    drill    = max(LASER_DRILL_MIN_MM, round(via_dia * 0.4, 3))
    if (via_dia - drill) / 2 < MIN_ANNULAR_MM:
        drill = max(LASER_DRILL_MIN_MM, via_dia - 2 * MIN_ANNULAR_MM)
    # Also respect net class limits (capped, not floored)
    via_dia = min(via_dia, nc.get("via_diameter_mm", 0.6))
    drill   = min(drill,   nc.get("via_drill_mm",    0.3))
    return round(via_dia, 3), round(drill, 3)


def escape_planner(
    lib, h,
    fp: dict,
    nc_by_net: dict[int, dict],
    default_nc: dict,
    copper_layers: int,
) -> tuple[list[dict], list[dict], dict[int, list[tuple[float, float, int]]]]:
    """Port of EscapePlanner.Fanout.

    Returns (new_traces, new_vias, escape_endpoints) where escape_endpoints
    is {net_id: [(wx, wy, layer), ...]} — the stub tips to use as routing
    targets instead of the BGA pad centres.
    """
    pads = fp.get("pads", [])
    if len(pads) < 16:
        return [], [], {}

    # Detect grid (local pad coords, rounded to 0.01mm)
    xs = sorted(set(round(p["x_mm"], 2) for p in pads))
    ys = sorted(set(round(p["y_mm"], 2) for p in pads))
    if len(xs) < 3 or len(ys) < 3 or len(pads) < 0.6 * len(xs) * len(ys):
        return [], [], {}   # not a grid (perimeter package)

    pitch_x = (xs[-1] - xs[0]) / max(len(xs) - 1, 1)
    pitch_y = (ys[-1] - ys[0]) / max(len(ys) - 1, 1)
    pitch   = min(pitch_x, pitch_y)
    if pitch < 0.3:
        return [], [], {}   # too fine — HDI microvias not modelled

    pad_min = min(min(p.get("w_mm", 1.0), p.get("h_mm", 1.0)) for p in pads)

    # Inner signal layers (skip layer 0 = top, skip layer N-1 = bottom, use odds)
    # Simple heuristic: odd layers (1,3,5,…) are signal in a standard stackup
    signal_layers = [l for l in range(1, copper_layers - 1) if l % 2 == 1]
    if not signal_layers:
        signal_layers = list(range(1, copper_layers - 1))

    top_layer = 0 if fp.get("side", 0) <= 0 else copper_layers - 1
    rot       = fp.get("rot_deg", 0)

    new_traces: list[dict] = []
    new_vias:   list[dict] = []
    # escape_endpoints: for BGA pads that got escaped, replace the pad centre
    # with the stub tip so the A* router starts from outside the BGA field
    escape_endpoints: dict[int, list[tuple[float, float, int]]] = defaultdict(list)

    stubs = dog_bones = skipped = 0

    for p in pads:
        net_id = p.get("net", -1)
        if net_id < 0:
            skipped += 1
            continue

        ix = xs.index(round(p["x_mm"], 2))
        iy = ys.index(round(p["y_mm"], 2))
        ring = min(ix, len(xs) - 1 - ix, iy, len(ys) - 1 - iy)

        nc  = nc_by_net.get(net_id, default_nc)
        w   = min(nc.get("trace_width_mm", 0.15), (pitch - nc.get("clearance_mm", 0.10) * 2) * 0.5)
        w   = max(w, 0.08)   # hard floor so traces are physical
        px, py = pad_world_xy(fp, p)

        # Outward direction in local footprint space
        dir_lx = -1.0 if ix <= len(xs) - 1 - ix else 1.0
        dir_ly = -1.0 if iy <= len(ys) - 1 - iy else 1.0
        horizontal = min(ix, len(xs) - 1 - ix) <= min(iy, len(ys) - 1 - iy)
        local_dx   = dir_lx if horizontal else 0.0
        local_dy   = 0.0   if horizontal else dir_ly
        # Rotate to world space
        wdx, wdy = rotate(local_dx, local_dy, rot)

        if ring <= 1:
            # Straight escape stub outward on the component layer
            run = ring * pitch + pitch + 0.5
            ex, ey = px + wdx * run, py + wdy * run
            seg = {"ax_mm": round(px, 4), "ay_mm": round(py, 4),
                   "bx_mm": round(ex, 4), "by_mm": round(ey, 4),
                   "w_mm": round(w, 4), "layer": top_layer,
                   "net": net_id, "pour": False}
            new_traces.append(seg)
            lib.dc_trace_add(h, nm(px), nm(py), nm(ex), nm(ey),
                             nm(w), top_layer, net_id, 0)
            escape_endpoints[net_id].append((ex, ey, top_layer))
            stubs += 1
        else:
            # Dog-bone: via at the diagonal grid cell, escape on inner layer
            if not signal_layers:
                skipped += 1
                continue
            via_dia, via_drill = hdi_via_params(pitch, pad_min, nc)
            # Diagonal offset toward the nearest corner (both axes away from centre)
            dvx_local, dvy_local = dir_lx * pitch / 2, dir_ly * pitch / 2
            dvx, dvy = rotate(dvx_local, dvy_local, rot)
            vx, vy   = px + dvx, py + dvy
            escape_layer = signal_layers[min((ring - 2) // 2, len(signal_layers) - 1)]

            # Stub: pad → via (on top layer)
            new_traces.append({"ax_mm": round(px, 4), "ay_mm": round(py, 4),
                                "bx_mm": round(vx, 4), "by_mm": round(vy, 4),
                                "w_mm": round(w, 4), "layer": top_layer,
                                "net": net_id, "pour": False})
            lib.dc_trace_add(h, nm(px), nm(py), nm(vx), nm(vy),
                             nm(w), top_layer, net_id, 0)

            # Dog-bone via
            new_vias.append({"x_mm": round(vx, 4), "y_mm": round(vy, 4),
                              "dia_mm": round(via_dia, 4), "drill_mm": round(via_drill, 4),
                              "net": net_id, "from": top_layer, "to": escape_layer})
            lib.dc_via_add(h, nm(vx), nm(vy), nm(via_dia), nm(via_drill),
                           net_id, top_layer, escape_layer)

            # Escape stub: via → clear of BGA field (on inner layer)
            run  = (ring + 1.5) * pitch
            ex   = vx + wdx * run
            ey   = vy + wdy * run
            new_traces.append({"ax_mm": round(vx, 4), "ay_mm": round(vy, 4),
                                "bx_mm": round(ex, 4), "by_mm": round(ey, 4),
                                "w_mm": round(w, 4), "layer": escape_layer,
                                "net": net_id, "pour": False})
            lib.dc_trace_add(h, nm(vx), nm(vy), nm(ex), nm(ey),
                             nm(w), escape_layer, net_id, 0)
            escape_endpoints[net_id].append((ex, ey, escape_layer))
            dog_bones += 1

    print(f"  {fp['ref']}: {stubs} edge escapes, {dog_bones} dog-bones"
          f" ({skipped} pads skipped no net)")
    return new_traces, new_vias, dict(escape_endpoints)


def is_bga(fp: dict) -> bool:
    pads = fp.get("pads", [])
    if len(pads) < 16:
        return False
    xs = set(round(p["x_mm"], 2) for p in pads)
    ys = set(round(p["y_mm"], 2) for p in pads)
    return len(xs) >= 3 and len(ys) >= 3 and len(pads) >= 0.6 * len(xs) * len(ys)


def fine_pitch_escape_planner(
    lib, h, fp: dict, nc_by_net: dict[int, dict], default_nc: dict,
    copper_layers: int, skipped_net_ids: set[int] | None = None,
    allowed_net_ids: set[int] | None = None,
    spread_factor: float = 1.0,
    normal_run_override: float | None = None,
) -> tuple[list[dict], dict[int, list[tuple[float, float, int]]]]:
    """Fan assigned pads with explicit local clearance outside the body.

    Global routing deliberately keeps the board-wide clearance.  These short
    manufacturer-rule fanouts are the only geometry allowed inside a QFN/VSON/
    connector's tighter pad field.
    """
    skipped_net_ids = skipped_net_ids or set()
    candidates = [pad for pad in fp.get("pads", [])
                  if int(pad.get("net", -1)) >= 0
                  and int(pad.get("net", -1)) not in skipped_net_ids
                  and (allowed_net_ids is None or
                       int(pad.get("net", -1)) in allowed_net_ids)
                  and not bool(pad.get("th", False))
                  and float(pad.get("clearance_mm", 0)) > 0]
    if not candidates:
        return [], {}
    layer = copper_layers - 1 if int(fp.get("side", 0)) == 1 else 0
    body_w = max(float(fp.get("body_w_mm", 0)), 0.1)
    body_h = max(float(fp.get("body_h_mm", 0)), 0.1)
    rotation = float(fp.get("rot_deg", 0))
    traces: list[dict] = []
    endpoints: dict[int, list[tuple[float, float, int]]] = defaultdict(list)
    for pad in candidates:
        net_id = int(pad["net"])
        local_x = float(pad.get("x_mm", 0)); local_y = float(pad.get("y_mm", 0))
        # A QFN/VSON exposed pad is connected into its plane with thermal vias,
        # not by dragging a lateral trace through one of the perimeter pins.
        if abs(local_x) < 1e-6 and abs(local_y) < 1e-6:
            continue
        dx = body_w / 2 - abs(local_x)
        dy = body_h / 2 - abs(local_y)
        # Spread perimeter exits tangentially so a full-width board trace can
        # expand after the short neck-down without colliding with the adjacent
        # package exit.  Monotonic scaling preserves pad order, so the fanouts
        # cannot cross one another.
        spread = max(1.0, spread_factor)
        normal_run = (normal_run_override if normal_run_override is not None
                      else (1.2 if fp.get("ref") == "U4" else .8))
        if dx <= dy:
            target_x = math.copysign(body_w / 2 + normal_run, local_x if local_x else 1)
            target_y = local_y * spread
        else:
            target_x = local_x * spread
            target_y = math.copysign(body_h / 2 + normal_run, local_y if local_y else 1)
        if int(fp.get("side", 0)) == 1:
            target_y = -target_y
        rx, ry = rotate(target_x, target_y, rotation)
        ex = float(fp["x_mm"]) + rx; ey = float(fp["y_mm"]) + ry
        sx, sy = pad_world_xy(fp, pad)
        nc = nc_by_net.get(net_id, default_nc)
        width = min(float(nc.get("trace_width_mm", .15)), .15,
                    min(float(pad.get("w_mm", .3)), float(pad.get("h_mm", .3))) * .7)
        width = max(width, .08)
        segment = {"ax_mm": round(sx, 4), "ay_mm": round(sy, 4),
                   "bx_mm": round(ex, 4), "by_mm": round(ey, 4),
                   "w_mm": round(width, 4), "layer": layer,
                   "net": net_id, "pour": False,
                   "min_width_override_mm": round(width, 4)}
        traces.append(segment)
        trace_id = lib.dc_trace_add(h, nm(sx), nm(sy), nm(ex), nm(ey),
                                    nm(width), layer, net_id, 0)
        if trace_id:
            lib.dc_trace_min_width_set(h, trace_id, nm(width))
        endpoints[net_id].append((ex, ey, layer))
    return traces, dict(endpoints)


# ── Phase 1: net-pad map (BGA pads replaced by escape endpoints) ──────────────

def build_net_pad_map(
    fps: list[dict],
    bga_refs: set[str],
    escape_eps: dict[int, list[tuple[float, float, int]]],
    copper_layers: int = 4,
) -> dict[int, list[tuple[float, float, int]]]:
    net_pads: dict[int, list] = defaultdict(list)
    for fp in fps:
        is_bga_fp = fp["ref"] in bga_refs
        for p in fp.get("pads", []):
            nid = p.get("net", -1)
            if nid < 0:
                continue
            if is_bga_fp:
                continue    # BGA pads are handled via escape endpoints
            wx, wy = pad_world_xy(fp, p)
            pad_layer = copper_layers - 1 if int(fp.get("side", 0)) == 1 else 0
            net_pads[nid].append((wx, wy, pad_layer))

    # Merge escape endpoints for BGA nets (replacing the BGA pad centres)
    for nid, eps in escape_eps.items():
        for ep in eps:
            net_pads[nid].append(ep)

    return dict(net_pads)


def greedy_spanning_tree(pads: list[tuple[float, float, int]]) -> list[tuple[int, int]]:
    if len(pads) < 2:
        return []
    visited = {0}
    edges: list[tuple[int, int]] = []
    while len(visited) < len(pads):
        best_d, best_i, best_j = float("inf"), -1, -1
        for i in visited:
            xi, yi, _ = pads[i]
            for j in range(len(pads)):
                if j in visited:
                    continue
                xj, yj, _ = pads[j]
                d = math.hypot(xi - xj, yi - yj)
                if d < best_d:
                    best_d, best_i, best_j = d, i, j
        visited.add(best_j)
        edges.append((best_i, best_j))
    return edges


def trace_identity(trace: dict) -> tuple:
    """Stable, direction-independent identity for replay-safe route merging."""
    a = (round(float(trace["ax_mm"]), 4), round(float(trace["ay_mm"]), 4))
    b = (round(float(trace["bx_mm"]), 4), round(float(trace["by_mm"]), 4))
    if b < a:
        a, b = b, a
    return (
        a, b,
        int(trace.get("layer", 0)),
        int(trace.get("net", -1)),
        round(float(trace.get("w_mm", 0)), 4),
        bool(trace.get("pour", False)),
    )


# ── Routing ───────────────────────────────────────────────────────────────────

def route_connection(
    lib, h,
    net_id: int,
    sx: float, sy: float, sl: int,
    gx: float, gy: float, gl: int,
    trace_w_nm: int,
    clearance_nm: int,
    via_d_nm: int,
    via_drill_nm: int,
    edge_clearance_nm: int = nm(.25),
) -> tuple[list[dict], list[dict]]:
    req = DcRouteRequestV2(
        abi_version       = 2,
        struct_size       = ctypes.sizeof(DcRouteRequestV2),
        net_id          = net_id,
        sx              = nm(sx), sy = nm(sy), start_layer = sl,
        gx              = nm(gx), gy = nm(gy), goal_layer  = gl,
        trace_width     = trace_w_nm,
        clearance       = clearance_nm,
        min_copper_to_edge = edge_clearance_nm,
        # Large distributed-control boards can exceed half a million 0.1 mm
        # cells even when a valid detour exists.  Keep the manufacturing grid
        # deterministic while matching CoreBridge's bounded large-board budget.
        grid_step       = nm(0.2),
        allow_vias      = 1,
        allow_microvia  = 0,
        via_diameter    = via_d_nm,
        via_drill       = via_drill_nm,
        via_cost_mm     = 0.4,
        max_expansions  = int(os.environ.get("DESIGNSTUDIO_ROUTE_MAX_EXPANSIONS", "300000")),
        min_drill_to_drill = nm(0.5),
    )
    status = lib.dc_route_run_v2(h, ctypes.byref(req))
    if status != 0:
        if os.environ.get("DESIGNSTUDIO_ROUTE_DEBUG", "").strip() == "1":
            print(f"    route status {status}: net={net_id} "
                  f"({sx:.4f},{sy:.4f},L{sl}) -> ({gx:.4f},{gy:.4f},L{gl})")
        return [], []

    n_pts = lib.dc_route_point_count(h)
    PtArray = DcRoutePoint * max(n_pts, 1)
    pt_arr  = PtArray()
    lib.dc_route_get_points(h, pt_arr, n_pts)

    n_vias = lib.dc_route_via_count(h)
    ViaArray = DcRouteVia * max(n_vias, 1)
    via_arr  = ViaArray()
    lib.dc_route_get_vias(h, via_arr, n_vias)

    new_traces: list[dict] = []
    new_vias:   list[dict] = []

    for k in range(n_pts - 1):
        a, b = pt_arr[k], pt_arr[k + 1]
        if a.layer != b.layer:
            continue
        seg = {"ax_mm": round(a.x * MM_PER_NM, 4), "ay_mm": round(a.y * MM_PER_NM, 4),
               "bx_mm": round(b.x * MM_PER_NM, 4), "by_mm": round(b.y * MM_PER_NM, 4),
               "w_mm":  round(trace_w_nm * MM_PER_NM, 4),
               "layer": a.layer, "net": net_id, "pour": False}
        # Only persist copper the native model actually accepted; previously
        # rejected segments still entered the project and corrupted DRC state.
        if lib.dc_trace_add(h, a.x, a.y, b.x, b.y, trace_w_nm, a.layer, net_id, 0):
            new_traces.append(seg)

    for k in range(n_vias):
        v = via_arr[k]
        if lib.dc_via_add(h, v.x, v.y, via_d_nm, via_drill_nm,
                          net_id, v.from_layer, v.to_layer):
            new_vias.append({"x_mm": round(v.x * MM_PER_NM, 4), "y_mm": round(v.y * MM_PER_NM, 4),
                             "dia_mm": round(via_d_nm * MM_PER_NM, 4),
                             "drill_mm": round(via_drill_nm * MM_PER_NM, 4),
                             "net": net_id, "from": v.from_layer, "to": v.to_layer,
                             "via_type": "through"})

    return new_traces, new_vias


# ── DRC report ────────────────────────────────────────────────────────────────

def run_drc_report(lib, data: dict) -> dict[str, int]:
    h2 = build_board(lib, data)
    opt = drc_options_from_project(data, check_skew=False)
    total = lib.dc_drc_run_v2(h2, ctypes.byref(opt))
    by_rule: dict[str, int] = defaultdict(int)
    v = DcDrcViolation()
    for i in range(max(total, 0)):
        if lib.dc_drc_get(h2, i, ctypes.byref(v)) == 0:
            by_rule[RULE_NAMES.get(v.rule, f"Rule{v.rule}")] += 1
    lib.dc_board_destroy(h2)
    return dict(by_rule)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(project_path: str) -> None:
    print(f"[bga-reroute] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps         = data["footprints"]
    net_table   = data["net_table"]
    net_classes = data["net_classes"]
    layers      = data.get("copper_layers", 8)

    nc_by_id  = {nc["id"]: nc for nc in net_classes}
    default_nc = nc_by_id.get(0, {})
    nc_by_net  = {n["id"]: nc_by_id.get(n.get("class_id", n.get("class", 0)), default_nc)
                  for n in net_table}
    net_name   = {n["id"]: n["name"] for n in net_table}
    skipped_names = {item.strip() for item in
                     os.environ.get("DESIGNSTUDIO_ROUTE_SKIP_NETS", "").split(",")
                     if item.strip()}
    skipped_net_ids = {nid for nid, name in net_name.items() if name in skipped_names}
    only_names = {item.strip() for item in
                  os.environ.get("DESIGNSTUDIO_ROUTE_ONLY_NETS", "").split(",")
                  if item.strip()}
    allowed_net_ids = ({nid for nid, name in net_name.items() if name in only_names}
                       if only_names else None)

    # Report the strictest class for visibility.  Each route still starts with
    # its own class clearance; DesignCore raises it when a stricter obstacle or
    # class-pair rule is encountered.
    max_board_cl = max(
        (nc.get("clearance_mm", 0.10) for nc in net_classes), default=0.10)
    fabrication_limits = data.get("pcb_rules", {}).get("limits_mm", {})
    edge_clearance_nm = nm(
        max(float(fabrication_limits.get("min_copper_to_edge", 0.25)), 0.0))

    def net_params(net_id: int) -> tuple[int, int, int, int]:
        nc  = nc_by_net.get(net_id, default_nc)
        tw  = max(nm(nc.get("trace_width_mm",  0.15)), nm(0.15))
        # The native router combines this value with each encountered
        # obstacle's class and class-pair rule, so this remains net-local.
        cl  = max(nm(nc.get("clearance_mm", 0.15)), nm(0.10))
        vd  = max(nm(nc.get("via_diameter_mm", nc.get("via_dia_mm", 0.60))), nm(0.60))
        vdr = max(nm(nc.get("via_drill_mm",    0.30)), nm(0.30))
        return tw, cl, vd, vdr

    print(f"[bga-reroute] {len(fps)} footprints, "
          f"{len(data['traces'])} traces, {len(data['vias'])} vias before clear")
    print(f"[bga-reroute] Route clearance: {max_board_cl:.3f} mm (board-wide max)")
    print(f"[bga-reroute] Copper-to-edge/cutout: "
          f"{edge_clearance_nm * MM_PER_NM:.3f} mm")

    lib = _load_lib()
    declare_route_fns(lib)

    preserve = os.environ.get("DESIGNSTUDIO_ROUTE_PRESERVE", "").strip() == "1"
    # Backbones may be installed first so the A* router treats them as fixed
    # copper obstacles.  Legacy full reroutes still start from an empty board.
    data_empty = data if preserve else {**data, "traces": [], "vias": []}
    h = build_board(lib, data_empty)
    print(f"[bga-reroute] Native board built ({'preserved backbones' if preserve else 'empty routes'})")

    all_traces: list[dict] = list(data.get("traces", [])) if preserve else []
    all_vias:   list[dict] = list(data.get("vias", [])) if preserve else []

    # ── Phase 0: BGA escape for fully-assigned BGAs only ─────────────────────
    # EscapePlanner is only beneficial when most BGA pads have nets assigned.
    # For partially-assigned BGAs (>30% unassigned) the escape stubs thread
    # through unassigned-pad obstacles and create more violations than they fix.
    bga_fps  = [fp for fp in fps if is_bga(fp)]
    bga_refs: set[str] = set()
    all_escape_eps: dict[int, list[tuple[float, float, int]]] = defaultdict(list)

    for fp in fps:
        if is_bga(fp):
            continue
        ref = str(fp.get("ref", ""))
        spread = 1.5 if ref == "U3" else (
            1.25 if ref in {"U4", "U5", "U6", "U7"} else 1.0)
        normal_run = {
            "U3": 2.5, "U4": 2.0, "U2": 1.2,
            "U5": 1.0, "U6": 1.0, "U7": 1.0,
        }.get(ref)
        traces, endpoints = fine_pitch_escape_planner(
            lib, h, fp, nc_by_net, default_nc, layers, skipped_net_ids,
            allowed_net_ids, spread_factor=spread,
            normal_run_override=normal_run)
        if not traces:
            continue
        all_traces.extend(traces)
        bga_refs.add(fp["ref"])
        for net_id, values in endpoints.items():
            all_escape_eps[net_id].extend(values)
        print(f"[bga-reroute] Fine-pitch fanout for {fp['ref']}: {len(traces)} pads")

    for fp in bga_fps:
        pads = fp.get("pads", [])
        assigned = sum(1 for p in pads if p.get("net", -1) >= 0)
        coverage = assigned / len(pads) if pads else 0
        if coverage >= 0.70:  # ≥70% assigned → run EscapePlanner
            print(f"[bga-reroute] EscapePlanner for {fp['ref']} "
                  f"({assigned}/{len(pads)} pads assigned, {coverage:.0%})")
            tr, vi, eps = escape_planner(lib, h, fp, nc_by_net, default_nc, layers)
            all_traces.extend(tr)
            all_vias.extend(vi)
            for nid, ep_list in eps.items():
                all_escape_eps[nid].extend(ep_list)
            bga_refs.add(fp["ref"])
        else:
            print(f"[bga-reroute] Skipping EscapePlanner for {fp['ref']} "
                  f"({assigned}/{len(pads)} assigned, {coverage:.0%} < 70% — direct routing)")

    if all_traces:
        print(f"  Escape total: {len(all_traces)} traces, {len(all_vias)} vias")

    # ── Phase 1: Build net-pad map (escaped BGAs use stub tips) ──────────────
    net_pads = build_net_pad_map(fps, bga_refs, dict(all_escape_eps), layers)
    routable = {nid: pads for nid, pads in net_pads.items()
                if len(pads) >= 2 and net_name.get(nid, "") not in skipped_names
                and (not only_names or net_name.get(nid, "") in only_names)}
    if skipped_names:
        print(f"[bga-reroute] Deferred backbone nets: {', '.join(sorted(skipped_names))}")
    if only_names:
        print(f"[bga-reroute] Targeted retry nets: {', '.join(sorted(only_names))}")
    print(f"\n[bga-reroute] Phase 1: A* routing — {len(routable)} routable nets")

    # Route the constrained/high-span nets before local two-pad links consume
    # the open corridors.  USB is explicit because the pair must share the
    # same uncluttered corridor; the remaining order is deterministic by
    # descending terminal span and fanout.
    critical_order = {name: index for index, name in enumerate((
        "COL3", "LED_ENABLE", "ENC_A", "ENC_B", "ENC_SW",
        "LED_DATA_MCU",
        "CC1", "CC2", "TS", "ILIM", "ISET", "L1", "L2", "LED_CT",
        "USB_D+", "USB_D-", "JOY_X", "JOY_Y"))}
    def route_priority(nid: int) -> tuple[float, float, int, int]:
        pads = routable[nid]
        xs = [pad[0] for pad in pads]; ys = [pad[1] for pad in pads]
        span = (max(xs) - min(xs)) + (max(ys) - min(ys))
        critical = critical_order.get(net_name.get(nid, ""), len(critical_order))
        return critical, -span, -len(pads), nid
    net_order = sorted(routable.keys(), key=route_priority)
    ok_count = fail_count = 0

    for net_id in net_order:
        pads   = routable[net_id]
        edges  = greedy_spanning_tree(pads)
        tw, cl, vd, vdr = net_params(net_id)
        net_fails = 0
        for i, j in edges:
            sx, sy, sl = pads[i]
            gx, gy, gl = pads[j]
            new_tr, new_v = route_connection(lib, h, net_id,
                                             sx, sy, sl, gx, gy, gl,
                                             tw, cl, vd, vdr,
                                             edge_clearance_nm)
            if new_tr or new_v:
                all_traces.extend(new_tr)
                all_vias.extend(new_v)
                ok_count += 1
            else:
                fail_count += 1
                net_fails += 1
        if net_fails:
            print(f"  WARN  {net_name.get(net_id,'?'):<20s} "
                  f"{net_fails}/{len(edges)} edges failed")

    lib.dc_board_destroy(h)
    print(f"\n[bga-reroute] A* done: {ok_count} edges OK, {fail_count} failed")

    # ── Post-route via cleanup ────────────────────────────────────────────────
    # 1. Remove true same-position duplicates on the SAME net.  Vias of
    #    different nets at one position are shorts, not duplicates: keeping
    #    one and dropping the other would silently leave the dropped net's
    #    traces dangling, so they are preserved and reported instead (the
    #    final DRC gate below will fail the run if any remain).
    seen_via: set[tuple] = set()
    deduped: list[dict] = []
    shorted_positions: list[tuple] = []
    for v in all_vias:
        key = (round(v["x_mm"], 3), round(v["y_mm"], 3), int(v.get("net", -1)))
        if key not in seen_via:
            seen_via.add(key)
            deduped.append(v)
    position_nets: dict[tuple, set] = defaultdict(set)
    for v in deduped:
        position_nets[(round(v["x_mm"], 3), round(v["y_mm"], 3))].add(int(v.get("net", -1)))
    shorted_positions = [pos for pos, nets in position_nets.items() if len(nets) > 1]
    if shorted_positions:
        print(f"[bga-reroute] ERROR: {len(shorted_positions)} via position(s) are shared "
              f"between different nets (short): {shorted_positions[:8]}")
    dup_removed = len(all_vias) - len(deduped)
    all_vias = deduped

    # 2. Remove vias that are too close to another via (DrillToDrill would fail).
    # The router uses 0.20mm clearance from via LAND; DrillToDrill needs 0.25mm
    # wall between DRILLS.  For 0.3mm drill: min_center_sep = 0.30 + 0.25 = 0.55mm.
    # Sort by net pad-count descending so higher-connectivity nets keep their vias.
    via_net_count = defaultdict(int)
    for v in all_vias:
        via_net_count[v["net"]] += 1
    # Sort: keep vias on nets with more pads/vias first
    sorted_vias = sorted(all_vias, key=lambda v: -via_net_count.get(v["net"], 0))
    kept: list[dict] = []
    kept_xy: list[tuple[float, float, float]] = []  # (x, y, drill_r)
    proximity_removed = 0
    for v in sorted_vias:
        vx, vy = v["x_mm"], v["y_mm"]
        vdr = v.get("drill_mm", 0.3) / 2
        conflict = False
        for kx, ky, kdr in kept_xy:
            min_sep = kdr + vdr + 0.25  # drill_r + drill_r + min_wall
            if math.hypot(vx - kx, vy - ky) < min_sep:
                conflict = True
                break
        if not conflict:
            kept.append(v)
            kept_xy.append((vx, vy, vdr))
        else:
            proximity_removed += 1
    all_vias = kept

    total_removed = dup_removed + proximity_removed
    if total_removed:
        print(f"[bga-reroute] Removed {dup_removed} duplicate + "
              f"{proximity_removed} too-close vias → {len(all_vias)} remain")

    # 2b. Preserved targeted retries may regenerate an identical package
    # fanout or reuse an already-routed grid segment.  Retain one stable copy
    # rather than accumulating coincident copper on every repair pass.
    unique_traces: list[dict] = []
    seen_traces: set[tuple] = set()
    duplicate_traces = 0
    for trace in all_traces:
        key = trace_identity(trace)
        if key in seen_traces:
            duplicate_traces += 1
            continue
        seen_traces.add(key)
        unique_traces.append(trace)
    all_traces = unique_traces
    if duplicate_traces:
        print(f"[bga-reroute] Removed {duplicate_traces} duplicate trace "
              f"segments → {len(all_traces)} remain")

    # 3. Remove traces/vias whose midpoint is inside a component courtyard
    #    on a different net — prevents routes from threading under ICs.
    sys.path.insert(0, str(_HERE.parent))
    try:
        from board_fit_agent import build_courtyards, remove_courtyard_violations
        if not preserve and os.environ.get("DESIGNSTUDIO_ROUTE_COURTYARD_CLEANUP", "") == "1":
            courts = build_courtyards(fps, margin=0.25)
            all_traces, all_vias, bad_tr, bad_v = remove_courtyard_violations(
                all_traces, all_vias, courts)
            if bad_tr or bad_v:
                print(f"[bga-reroute] Courtyard cleanup: removed {bad_tr} traces, "
                      f"{bad_v} vias under components")
    except ImportError:
        pass   # board_fit_agent not available — skip

    data["traces"] = all_traces
    data["vias"]   = all_vias

    tmp = project_path + ".reroute.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, project_path)
    print(f"[bga-reroute] Saved → {project_path}")
    print(f"[bga-reroute] Final: {len(all_traces)} traces, {len(all_vias)} vias")

    # ── DRC report ────────────────────────────────────────────────────────────
    print(f"[bga-reroute] Running DRC...")
    by_rule = run_drc_report(lib, data)
    total   = sum(by_rule.values())
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Post-reroute DRC: {total} violations")
    for rule, cnt in sorted(by_rule.items(), key=lambda x: -x[1]):
        print(f"    {rule:28s}  {cnt}")
    if not by_rule:
        print("  ✓ No violations!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return 0 if total == 0 and not shorted_positions else 2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: bga_reroute_agent.py <project.dsproj>"); sys.exit(1)
    if not os.path.exists(sys.argv[1]):
        print(f"error: not found: {sys.argv[1]}"); sys.exit(1)
    sys.exit(main(sys.argv[1]))
