#!/usr/bin/env python3
"""Deterministic, fail-closed PCB placement constraint legalizer.

All dimensions are millimetres.  The module intentionally uses only the Python
standard library so the same checks run in development and packaged agents.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-7
EDGE_MARGIN = 0.25
SEARCH_GRID = 0.5


Point = tuple[float, float]
Polygon = list[Point]


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def point_in_polygon(p: Point, poly: Polygon) -> bool:
    if len(poly) < 3:
        return False
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        a, b = poly[j], poly[i]
        if abs(_cross(a, b, p)) <= EPS and min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS \
                and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS:
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]
            if p[0] < x:
                inside = not inside
        j = i
    return inside


def _orient(a: Point, b: Point, c: Point) -> int:
    value = _cross(a, b, c)
    return 1 if value > EPS else -1 if value < -EPS else 0


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    for p, x, y, o in ((c, a, b, o1), (d, a, b, o2), (a, c, d, o3), (b, c, d, o4)):
        if o == 0 and min(x[0], y[0]) - EPS <= p[0] <= max(x[0], y[0]) + EPS \
                and min(x[1], y[1]) - EPS <= p[1] <= max(x[1], y[1]) + EPS:
            return True
    return False


def polygons_intersect(a: Polygon, b: Polygon) -> bool:
    if not a or not b:
        return False
    if point_in_polygon(a[0], b) or point_in_polygon(b[0], a):
        return True
    return any(segments_intersect(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)])
               for i in range(len(a)) for j in range(len(b)))


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= EPS:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def polygon_gap(a: Polygon, b: Polygon) -> float:
    if polygons_intersect(a, b):
        return 0.0
    return min(_point_segment_distance(a[i], b[j], b[(j + 1) % len(b)])
               for i in range(len(a)) for j in range(len(b)))


def _boundary_gap(a: Polygon, ring: Polygon) -> float:
    """Distance between polygon boundaries, ignoring containment."""
    if any(segments_intersect(a[i], a[(i + 1) % len(a)],
                              ring[j], ring[(j + 1) % len(ring)])
           for i in range(len(a)) for j in range(len(ring))):
        return 0.0
    return min(
        min(_point_segment_distance(a[i], ring[j], ring[(j + 1) % len(ring)]),
            _point_segment_distance(ring[j], a[i], a[(i + 1) % len(a)]))
        for i in range(len(a)) for j in range(len(ring)))


def _bbox(poly: Polygon) -> tuple[float, float, float, float]:
    return (min(p[0] for p in poly), min(p[1] for p in poly),
            max(p[0] for p in poly), max(p[1] for p in poly))


def _local_outline(fp: dict) -> Polygon:
    court = fp.get("courtyard_pts", [])
    if len(court) >= 3:
        return [(float(p[0]), float(p[1])) for p in court]
    xs: list[float] = []
    ys: list[float] = []
    for pad in fp.get("pads", []):
        w = float(pad.get("w_mm", pad.get("width_mm", 0.5)))
        h = float(pad.get("h_mm", pad.get("height_mm", 0.5)))
        x, y = float(pad.get("x_mm", 0)), float(pad.get("y_mm", 0))
        xs.extend((x - w / 2, x + w / 2)); ys.extend((y - h / 2, y + h / 2))
    bw, bh = float(fp.get("body_w_mm", 0)), float(fp.get("body_h_mm", 0))
    if bw > 0 and bh > 0:
        cx, cy = float(fp.get("body_cx_mm", 0)), float(fp.get("body_cy_mm", 0))
        xs.extend((cx - bw / 2, cx + bw / 2)); ys.extend((cy - bh / 2, cy + bh / 2))
    if not xs:
        xs, ys = [-1.5, 1.5], [-1.5, 1.5]
    halo = 0.5
    x0, x1, y0, y1 = min(xs) - halo, max(xs) + halo, min(ys) - halo, max(ys) + halo
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def component_polygon(fp: dict, x: float | None = None, y: float | None = None,
                      rot: float | None = None) -> Polygon:
    x = float(fp.get("x_mm", 0)) if x is None else x
    y = float(fp.get("y_mm", 0)) if y is None else y
    angle = math.radians(float(fp.get("rot_deg", 0)) if rot is None else rot)
    c, s = math.cos(angle), math.sin(angle)
    mirror_y = int(fp.get("side", 0)) == 1
    return [(x + px * c - (-py if mirror_y else py) * s,
             y + px * s + (-py if mirror_y else py) * c)
            for px, py in _local_outline(fp)]


def _board_geometry(data: dict) -> tuple[Polygon, list[Polygon]]:
    raw = data.get("board_outline_pts", [])
    if len(raw) >= 3:
        outer = [(float(p[0]), float(p[1])) for p in raw]
    else:
        w, h = float(data.get("board_width_mm", 100)), float(data.get("board_height_mm", 80))
        outer = [(0, 0), (w, 0), (w, h), (0, h)]
    cutouts = [[(float(p[0]), float(p[1])) for p in ring]
               for ring in data.get("board_cutouts", []) if len(ring) >= 3]
    return outer, cutouts


def _inside_board(poly: Polygon, outer: Polygon, cutouts: list[Polygon]) -> bool:
    # Edge midpoints catch a polygon chord escaping through a concave outline.
    probes = poly + [((poly[i][0] + poly[(i + 1) % len(poly)][0]) / 2,
                      (poly[i][1] + poly[(i + 1) % len(poly)][1]) / 2)
                     for i in range(len(poly))]
    if not all(point_in_polygon(p, outer) for p in probes):
        return False
    return not any(polygons_intersect(poly, hole) for hole in cutouts)


def _placement(fp: dict) -> dict:
    return fp.get("placement", {}) if isinstance(fp.get("placement", {}), dict) else {}


def _area_polygon(area: dict) -> Polygon:
    return [(float(p[0]), float(p[1])) for p in area.get("pts", [])]


@dataclass
class Placed:
    index: int
    poly: Polygon


def _candidate_error(data: dict, fp: dict, poly: Polygon, placed: list[Placed],
                     outer: Polygon, cutouts: list[Polygon]) -> str | None:
    if not _inside_board(poly, outer, cutouts):
        return "outside board outline or intersects a cutout"
    p = _placement(fp)
    if p.get("test_access_required", False):
        halo = float(p.get("test_access_halo_mm", 0))
        edge_gap = min([_boundary_gap(poly, outer)]
                       + [_boundary_gap(poly, hole) for hole in cutouts])
        if edge_gap + EPS < halo:
            return f"test-access edge gap is {edge_gap:.3f} mm, requires {halo:.3f} mm"

    height = float(fp.get("h3d_mm", 0))
    for area in data.get("rule_areas", []):
        ap = _area_polygon(area)
        if len(ap) < 3 or not polygons_intersect(poly, ap):
            continue
        if area.get("forbid_placement", False):
            return f"intersects no-placement area {area.get('name', area.get('id', '?'))}"
        limit = float(area.get("max_height_mm", 0))
        if limit > 0 and height > limit + EPS:
            return f"height {height:g} mm exceeds {limit:g} mm in {area.get('name', 'rule area')}"

    for other in placed:
        ofp = data["footprints"][other.index]
        if int(fp.get("side", 0)) != int(ofp.get("side", 0)):
            continue
        op = _placement(ofp)
        gap = polygon_gap(poly, other.poly)
        required = 0.0
        if float(p.get("thermal_power_w", 0)) > 0:
            required = max(required, float(p.get("thermal_clearance_mm", 0)))
        if float(op.get("thermal_power_w", 0)) > 0:
            required = max(required, float(op.get("thermal_clearance_mm", 0)))
        if p.get("test_access_required", False):
            required = max(required, float(p.get("test_access_halo_mm", 0)))
        if op.get("test_access_required", False):
            required = max(required, float(op.get("test_access_halo_mm", 0)))
        if gap + EPS < required or (required <= EPS and polygons_intersect(poly, other.poly)):
            return f"clearance to {ofp.get('ref', '?')} is {gap:.3f} mm, requires {required:.3f} mm"
    return None


def _spiral(cx: float, cy: float, bounds: tuple[float, float, float, float]):
    x0, y0, x1, y1 = bounds
    max_ring = int(math.ceil(max(x1 - x0, y1 - y0) / SEARCH_GRID)) + 2
    yield cx, cy
    for ring in range(1, max_ring + 1):
        for dx in range(-ring, ring + 1):
            for dy in (-ring, ring):
                yield cx + dx * SEARCH_GRID, cy + dy * SEARCH_GRID
        for dy in range(-ring + 1, ring):
            for dx in (-ring, ring):
                yield cx + dx * SEARCH_GRID, cy + dy * SEARCH_GRID


def _anchor_candidates(fp: dict, anchor: str, outer: Polygon):
    local = component_polygon(fp, 0, 0)
    lx0, ly0, lx1, ly1 = _bbox(local)
    x0, y0, x1, y1 = _bbox(outer)
    if anchor in ("left", "right"):
        x = x0 + EDGE_MARGIN - lx0 if anchor == "left" else x1 - EDGE_MARGIN - lx1
        start = max(y0 - ly0 + EDGE_MARGIN, y0)
        end = min(y1 - ly1 - EDGE_MARGIN, y1)
        preferred = float(fp.get("y_mm", (start + end) / 2))
        values = sorted((start + i * SEARCH_GRID for i in range(max(0, int((end-start)/SEARCH_GRID)) + 1)),
                        key=lambda value: (abs(value - preferred), value))
        for y in values:
            yield x, y
    else:
        y = y0 + EDGE_MARGIN - ly0 if anchor == "top" else y1 - EDGE_MARGIN - ly1
        start = max(x0 - lx0 + EDGE_MARGIN, x0)
        end = min(x1 - lx1 - EDGE_MARGIN, x1)
        preferred = float(fp.get("x_mm", (start + end) / 2))
        values = sorted((start + i * SEARCH_GRID for i in range(max(0, int((end-start)/SEARCH_GRID)) + 1)),
                        key=lambda value: (abs(value - preferred), value))
        for x in values:
            yield x, y


def apply_constraints(data: dict, original_positions: list[tuple[float, float, float]] | None = None) -> dict:
    """Legalize footprints in-place. Returns {ok, errors, moved}.

    Locked footprints are validated at their original locations. Edge anchors
    are placed before unconstrained parts. On failure callers must discard the
    mutated in-memory document and leave the source file untouched.
    """
    fps = data.get("footprints", [])
    outer, cutouts = _board_geometry(data)
    bounds = _bbox(outer)
    original_positions = original_positions or [
        (float(fp.get("x_mm", 0)), float(fp.get("y_mm", 0)), float(fp.get("rot_deg", 0))) for fp in fps]
    placed: list[Placed] = []
    errors: list[str] = []
    moved = 0

    def priority(i: int) -> tuple[int, int]:
        p = _placement(fps[i])
        return (0 if p.get("locked", False) else 1 if p.get("edge_anchor", "") in
                ("left", "right", "top", "bottom") else 2, i)

    for i in sorted(range(len(fps)), key=priority):
        fp, p = fps[i], _placement(fps[i])
        locked = bool(p.get("locked", False))
        anchor = str(p.get("edge_anchor", "")).lower()
        rot = original_positions[i][2] if locked else float(fp.get("rot_deg", 0))
        candidates = []
        if locked:
            candidates = [(original_positions[i][0], original_positions[i][1])]
        elif anchor in ("left", "right", "top", "bottom"):
            candidates = _anchor_candidates(fp, anchor, outer)
        else:
            candidates = _spiral(float(fp.get("x_mm", 0)), float(fp.get("y_mm", 0)), bounds)

        last_error = "no candidate position"
        accepted = None
        for x, y in candidates:
            poly = component_polygon(fp, x, y, rot)
            last_error = _candidate_error(data, fp, poly, placed, outer, cutouts)
            if last_error is None:
                accepted = (x, y, poly)
                break
        if accepted is None:
            kind = "locked" if locked else f"{anchor}-anchored" if anchor else "movable"
            errors.append(f"{fp.get('ref', '?')} ({kind}): {last_error}")
            continue
        x, y, poly = accepted
        if abs(x - float(fp.get("x_mm", 0))) > EPS or abs(y - float(fp.get("y_mm", 0))) > EPS:
            moved += 1
        fp["x_mm"], fp["y_mm"], fp["rot_deg"] = round(x, 3), round(y, 3), rot
        placed.append(Placed(i, poly))

    return {"ok": not errors, "errors": errors, "moved": moved}
