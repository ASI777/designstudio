#!/usr/bin/env python3
"""
pad_dimensioner.py — datasheet recommended-layout SVG → dimensioned footprint.

Self-contained (no external deps). Pipeline:

  1. PARSE geometry: arrowhead tips (filled #4b4b4b triangles), dimension text
     labels (numeric <text>), and the line segments (witness/extension lines).
  2. MATCH (two-pass):
       pass 1 — bracket each label with its nearest arrow pair → median scale
                from the agreeing large dims = the drawing's true mm/svg scale.
       pass 2 — re-match EVERY label (incl. small/repeated pad dims) by finding
                the arrow pair whose span ≈ value/scale (expected span). This is
                what lets 0.30 / 0.50 pitch / 0.85 / 1.10 pad dims resolve, since
                their closely-spaced arrows are otherwise ambiguous.
  3. PROJECT each dim's two extension (witness) lines back to the copper feature
     EDGES they terminate on. H-dim → two vertical edges, V-dim → two horizontal.
  4. VIEWS: cluster dims into drawing views (left = PCB layout, right = solder
     mask…) via 2-means on label X; reference each view to its own connector
     centre so identical pads land on identical coordinates.
  5. OVERLAY + MERGE: cluster feature edges across views (±0.08mm). An edge in
     ≥2 views is confirmed; each view also contributes its unique dims (length,
     width, pitch, radius) → one combined dimensioned model.

Output: the `--output` path, which defaults to the external DesignStudio state
directory rather than next to the source SVG.
"""
import argparse, os, re, math, json, sys
from pathlib import Path
from statistics import median

PT2MM = 25.4 / 72   # SVG page pt → mm


# ── low-level SVG parsing ────────────────────────────────────────────────────
def parse_matrix(tr):
    if not tr:
        return [1, 0, 0, 1, 0, 0]
    m = re.search(r'matrix\(([^)]+)\)', tr)
    if not m:
        return [1, 0, 0, 1, 0, 0]
    nums = [float(x) for x in re.findall(r'-?\d*\.?\d+(?:e-?\d+)?', m.group(1))]
    return nums[:6] if len(nums) >= 6 else [1, 0, 0, 1, 0, 0]


def apply_mat(mat, rx, ry):
    a, b, c, d, e, f = mat
    return a * rx + c * ry + e, b * rx + d * ry + f


def to_mm(wx, wy):
    return wx * PT2MM, wy * PT2MM


def parse_path_pts(d):
    pts, cur = [], [0.0, 0.0]
    toks = re.findall(r'[MLHVZmlhv]|-?\d*\.?\d+(?:e-?\d+)?', d)
    i = 0
    while i < len(toks):
        t = toks[i]; i += 1
        if t == 'M':
            cur = [float(toks[i]), float(toks[i + 1])]; i += 2; pts.append(tuple(cur))
        elif t == 'L':
            cur = [float(toks[i]), float(toks[i + 1])]; i += 2; pts.append(tuple(cur))
        elif t == 'H':
            cur[0] = float(toks[i]); i += 1; pts.append(tuple(cur))
        elif t == 'V':
            cur[1] = float(toks[i]); i += 1; pts.append(tuple(cur))
        elif t == 'm':
            cur = [cur[0] + float(toks[i]), cur[1] + float(toks[i + 1])]; i += 2; pts.append(tuple(cur))
        elif t in 'Zz':
            pass
    return pts


def _dist_to_line(pt, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return math.hypot(pt[0] - a[0], pt[1] - a[1])
    return abs(dx * (a[1] - pt[1]) - dy * (a[0] - pt[0])) / L


# ── geometry extraction ──────────────────────────────────────────────────────
_WHITEISH = {'#fff', '#ffffff', '#ffffffff', 'white'}


def _solid_fill(attrs):
    """Return the path's solid fill colour (lowercased) if it has one that isn't
    'none' or white, else None. Handles fill="..." and style="fill:...".
    Datasheet arrowheads are solid-filled triangles while dimension/copper lines
    are fill="none" strokes — this distinguishes them for ANY fill colour, not
    just this datasheet's #4b4b4b."""
    m = re.search(r'fill="([^"]*)"', attrs) or re.search(r'fill:\s*([^;"\s]+)', attrs)
    if not m:
        return None
    c = m.group(1).strip().lower()
    if c == 'none' or c in _WHITEISH:
        return None
    return c


ARROW_MAX_MM = 2.5   # an arrowhead triangle's longest side is small


def _arrow_tips(svg):
    tips = []
    for pm in re.finditer(r'<path([^/]*/?>)', svg, re.S):
        p = pm.group(1)
        if not _solid_fill(p):                       # arrowhead = solid-filled
            continue
        tr_m = re.search(r'transform="([^"]*)"', p)
        d_m = re.search(r'\bd="([^"]*)"', p)
        if not d_m:
            continue
        mat = parse_matrix(tr_m.group(1) if tr_m else '')
        pts = parse_path_pts(d_m.group(1))
        if len(pts) < 3:
            continue
        mm = [to_mm(*apply_mat(mat, *pt)) for pt in pts[:3]]
        # size guard: skip large filled shapes (logos, filled regions); keep only
        # small triangles consistent with an arrowhead.
        side = max(math.hypot(mm[i][0] - mm[j][0], mm[i][1] - mm[j][1])
                   for i in range(3) for j in range(i + 1, 3))
        if side > ARROW_MAX_MM:
            continue
        cand = sorted([(_dist_to_line(mm[i], mm[(i + 1) % 3], mm[(i + 2) % 3]), mm[i])
                       for i in range(3)], reverse=True)
        tips.append(cand[0][1])
    # fallback: SVGs that use <marker> arrowheads (or stroked, not filled) carry
    # no solid triangle — recover tips from the dimension lines' marked endpoints.
    if len(tips) < 4:
        tips += _marker_tips(svg)
    return tips


def _marker_tips(svg):
    tips = []
    for pm in re.finditer(r'<(?:path|line)\b([^>]*?)/?>', svg, re.S):
        attrs = pm.group(1)
        if 'marker-end' not in attrs and 'marker-start' not in attrs:
            continue
        tr_m = re.search(r'transform="([^"]*)"', attrs)
        mat = parse_matrix(tr_m.group(1) if tr_m else '')
        d_m = re.search(r'\bd="([^"]*)"', attrs)
        if d_m:
            pts = parse_path_pts(d_m.group(1))
            if not pts:
                continue
            if 'marker-start' in attrs:
                tips.append(to_mm(*apply_mat(mat, *pts[0])))
            if 'marker-end' in attrs:
                tips.append(to_mm(*apply_mat(mat, *pts[-1])))
            continue
        g = {k: re.search(rf'{k}="(-?\d*\.?\d+)"', attrs) for k in ('x1', 'y1', 'x2', 'y2')}
        if all(g.values()):
            if 'marker-start' in attrs:
                tips.append(to_mm(*apply_mat(mat, float(g['x1'].group(1)), float(g['y1'].group(1)))))
            if 'marker-end' in attrs:
                tips.append(to_mm(*apply_mat(mat, float(g['x2'].group(1)), float(g['y2'].group(1)))))
    return tips


def _text_labels(svg):
    out = []
    for tm in re.finditer(r'<text[^>]*transform="([^"]*)"[^>]*font-size="([^"]*)"[^>]*>(.*?)</text>',
                          svg, re.S):
        mat_s, fs, body = tm.groups()
        nums = [float(x) for x in re.findall(r'-?\d*\.?\d+(?:e-?\d+)?', mat_s)]
        if len(nums) < 6:
            continue
        a, b, c, d, e, f = nums[:6]
        tsp = re.search(r'<tspan[^>]*y="(-?\d*\.?\d+)"[^>]*x="(-?\d*\.?\d+)', body)
        if not tsp:
            continue
        ty, tx = float(tsp.group(1)), float(tsp.group(2))
        wx, wy = a * tx + c * ty + e, b * tx + d * ty + f
        txt = re.sub(r'<[^>]+>', '', body).strip().replace('&#x00b1;', '±').replace('&#x00d8;', 'Ø')
        is_rot = abs(a) < abs(b)
        m1 = re.match(r'(\d+)[xX×]\s*(\d+\.?\d*)', txt.strip())
        m2 = re.match(r'Pitch\s*=\s*(\d+\.?\d*)', txt)
        m3 = re.match(r'R(\d+\.?\d*)', txt)
        m4 = re.match(r'^(\d+\.?\d*)$', txt.strip())
        is_radius = bool(m3)
        if m1:
            mult, val = int(m1.group(1)), float(m1.group(2))
        elif m2:
            mult, val = 1, float(m2.group(1))
        elif m3:
            mult, val = 1, float(m3.group(1))
        elif m4:
            mult, val = 1, float(m4.group(1))
        else:
            continue
        if val < 0.05 or val > 20:
            continue
        if re.fullmatch(r'[AB]\d+', txt) or re.fullmatch(r'[A-H]\d?', txt):
            continue
        if mult == 1 and val in (1, 2, 3, 4, 5, 6, 7, 8) and len(txt.strip()) == 1:
            continue
        out.append({'txt': txt, 'mult': mult, 'val': val, 'radius': is_radius,
                    'mmx': to_mm(wx, wy)[0], 'mmy': to_mm(wx, wy)[1],
                    'axis': 'V' if is_rot else 'H'})
    return out


def _segments(svg):
    segs = []
    for pm in re.finditer(r'<path([^/]*/?>)', svg, re.S):
        p = pm.group(1)
        if _solid_fill(p):                            # skip filled arrowheads
            continue
        tr_m = re.search(r'transform="([^"]*)"', p)
        d_m = re.search(r'\bd="([^"]*)"', p)
        if not d_m:
            continue
        mat = parse_matrix(tr_m.group(1) if tr_m else '')
        mm = [to_mm(*apply_mat(mat, *pt)) for pt in parse_path_pts(d_m.group(1))]
        for i in range(len(mm) - 1):
            segs.append((mm[i], mm[i + 1]))
    return segs


# ── matching ─────────────────────────────────────────────────────────────────
PERP_TOL = 1.5   # mm: how close an arrow tip must be to the label's dim line


def _candidates(dt, tips):
    lx, ly, axis = dt['mmx'], dt['mmy'], dt['axis']
    cands = []
    for tip in tips:
        perp = abs(tip[1] - ly) if axis == 'H' else abs(tip[0] - lx)
        if perp < PERP_TOL:
            par = tip[0] if axis == 'H' else tip[1]
            cands.append((par, tip))
    cands.sort()
    return cands


def _match_bracket(dt, tips):
    """Pass 1: smallest arrow pair bracketing the label."""
    cands = _candidates(dt, tips)
    if len(cands) < 2:
        return None
    lp = dt['mmx'] if dt['axis'] == 'H' else dt['mmy']
    best, best_span = None, 1e9
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            p1, p2 = cands[i][0], cands[j][0]
            if min(p1, p2) - 2 <= lp <= max(p1, p2) + 2:
                span = abs(p2 - p1)
                if 0.5 < span < best_span:
                    best, best_span = (cands[i][1], cands[j][1]), span
    return (best, best_span) if best else None


def _match_expected(dt, tips, scale):
    """Pass 2: arrow pair whose span ≈ value/scale, nearest the label."""
    cands = _candidates(dt, tips)
    if len(cands) < 2:
        return None
    lp = dt['mmx'] if dt['axis'] == 'H' else dt['mmy']
    expected = dt['val'] / scale
    best, best_cost = None, 1e9
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            p1, p2 = cands[i][0], cands[j][0]
            span = abs(p2 - p1)
            if span < 0.3:
                continue
            mid = (p1 + p2) / 2
            # cost: span error dominates; small penalty for distance from label
            cost = abs(span - expected) + 0.15 * abs(mid - lp)
            if cost < best_cost:
                best, best_cost = (cands[i][1], cands[j][1], span), cost
    if not best:
        return None
    t1, t2, span = best
    # accept only if span is within 18% (or 0.4mm) of expected
    if abs(span - expected) > max(0.18 * expected, 0.4):
        return None
    return ((t1, t2), span)


# ── witness projection ───────────────────────────────────────────────────────
def _seg_kind(a, b):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    if dy < 0.06 and dx > 0.1:
        return 'H', dx
    if dx < 0.06 and dy > 0.1:
        return 'V', dy
    return None, 0.0


def _witness_far(segs, tx, ty, axis):
    want = 'V' if axis == 'H' else 'H'
    best = None
    for a, b in segs:
        k, ln = _seg_kind(a, b)
        if k != want or ln <= 0.3:
            continue
        if axis == 'H' and abs(a[0] - tx) < 0.6:
            lo, hi = min(a[1], b[1]), max(a[1], b[1])
            if lo - 1.5 <= ty <= hi + 1.5 and (best is None or ln > best[0]):
                best = (ln, lo, hi)
        elif axis == 'V' and abs(a[1] - ty) < 0.6:
            lo, hi = min(a[0], b[0]), max(a[0], b[0])
            if lo - 1.5 <= tx <= hi + 1.5 and (best is None or ln > best[0]):
                best = (ln, lo, hi)
    if not best:
        return None
    _, lo, hi = best
    ref = ty if axis == 'H' else tx
    return lo if abs(lo - ref) > abs(hi - ref) else hi


def _cluster_1d(vals, eps):
    out = []
    for v in sorted(vals):
        if out and abs(v - out[-1][0]) <= eps:
            mem = out[-1][1] + [v]
            out[-1] = (sum(mem) / len(mem), mem)
        else:
            out.append((v, [v]))
    return out


def _contact_array(segs, scale, ax, ay, dims):
    """Generate the contact-finger pad array (e.g. USB-C 2×12) from:
       pitch (from a 'Pitch=' dim), per-row count (from an 'Nx' dim or row-span
       dim / pitch), and the two contact-row Y bands measured from the comb
       geometry. Returns pads in connector-relative datum-centred mm.
       Driven by parameters, not hard-coded to a part."""
    # pitch
    pitch = next((d['val'] for d in dims if d['axis'] == 'H' and 'Pitch' in d['txt']), 0.5)

    # contact-row pad heights from the V "Nx" dims (e.g. 24x0.85, 12x1.10)
    vh = sorted({round(d['val'], 2) for d in dims if d['axis'] == 'V' and 0.8 <= d['val'] <= 1.3})
    if not vh:
        vh = [0.85, 1.10]
    hmin, hmax = vh[0], vh[-1]

    # contact fingers = short vertical segs whose length ≈ a contact pad height
    # (excludes taller shield-tab edges ~1.6–2.3 mm and dimension lines). Limited
    # to the central x-corridor so the other view / annotations don't leak in.
    fingers = []
    for a, b in segs:
        dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
        L = dy * scale
        if dx < 0.06 and (hmin - 0.1) <= L <= (hmax + 0.2):
            fx = ((a[0]+b[0])/2 - ax) * scale
            fy = -((a[1]+b[1])/2 - ay) * scale
            if abs(fx) < 3.6:
                fingers.append((fx, fy, L))
    if len(fingers) < 8:
        return []

    # cluster finger Y into rows by gap; keep only DENSE rows (the comb/hatched
    # contact rows have many segments; stray dim lines cluster as n=1).
    fys = sorted(f[1] for f in fingers)
    rows = []
    for y in fys:
        if rows and abs(y - rows[-1][-1]) < 0.4:
            rows[-1].append(y)
        else:
            rows.append([y])
    rows = [r for r in rows if len(r) >= 8]
    if not rows:
        return []
    row_cy = sorted(sum(r)/len(r) for r in rows)

    # per-row count: largest H span that is a near-integer multiple of pitch and
    # fits inside the part (the contact-row width, e.g. 5.50 → 12 pads).
    hspans = sorted([d['val'] for d in dims if d['axis'] == 'H' and 'Pitch' not in d['txt']
                     and 2*pitch < d['val']], reverse=True)
    span = next((v for v in hspans if abs(round(v/pitch) - v/pitch) < 0.1), None)
    if span:
        n_per_row = round(span / pitch) + 1
        x_center = next((sum(d['edges'])/2 for d in dims
                         if d['axis'] == 'H' and abs(d['val']-span) < 1e-3), 0.0)
    else:
        fxs = [f[0] for f in fingers]
        n_per_row = max(1, round((max(fxs)-min(fxs))/pitch) + 1)
        x_center = (min(fxs)+max(fxs))/2

    pad_w = next((d['val'] for d in dims if d['axis'] in ('H', 'V') and 0.28 <= d['val'] <= 0.35), 0.30)
    # taller pads → top row, shorter → bottom row
    row_h = {row_cy[-1]: hmax, row_cy[0]: hmin}
    if len(row_cy) == 1:
        row_h = {row_cy[0]: hmax}

    pads = []
    half = (n_per_row - 1) / 2.0
    for cy in row_cy:
        h = row_h.get(cy, hmin)
        for i in range(n_per_row):
            cx = round(x_center + (i - half) * pitch, 3)
            pads.append({'cx': cx, 'cy': round(cy, 3),
                         'w': round(pad_w, 3), 'h': round(h, 3),
                         'w_src': f'Pitch={pitch}', 'h_src': 'array', 'kind': 'contact'})
    return pads


# ── main ─────────────────────────────────────────────────────────────────────
def _in_crop(pt, crop, margin=1.0):
    """True if a page-mm point is inside the crop box (x0,y0,x1,y1, page-mm),
    with a margin so an arrow/witness just outside the drag isn't lost."""
    if crop is None:
        return True
    x0, y0, x1, y1 = crop
    return (x0 - margin <= pt[0] <= x1 + margin and
            y0 - margin <= pt[1] <= y1 + margin)


def dimension_pads(svg_path, crop=None):
    """Solve the dimensioned model. If `crop` is given (x0,y0,x1,y1 in page-mm),
    only geometry inside that box is used — so a user can rubber-band one
    reference dimension + the component and downsize JUST that, with no
    interference from other parts/views elsewhere on the sheet."""
    svg = Path(svg_path).read_text(encoding='utf-8', errors='replace')
    if crop is not None:                       # normalise corner order
        x0, y0, x1, y1 = crop
        crop = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    tips = [t for t in _arrow_tips(svg) if _in_crop(t, crop)]
    labels = [d for d in _text_labels(svg) if _in_crop((d['mmx'], d['mmy']), crop)]
    segs = [s for s in _segments(svg) if _in_crop(s[0], crop) or _in_crop(s[1], crop)]

    # PASS 1 — establish the drawing scale from confidently-bracketed dims
    p1 = []
    for dt in labels:
        if dt['radius']:
            continue
        m = _match_bracket(dt, tips)
        if m:
            (_, span) = m
            p1.append(dt['val'] / span)
    if not p1:
        raise ValueError(
            "could not establish drawing scale: no dimension was bracketed by an "
            "arrow pair in pass 1. Check arrowhead detection (fill colour / "
            "<marker> style) and that page units are PostScript points.")
    scale = median(p1)

    # PASS 2 — re-match every dim using expected span = value / scale
    matched = []
    for dt in labels:
        if dt['radius']:
            continue
        m = _match_expected(dt, tips, scale)
        if not m:
            continue
        (t1, t2), span = m
        # perp feature coordinate (svg mm): where this dim's witness lines land on
        # the copper — H-dim → feature row Y, V-dim → feature col X. Averaged over
        # the two witnesses; used to pair an H (width) with a V (height) dim that
        # bound the SAME pad.
        f1 = _witness_far(segs, t1[0], t1[1], dt['axis'])
        f2 = _witness_far(segs, t2[0], t2[1], dt['axis'])
        perp = None
        if f1 is not None and f2 is not None:
            perp = (f1 + f2) / 2
        matched.append({**dt, 'tip1': t1, 'tip2': t2, 'span': span,
                        'scale': dt['val'] / span, 'perp_svg': perp})

    # VIEWS — 2-means on label X
    xs = sorted(d['mmx'] for d in matched)
    if len(xs) >= 2:
        c0, c1 = xs[0], xs[-1]
        for _ in range(40):
            lo = [x for x in xs if abs(x - c0) <= abs(x - c1)]
            hi = [x for x in xs if abs(x - c0) > abs(x - c1)]
            if not lo or not hi:
                break
            n0, n1 = sum(lo) / len(lo), sum(hi) / len(hi)
            if abs(n0 - c0) < 1e-6 and abs(n1 - c1) < 1e-6:
                break
            c0, c1 = n0, n1
        split = (c0 + c1) / 2
    else:
        split = 1e9

    # A crop means the user already isolated ONE component, so don't 2-means it
    # into two views (which would fragment it and split the overall dimension off
    # from the body, moving the datum). One crop = one view = one datum.
    if crop is not None:
        split = 1e9

    groups = {'left': [], 'right': []}
    for d in matched:
        (groups['left'] if d['mmx'] < split else groups['right']).append(d)

    views = {}
    for vname, vd in groups.items():
        if not vd:
            continue
        # Datum = midpoint of the largest-VALUE dim per axis (the overall part
        # dimension, whose arrows sit on the outer edges). Using value not span
        # keeps the datum stable when a crop drops some dimensions — otherwise a
        # mis-bound span could move the centre and clip the part.
        hd = sorted([d for d in vd if d['axis'] == 'H'], key=lambda x: -x['val'])
        vv = sorted([d for d in vd if d['axis'] == 'V'], key=lambda x: -x['val'])
        ax = (hd[0]['tip1'][0] + hd[0]['tip2'][0]) / 2 if hd else sum(d['mmx'] for d in vd) / len(vd)
        ay = (vv[0]['tip1'][1] + vv[0]['tip2'][1]) / 2 if vv else sum(d['mmy'] for d in vd) / len(vd)
        vedges, hedges, dims = [], [], []
        for d in vd:
            t1, t2 = d['tip1'], d['tip2']
            if d['axis'] == 'H':
                e1, e2 = round((t1[0] - ax) * scale, 3), round((t2[0] - ax) * scale, 3)
                vedges += [e1, e2]
                # perp = feature row Y in connector-relative mm (Y flipped)
                perp = round(-(d['perp_svg'] - ay) * scale, 3) if d['perp_svg'] is not None else None
            else:
                e1, e2 = round(-(t1[1] - ay) * scale, 3), round(-(t2[1] - ay) * scale, 3)
                hedges += [e1, e2]
                # perp = feature col X in connector-relative mm
                perp = round((d['perp_svg'] - ax) * scale, 3) if d['perp_svg'] is not None else None
            dims.append({'txt': d['txt'], 'val': d['val'], 'mult': d['mult'],
                         'axis': d['axis'], 'edges': sorted([e1, e2]), 'perp': perp})
        views[vname] = {'anchor_svg': [round(ax, 3), round(ay, 3)],
                        'vertical_edges': sorted(set(vedges)),
                        'horizontal_edges': sorted(set(hedges)),
                        'dims': dims}

    # OVERLAY + MERGE
    all_v = [e for v in views.values() for e in v['vertical_edges']]
    all_h = [e for v in views.values() for e in v['horizontal_edges']]
    mv = [round(m, 3) for m, _ in _cluster_1d(all_v, 0.08)]
    mh = [round(m, 3) for m, _ in _cluster_1d(all_h, 0.08)]

    def conf(edge, axis):
        return sum(1 for v in views.values()
                   if any(abs(edge - e) <= 0.08
                          for e in (v['vertical_edges'] if axis == 'V' else v['horizontal_edges'])))

    overlay = {'vertical_edges':   [{'x': e, 'views': conf(e, 'V')} for e in mv],
               'horizontal_edges': [{'y': e, 'views': conf(e, 'H')} for e in mh]}

    radii = [{'r_mm': d['val'], 'txt': d['txt']} for d in labels if d['radius']]

    # ── PAD ASSEMBLY: pair a width-dim (H) with a height-dim (V) that bound the
    # SAME pad, giving a rectangle at an absolute datum-centred coordinate.
    # Two dims bound the same pad when the H-dim's feature row (perp Y) lies
    # within the V-dim's [y1,y2] span AND the V-dim's feature col (perp X) lies
    # within the H-dim's [x1,x2] span. This is the "combine length + width into
    # one element at its real placement" step.
    alldims = [d for v in views.values() for d in v['dims']]
    extW = (max(mv) - min(mv)) if mv else 1e9
    extH = (max(mh) - min(mh)) if mh else 1e9
    # a pad-defining dim is never the overall span — drop dims ≥70% of the extent
    # (those are outline/overall dims, e.g. 8.64 width, 6.20 height, 6.80 PCB edge)
    def is_pad_dim(d):
        ext = extW if d['axis'] == 'H' else extH
        return d['val'] < 0.7 * ext
    hdims = [d for d in alldims if d['axis'] == 'H' and d['perp'] is not None and is_pad_dim(d)]
    vdims = [d for d in alldims if d['axis'] == 'V' and d['perp'] is not None and is_pad_dim(d)]
    pads = []
    TOL = 0.4
    for hd in hdims:
        hx1, hx2 = hd['edges']
        hy = hd['perp']                 # feature row Y this H-dim sits on
        for vd in vdims:
            vy1, vy2 = vd['edges']
            vx = vd['perp']             # feature col X this V-dim sits on
            if (min(vy1, vy2) - TOL <= hy <= max(vy1, vy2) + TOL and
                    min(hx1, hx2) - TOL <= vx <= max(hx1, hx2) + TOL):
                cx = round((hx1 + hx2) / 2, 3)
                cy = round((vy1 + vy2) / 2, 3)
                pads.append({'cx': cx, 'cy': cy,
                             'w': round(abs(hx2 - hx1), 3), 'h': round(abs(vy2 - vy1), 3),
                             'w_src': hd['txt'], 'h_src': vd['txt']})
    for pd in pads:
        pd.setdefault('kind', 'tab')

    # ── CONTACT ARRAY: expand the centre finger row(s) from pitch × count ─────
    ref = views.get('left') or next(iter(views.values()), None)
    if ref:
        rax, ray = ref['anchor_svg']
        contacts = _contact_array(segs, scale, rax, ray, ref['dims'])
        pads += contacts

    # dedupe pads coincident within 0.15mm (same pad found from both views)
    uniq_pads = []
    for pd in pads:
        if not any(abs(pd['cx'] - q['cx']) < 0.15 and abs(pd['cy'] - q['cy']) < 0.15 and
                   abs(pd['w'] - q['w']) < 0.15 and abs(pd['h'] - q['h']) < 0.15
                   for q in uniq_pads):
            uniq_pads.append(pd)

    return {
        'source_svg': str(svg_path),
        'scale': round(scale, 6),
        'view_split_x': round(split, 2),
        'views': views,
        'overlay_merged': overlay,
        'radii': radii,
        'pads': uniq_pads,
        'extent_mm': {'width': round(max(mv) - min(mv), 3) if mv else 0,
                      'height': round(max(mh) - min(mh), 3) if mh else 0},
        'n_dims_matched': len(matched),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('svg')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    svg = args.svg
    r = dimension_pads(svg)
    if args.output:
        out = args.output
    else:
        state = Path(os.environ.get(
            'DESIGNSTUDIO_ARTIFACT_ROOT',
            Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')) /
            'designstudio' / 'artifacts'))
        out = state / 'dimensions' / (Path(svg).stem + '.pads.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2))
    print(f"scale={r['scale']:.5f}  split_x={r['view_split_x']}  matched={r['n_dims_matched']}  → {out}")
    print(f"overall extent (merged): {r['extent_mm']['width']} × {r['extent_mm']['height']} mm")
    for vn, v in r['views'].items():
        print(f"\n[{vn}]  {len(v['dims'])} dims")
        for d in sorted(v['dims'], key=lambda x: (x['axis'], x['val'])):
            print(f"   {d['axis']} {d['txt']:10s} = {d['val']:.2f}mm  edges={d['edges']}")
    print("\n── OVERLAY merged feature edges ([n]=#views confirming) ──")
    print("  V(x): " + ", ".join(f"{e['x']:+.2f}[{e['views']}]" for e in r['overlay_merged']['vertical_edges']))
    print("  H(y): " + ", ".join(f"{e['y']:+.2f}[{e['views']}]" for e in r['overlay_merged']['horizontal_edges']))
    if r['radii']:
        print("  radii: " + ", ".join(x['txt'] for x in r['radii']))
    print(f"\n── ASSEMBLED PADS (absolute datum-centred mm): {len(r['pads'])} ──")
    for pd in sorted(r['pads'], key=lambda p: (round(p['cy'], 1), round(p['cx'], 1))):
        print(f"   @({pd['cx']:+.3f},{pd['cy']:+.3f})  {pd['w']:.3f}×{pd['h']:.3f} mm"
              f"   [W:{pd['w_src']}  H:{pd['h_src']}]")
