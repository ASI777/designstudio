#!/usr/bin/env python3
"""
SVG dimension graph extractor.

This is the vector-first pass for datasheet footprint/layout drawings:

  1. Parse numeric dimension text, arrowhead tips, and path segments from SVG.
  2. Attach each dimension value to the nearest compatible two-arrow span.
  3. From each arrow tip, cast perpendicular rays toward the component drawing.
  4. Stop rays at the first likely component/feature outline segment.
  5. Emit calibrated constraints in mm for downstream grid/shape adjustment.

It intentionally does not try to generate a finished footprint. The output is a
dimension graph: text -> arrow pair -> measured feature endpoints -> local scale.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import median
from typing import Iterable

PT_TO_MM = 25.4 / 72.0


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Segment:
    a: Point
    b: Point
    stroke: str = ""
    fill: str = ""
    stroke_width: float = 0.0
    source: str = "path"

    @property
    def dx(self) -> float:
        return self.b.x - self.a.x

    @property
    def dy(self) -> float:
        return self.b.y - self.a.y

    @property
    def length(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan2(self.dy, self.dx))

    def bbox(self) -> tuple[float, float, float, float]:
        return min(self.a.x, self.b.x), min(self.a.y, self.b.y), max(self.a.x, self.b.x), max(self.a.y, self.b.y)


@dataclass
class Label:
    text: str
    value: float
    x: float
    y: float
    axis_hint: str
    multiplier: int = 1
    kind: str = "linear"


@dataclass
class ArrowTip:
    x: float
    y: float
    size: float


@dataclass
class DimensionConstraint:
    label: Label
    axis: str
    arrow_tips: list[Point]
    arrow_span_svg_mm: float
    scale_mm_per_svg_mm: float
    projection_direction: Point | None
    feature_hits: list[Point]
    measured_span_mm: float | None
    confidence: float
    notes: list[str]


def parse_matrix(transform: str | None) -> list[float]:
    if not transform:
        return [1, 0, 0, 1, 0, 0]
    m = re.search(r"matrix\(([^)]+)\)", transform)
    if not m:
        return [1, 0, 0, 1, 0, 0]
    nums = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:e[-+]?\d+)?", m.group(1), re.I)]
    return nums[:6] if len(nums) >= 6 else [1, 0, 0, 1, 0, 0]


def apply_mat(mat: list[float], x: float, y: float) -> Point:
    a, b, c, d, e, f = mat
    return Point((a * x + c * y + e) * PT_TO_MM, (b * x + d * y + f) * PT_TO_MM)


def attr(tag: str, name: str, default: str = "") -> str:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return m.group(1) if m else default


def parse_path_points(d: str) -> list[Point]:
    """Parse enough SVG path syntax for CAD-like M/L/H/V paths."""
    toks = re.findall(r"[MLHVZmlhv]|-?\d*\.?\d+(?:e[-+]?\d+)?", d, re.I)
    pts: list[Point] = []
    cur = Point(0.0, 0.0)
    i = 0
    cmd = ""
    while i < len(toks):
        if re.fullmatch(r"[MLHVZmlhv]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                continue
        if cmd in ("M", "L"):
            if i + 1 >= len(toks):
                break
            cur = Point(float(toks[i]), float(toks[i + 1]))
            pts.append(cur)
            i += 2
            if cmd == "M":
                cmd = "L"
        elif cmd in ("m", "l"):
            if i + 1 >= len(toks):
                break
            cur = Point(cur.x + float(toks[i]), cur.y + float(toks[i + 1]))
            pts.append(cur)
            i += 2
            if cmd == "m":
                cmd = "l"
        elif cmd == "H":
            cur = Point(float(toks[i]), cur.y)
            pts.append(cur)
            i += 1
        elif cmd == "h":
            cur = Point(cur.x + float(toks[i]), cur.y)
            pts.append(cur)
            i += 1
        elif cmd == "V":
            cur = Point(cur.x, float(toks[i]))
            pts.append(cur)
            i += 1
        elif cmd == "v":
            cur = Point(cur.x, cur.y + float(toks[i]))
            pts.append(cur)
            i += 1
        else:
            i += 1
    return pts


def transform_points(points: Iterable[Point], mat: list[float]) -> list[Point]:
    return [apply_mat(mat, p.x, p.y) for p in points]


def dist_point_line(p: Point, a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.hypot(p.x - a.x, p.y - a.y)
    return abs(dx * (a.y - p.y) - dy * (a.x - p.x)) / length


def parse_svg(svg: str) -> tuple[list[Segment], list[ArrowTip], list[Label]]:
    segments: list[Segment] = []
    arrow_tips: list[ArrowTip] = []
    labels: list[Label] = []

    for pm in re.finditer(r"<path\b([^>]*)/?>", svg, re.S):
        tag = pm.group(1)
        d = attr(tag, "d")
        if not d:
            continue
        mat = parse_matrix(attr(tag, "transform"))
        fill = attr(tag, "fill")
        stroke = attr(tag, "stroke")
        sw = float(attr(tag, "stroke-width", "0") or 0)
        pts = transform_points(parse_path_points(d), mat)
        if fill and fill.lower() not in ("none", "#ffffff") and len(pts) >= 3:
            # Treat small filled triangles as arrowheads. Tip is the vertex with
            # largest distance from the opposite edge.
            tri = pts[:3]
            area = abs((tri[1].x - tri[0].x) * (tri[2].y - tri[0].y) - (tri[2].x - tri[0].x) * (tri[1].y - tri[0].y)) / 2
            side = max(math.hypot(tri[i].x - tri[(i + 1) % 3].x, tri[i].y - tri[(i + 1) % 3].y) for i in range(3))
            if 0.002 < area < 12 and side < 8:
                ranked = sorted(
                    ((dist_point_line(tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]), tri[i]) for i in range(3)),
                    reverse=True,
                    key=lambda item: item[0],
                )
                arrow_tips.append(ArrowTip(ranked[0][1].x, ranked[0][1].y, math.sqrt(area)))
        if fill and fill.lower() != "none" and not stroke:
            continue
        for a, b in zip(pts, pts[1:]):
            if math.hypot(a.x - b.x, a.y - b.y) > 0.05:
                segments.append(Segment(a, b, stroke=stroke, fill=fill, stroke_width=sw))

    # pdftocairo sometimes emits real text, while other conversions outline text
    # as glyph use-paths. This parser handles real text; outlined glyph OCR is a
    # separate stage.
    for tm in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        tag, body = tm.groups()
        mat = parse_matrix(attr(tag, "transform"))
        tsp = re.search(r"<tspan\b([^>]*)>(.*?)</tspan>", body, re.S)
        if not tsp:
            continue
        ttag, raw = tsp.groups()
        text = re.sub(r"<[^>]+>", "", raw)
        text = (
            text.replace("&#x00b1;", "±")
            .replace("&#x00d8;", "Ø")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .strip()
        )
        xvals = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:e[-+]?\d+)?", attr(ttag, "x"), re.I)]
        yvals = [float(y) for y in re.findall(r"-?\d*\.?\d+(?:e[-+]?\d+)?", attr(ttag, "y"), re.I)]
        if not xvals or not yvals:
            continue
        p = apply_mat(mat, xvals[0], yvals[0])
        label = parse_dimension_label(text, p.x, p.y, mat)
        if label:
            labels.append(label)
    return segments, arrow_tips, labels


def parse_dimension_label(text: str, x: float, y: float, mat: list[float]) -> Label | None:
    s = text.strip()
    if re.fullmatch(r"[A-Z]{1,3}\d{0,3}", s):
        return None
    cleaned = s.replace("Ø", "Ø").replace("⌀", "Ø").replace("mm", "").strip()
    cleaned = cleaned.replace(" ", "")
    pitch = re.fullmatch(r"Pitch=?(\d+(?:\.\d+)?)", cleaned, re.I)
    if pitch:
        value = float(pitch.group(1))
        axis_hint = "V" if abs(mat[1]) > abs(mat[0]) else "H"
        return Label(s, value, x, y, axis_hint, 1, "pitch")
    # Accept only full dimension-token strings. This deliberately rejects title
    # block metadata like dates, "3rd Angle Projection", page numbers, etc.
    m = re.fullmatch(r"(?:(\d+)[xX×])?([RrØ]?)(\d+(?:\.\d+)?)(?:±\d+(?:\.\d+)?)?", cleaned)
    if not m:
        return None
    mult = int(m.group(1) or 1)
    prefix = m.group(2)
    is_radius = prefix.lower() == "r"
    value = float(m.group(3))
    if mult == 1 and not prefix and "." not in m.group(3):
        return None
    if value <= 0 or value > 500:
        return None
    axis_hint = "V" if abs(mat[1]) > abs(mat[0]) else "H"
    return Label(s, value, x, y, axis_hint, mult, "radius" if is_radius else "linear")


def unit(vx: float, vy: float) -> Point:
    n = math.hypot(vx, vy)
    return Point(vx / n, vy / n) if n else Point(0, 0)


def dot(a: Point, b: Point) -> float:
    return a.x * b.x + a.y * b.y


def cross(a: Point, b: Point) -> float:
    return a.x * b.y - a.y * b.x


def ray_segment_intersection(origin: Point, direction: Point, seg: Segment) -> tuple[float, Point] | None:
    # origin + t*direction intersects seg.a + u*(seg.b-seg.a)
    r = direction
    s = Point(seg.dx, seg.dy)
    denom = cross(r, s)
    if abs(denom) < 1e-8:
        return None
    qp = Point(seg.a.x - origin.x, seg.a.y - origin.y)
    t = cross(qp, s) / denom
    u = cross(qp, r) / denom
    if t >= 0 and -1e-6 <= u <= 1 + 1e-6:
        return t, Point(origin.x + t * r.x, origin.y + t * r.y)
    return None


def nearest_hit(origin: Point, direction: Point, segments: list[Segment], exclude_radius: float = 0.35) -> tuple[float, Point, Segment] | None:
    best = None
    for seg in segments:
        # Avoid immediately hitting the dimension arrow/leader itself.
        if dist_point_line(origin, seg.a, seg.b) < exclude_radius:
            continue
        hit = ray_segment_intersection(origin, direction, seg)
        if not hit:
            continue
        t, p = hit
        if t < exclude_radius:
            continue
        if best is None or t < best[0]:
            best = (t, p, seg)
    return best


def candidate_arrow_pairs(label: Label, tips: list[ArrowTip], perp_tol: float) -> list[tuple[float, ArrowTip, ArrowTip, str]]:
    pairs = []
    for i, a in enumerate(tips):
        for b in tips[i + 1 :]:
            dx, dy = b.x - a.x, b.y - a.y
            span = math.hypot(dx, dy)
            if span < 0.4:
                continue
            axis = "H" if abs(dx) >= abs(dy) else "V"
            if axis == "H":
                perp = abs(((a.y + b.y) / 2) - label.y)
                lo, hi = sorted([a.x, b.x])
                label_par = label.x
            else:
                perp = abs(((a.x + b.x) / 2) - label.x)
                lo, hi = sorted([a.y, b.y])
                label_par = label.y
            if perp > perp_tol:
                continue
            outside = 0 if lo - 3 <= label_par <= hi + 3 else min(abs(label_par - lo), abs(label_par - hi))
            hint_penalty = 0 if axis == label.axis_hint else 2.0
            score = span * 0.02 + perp + outside * 0.35 + hint_penalty
            pairs.append((score, a, b, axis))
    return sorted(pairs, key=lambda item: item[0])


def projection_hits(a: ArrowTip, b: ArrowTip, axis: str, segments: list[Segment]) -> tuple[Point | None, list[Point]]:
    if axis == "H":
        dirs = [Point(0, -1), Point(0, 1)]
    else:
        dirs = [Point(-1, 0), Point(1, 0)]
    origins = [Point(a.x, a.y), Point(b.x, b.y)]
    best_dir = None
    best_hits: list[Point] = []
    best_total = float("inf")
    for direction in dirs:
        hits = []
        total = 0.0
        for origin in origins:
            hit = nearest_hit(origin, direction, segments)
            if hit:
                total += hit[0]
                hits.append(hit[1])
        if len(hits) == 2 and total < best_total:
            best_total = total
            best_dir = direction
            best_hits = hits
    return best_dir, best_hits


def extract_dimension_graph(svg_path: str | Path, values: dict[str, float] | None = None) -> dict:
    svg_path = Path(svg_path)
    svg = svg_path.read_text(encoding="utf-8", errors="replace")
    segments, tips, labels = parse_svg(svg)
    if values:
        for label in labels:
            if label.text in values:
                label.value = float(values[label.text])

    constraints: list[DimensionConstraint] = []
    raw_scales = []
    for label in labels:
        if label.kind != "linear":
            continue
        pairs = candidate_arrow_pairs(label, tips, perp_tol=2.0)
        if not pairs:
            continue
        score, a, b, axis = pairs[0]
        span = abs(b.x - a.x) if axis == "H" else abs(b.y - a.y)
        if span <= 0:
            continue
        scale = label.value / span
        raw_scales.append(scale)
        direction, hits = projection_hits(a, b, axis, segments)
        measured = None
        notes = []
        if len(hits) == 2:
            measured = abs(hits[1].x - hits[0].x) * scale if axis == "H" else abs(hits[1].y - hits[0].y) * scale
        else:
            notes.append("perpendicular projection did not hit two feature outlines")
        confidence = max(0.0, min(1.0, 1.0 - score / 10.0))
        if measured is not None and abs(measured - label.value) > max(0.25, 0.12 * label.value):
            notes.append("projected feature span differs from dimension value; drawing may be not to scale or hit wrong outline")
            confidence *= 0.65
        constraints.append(
            DimensionConstraint(
                label=label,
                axis=axis,
                arrow_tips=[Point(a.x, a.y), Point(b.x, b.y)],
                arrow_span_svg_mm=span,
                scale_mm_per_svg_mm=scale,
                projection_direction=direction,
                feature_hits=hits,
                measured_span_mm=measured,
                confidence=round(confidence, 3),
                notes=notes,
            )
        )

    global_scale = median(raw_scales) if raw_scales else None
    return {
        "source_svg": str(svg_path),
        "counts": {
            "segments": len(segments),
            "arrow_tips": len(tips),
            "dimension_labels": len(labels),
            "constraints": len(constraints),
        },
        "global_scale_mm_per_svg_mm": global_scale,
        "constraints": [asdict(c) for c in constraints],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract dimension-arrow-feature graph from a layout SVG.")
    ap.add_argument("svg", help="Input SVG with vector text if available")
    ap.add_argument("-o", "--output", help="Output JSON path. Defaults to <svg>.dimension_graph.json")
    ap.add_argument("--values-json", help="Optional mapping for table/reference labels, e.g. {'A': 1.27}")
    args = ap.parse_args()

    values = json.loads(Path(args.values_json).read_text()) if args.values_json else None
    graph = extract_dimension_graph(args.svg, values=values)
    out = Path(args.output) if args.output else Path(args.svg).with_suffix(".dimension_graph.json")
    out.write_text(json.dumps(graph, indent=2) + "\n")
    c = graph["counts"]
    print(
        f"{Path(args.svg).name}: labels={c['dimension_labels']} tips={c['arrow_tips']} "
        f"segments={c['segments']} constraints={c['constraints']} -> {out}"
    )
    if graph["global_scale_mm_per_svg_mm"]:
        print(f"global scale median: {graph['global_scale_mm_per_svg_mm']:.6f} mm/svg-mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
