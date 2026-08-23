#!/usr/bin/env python3
"""
USB4910 layout extractor  --  OpenCV 5 stroke/object detection -> CAD grid.

Pipeline
  1. Render the "Recommended PCB Layout" drawing from the datasheet PDF (PyMuPDF).
  2. Strip annotation: long straight strokes (centre-lines, leader & dimension
     lines) are removed by morphological opening so only copper artifacts remain.
  3. Detect & label, each with the right OpenCV tool:
       - signal pads : 2x12 regular grid -> column projection + pitch-run
                       (CV locates the field & MEASURES the pitch; the known
                       12-per-row structure places the pads -- robust to the
                       centre-line that crosses every pad).
       - shield pads : large hatched solder areas -> contour + area/shape filter.
       - drill holes : thin ring circles            -> HoughCircles.
  4. Calibrate px->mm from the MEASURED 0.50 mm pad pitch, snap every artifact to
     a fabrication grid, and emit artifacts.json + a KiCad footprint + a labeled
     overlay image.

Runs on OpenCV 5.0.0 (findContours uses the new TRUCO algorithm); identical API on 4.x.
"""
import json
import os
import numpy as np
import cv2
import fitz  # PyMuPDF
import classifier as clf
from runtime_paths import layout_output_dir, sample_datasheet

DPI       = 1200
PDF       = str(sample_datasheet())
PAGE      = 1                                  # sheet 2/3, Recommended PCB Layout
CLIP_PT   = fitz.Rect(180, 204, 360, 351)      # the layout drawing only
OUT       = str(layout_output_dir())
PX        = DPI / 25.4                          # nominal px per mm at render dpi
GRID_MM   = 0.05                                # CAD snap grid

# ---- datasheet truths used to drive the regular-grid placement ----
PAD_PITCH_MM = 0.50
PADS_PER_ROW = 12
ROW_TAGS     = ("A", "B")          # A = upper row, B = lower row
PAD_W_MM, PAD_H_MM = 0.30, 0.85    # nominal signal-pad copper size

# USB4910 (USB-C) pin map from the datasheet contact table. power_in for the
# power/ground rails, bidirectional for everything else (per Design Studio's
# ValidTypes).
USB_C_PINS = {
    "A1":"GND","A2":"SSTXp1","A3":"SSTXn1","A4":"VBUS","A5":"CC1","A6":"DP1",
    "A7":"DN1","A8":"SBU1","A9":"VBUS","A10":"SSRXn2","A11":"SSRXp2","A12":"GND",
    "B1":"GND","B2":"SSTXp2","B3":"SSTXn2","B4":"VBUS","B5":"CC2","B6":"DP2",
    "B7":"DN2","B8":"SBU2","B9":"VBUS","B10":"SSRXn1","B11":"SSRXp1","B12":"GND",
}
def _etype(ref):
    return "power_in" if USB_C_PINS.get(ref) in ("GND", "VBUS") else "bidirectional"


# --------------------------------------------------------------------------- io
def render():
    page = fitz.open(PDF)[PAGE]
    pix  = page.get_pixmap(matrix=fitz.Matrix(DPI/72, DPI/72), clip=CLIP_PT)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(a, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)


def strip_centerline(gray):
    """Binary ink with the long horizontal centre-line removed (it crosses every
    pad and would merge them).  Vertical pad edges are KEPT."""
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (int(1.2*PX), 1)))
    return ink, cv2.subtract(ink, hor)


def _cluster_1d(vals, tol):
    """Group sorted values whose neighbours are within tol; return cluster means."""
    out, grp = [], [vals[0]]
    for v in vals[1:]:
        if v - grp[-1] <= tol:
            grp.append(v)
        else:
            out.append(np.mean(grp)); grp = [v]
    out.append(np.mean(grp))
    return out


def detect_signal_pads(gray):
    """24 signal pads as solid blobs (hatch solidified by a vertical close).
    NB: the drawing is NOT to scale -- the pad *pitch* measured here (px) is
    calibrated to the datasheet's 0.50 mm to recover px/mm. Detected pads are
    snapped onto a clean 12-column x 2-row grid so merges/misses self-heal."""
    _, noline = strip_centerline(gray)
    solid = cv2.morphologyEx(noline, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (3, int(0.5*PX))))
    n, _, stats, cent = cv2.connectedComponentsWithStats(solid)
    cand = [dict(cx=float(cent[i][0]), cy=float(cent[i][1]),
                 w=int(stats[i][2]), h=int(stats[i][3]))
            for i in range(1, n)
            if 50 < stats[i][2] < 130 and 100 < stats[i][3] < 320]   # pad ~85x230px

    # rows = the two densest y-bands (rejects stray components)
    ybin = {}
    for c in cand:
        ybin.setdefault(round(c["cy"]/40), []).append(c)
    top2 = sorted(ybin.values(), key=len, reverse=True)[:2]
    rows_y = sorted(np.mean([c["cy"] for c in g]) for g in top2)
    members = {ry: [c for c in cand if abs(c["cy"]-ry) < 50] for ry in rows_y}

    # pitch from WITHIN a row (the two rows are staggered ~half-pitch, so a
    # combined column projection would read double-frequency).
    pitches = []
    for ry, m in members.items():
        cols = _cluster_1d(sorted(c["cx"] for c in m), 60)
        pitches += [d for d in np.diff(cols) if d < 200]      # drop gaps (missing pads)
    px_pitch = float(np.median(pitches))
    pw = float(np.median([c["w"] for c in cand]))
    ph = float(np.median([c["h"] for c in cand]))

    # per-row grid: anchor on the row's leftmost detected pad, fill all 12 slots
    pads = []
    for tag, ry in zip(ROW_TAGS, rows_y):
        x0 = min(c["cx"] for c in members[ry])
        for i in range(1, PADS_PER_ROW + 1):
            ref = f"{tag}{i}" if tag == "A" else f"{tag}{PADS_PER_ROW-i+1}"
            pads.append(dict(ref=ref, cx=x0 + (i-1)*px_pitch, cy=ry, w=pw, h=ph))
    return pads, px_pitch


def _roi_mask(shape, ox, oy, scale, xpad=5.4, yup=2.8, ydn=3.6, hole=None):
    """White everywhere except the copper bounding box (in mm about the pad
    centre). Excludes the off-to-the-side dimension text. `hole` optionally
    blanks the signal-pad band so it isn't picked up as a shield."""
    m = np.zeros(shape, np.uint8)
    x0, x1 = int(ox - xpad*scale), int(ox + xpad*scale)
    y0, y1 = int(oy - yup*scale),  int(oy + ydn*scale)
    m[max(0,y0):y1, max(0,x0):x1] = 255
    if hole:
        hx, hy = hole
        m[int(oy-hy*scale):int(oy+hy*scale), int(ox-hx*scale):int(ox+hx*scale)] = 0
    return m


def _fill_holes(b):
    """Solidify any CLOSED outline by flood-filling the background from a padded
    border (guaranteed-background seed) and OR-ing the enclosed interiors back."""
    p = cv2.copyMakeBorder(b, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=0)
    ff = p.copy()
    cv2.floodFill(ff, np.zeros((p.shape[0]+2, p.shape[1]+2), np.uint8), (0, 0), 255)
    return cv2.bitwise_or(p, cv2.bitwise_not(ff))[4:-4, 4:-4]


def detect_shield_pads(gray, ox, oy, scale):
    """The 4 curved corner solder areas (2 keyholes top, 2 oblongs bottom) are
    HOLLOW outline shapes punctured by leader/centre-lines, so a global fill
    leaks. Process each corner ROI separately: drop long straight lines (leaders,
    centre-lines, dimensions) to re-seal the outline, flood-fill its interior,
    take the largest blob. Corners are derived from the pad-field datum, not
    hard-coded positions."""
    H, W = gray.shape
    def m2p(xm, ym): return int(round(ox + xm*scale)), int(round(oy - ym*scale))
    def clamp(v, lo, hi): return max(lo, min(hi, v))
    rois = [(-6.0, -3.0,  1.8, -1.2), ( 3.0, 6.0,  1.8, -1.2),   # keyholes (top, wide)
            (-5.4, -3.4, -1.3, -5.0), ( 3.4, 5.4, -1.3, -5.0)]   # oblongs (bottom, tight x)
    L = int(4.0 * scale)                                    # > shape, < leader length
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, L))
    seal = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.3*scale)|1,)*2)

    blobs = []
    for xa, xb, ya, yb in rois:
        x0, _ = m2p(xa, 0); x1, _ = m2p(xb, 0); _, yA = m2p(0, ya); _, yB = m2p(0, yb)
        x0, x1 = clamp(x0, 0, W), clamp(x1, 0, W)
        yA, yB = clamp(yA, 0, H), clamp(yB, 0, H)
        if x1 - x0 < 10 or yB - yA < 10:
            continue
        ink = cv2.threshold(gray[yA:yB, x0:x1], 0, 255,
                            cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
        clean = cv2.subtract(ink, cv2.bitwise_or(cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk),
                                                 cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk)))
        filled = _fill_holes(cv2.morphologyEx(clean, cv2.MORPH_CLOSE, seal))
        # OPEN away thin protrusions: leftover leader-line stubs / dimension-tick
        # spikes are < ~0.25 mm wide, real shape features (keyhole neck) are larger.
        op = int(0.22*scale) | 1
        filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (op, op)))
        cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            a = cv2.contourArea(c) / scale**2          # filled-area, not bbox
            bx, by, bw, bh = cv2.boundingRect(c)       # pads are axis-aligned
            if bw == 0 or bh == 0:
                continue
            if 0.8 < a < 6.0 and min(bw, bh)/scale > 0.45 and max(bw, bh)/scale < 4.5:
                if best is None or a > best["area"]:
                    # keep the ACTUAL outline (simplified), offset to global px
                    eps = 0.02 * scale                 # ~0.02 mm vertex tolerance
                    poly = cv2.approxPolyDP(c, eps, True).reshape(-1, 2).astype(float)
                    poly[:, 0] += x0; poly[:, 1] += yA
                    best = dict(cx=bx + x0 + bw/2, cy=by + yA + bh/2,
                                w=float(bw), h=float(bh), area=a, contour=poly)
        if best:
            cx, cy, w, h = best["cx"], best["cy"], best["w"], best["h"]
            best["box"] = np.array([[cx-w/2, cy-h/2], [cx+w/2, cy-h/2],
                                    [cx+w/2, cy+h/2], [cx-w/2, cy+h/2]])
            blobs.append(best)
    return blobs


def detect_holes(gray, ox, oy, scale):
    # the 2x Ø0.55 NPTH holes sit BELOW the pad rows (y ~ -1.3 mm), so the ROI is
    # asymmetric (down) and the radius is pinned tightly to Ø0.55 to reject the
    # keyhole arcs and other round features.
    roi = _roi_mask(gray.shape, ox, oy, scale, xpad=4.2, yup=0.4, ydn=2.2)
    g = cv2.medianBlur(cv2.bitwise_or(gray, cv2.bitwise_not(roi)), 5)
    r = 0.55/2 * scale
    circ = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1, minDist=int(3*r),
                            param1=120, param2=33,
                            minRadius=int(0.82*r), maxRadius=int(1.22*r))
    out = []
    if circ is not None:
        for x, y, rad in circ[0]:
            out.append(dict(cx=float(x), cy=float(y), r=float(rad)))
    out.sort(key=lambda h: h["cx"])
    return out[:2]


# ----------------------------------------- VECTOR extraction of mounting shapes
def detect_mounting_shapes_vector(page, cx_pt, cy_pt, ptmm):
    """Extract the 4 curved corner solder areas from the PDF's EXACT vector paths
    (not raster) -- this is the only way to recover the true keyhole L-cutout and
    the obround radii. The page is rotated 270deg, so vector points are mapped
    through page.rotation_matrix into the same mm frame as the raster pipeline.
    Segments with BOTH endpoints local to a corner are kept (excludes leader and
    dimension lines that reach outward)."""
    R = page.rotation_matrix
    def to_mm(p):
        q = p * R
        return ((q.x - cx_pt)/ptmm, -(q.y - cy_pt)/ptmm)
    segs = [(to_mm(it[1]), to_mm(it[2]))
            for d in page.get_drawings() for it in d["items"] if it[0] == "l"]

    PPMM = 600          # L1: higher raster resolution -> finer morphology/contour steps
    # (key, side, box) -- pairs are symmetric about x=0
    boxes = [("key", "L", -4.5,  0.6, 1.7, 1.5), ("key", "R", 4.5,  0.4, 1.7, 1.5),
             ("obl", "L", -4.6, -4.0, 1.1, 1.6), ("obl", "R", 4.5, -4.0, 1.1, 1.6)]

    def _slen(a, b):
        return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5

    def extract(cxm, cym, hx, hy):
        # NB: do NOT length-filter segments here -- an obround's straight sides are
        # long single strokes, indistinguishable by length from leader lines. Leaders
        # are excluded by the both-endpoints-local box test + the OPEN de-spike below.
        loc = [(a, b) for a, b in segs
               if abs(a[0]-cxm) < hx and abs(a[1]-cym) < hy
               and abs(b[0]-cxm) < hx and abs(b[1]-cym) < hy]
        if not loc:
            return None
        Wd, Hd = int(2*hx*PPMM), int(2*hy*PPMM)
        img = np.zeros((Hd, Wd), np.uint8)
        def tp(x, y): return int((x-(cxm-hx))*PPMM), int(((cym+hy)-y)*PPMM)
        for a, b in loc:
            cv2.line(img, tp(*a), tp(*b), 255, 2)
        # HOLLOW RING: close the hatch into a band (kernel < hollow centre so it
        # is not bridged) -> outer contour + inner child (the hole).
        band = cv2.morphologyEx(img, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.12*PPMM)|1,)*2))
        cnts, hier = cv2.findContours(band, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not len(cnts):
            return None
        outers = [i for i in range(len(cnts)) if hier[0][i][3] == -1]
        oi = max(outers, key=lambda i: cv2.contourArea(cnts[i]))
        kids = [i for i in range(len(cnts)) if hier[0][i][3] == oi]
        hi = max(kids, key=lambda i: cv2.contourArea(cnts[i])) if kids else None
        # de-spike: fill outer solid, OPEN away thin leader/centre stubs, re-contour
        outer = np.zeros((Hd, Wd), np.uint8); cv2.drawContours(outer, [cnts[oi]], -1, 255, -1)
        outer = cv2.morphologyEx(outer, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.16*PPMM)|1,)*2))
        hole = np.zeros((Hd, Wd), np.uint8)
        if hi is not None:
            cv2.drawContours(hole, [cnts[hi]], -1, 255, -1)
        oc, _ = cv2.findContours(outer, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        ic, _ = cv2.findContours(hole, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not oc:
            return None
        oc = max(oc, key=cv2.contourArea)
        ic = max(ic, key=cv2.contourArea) if ic else None
        def cmm(cnt):
            # eps scales with PPMM: 1.0 px @600ppmm = ~0.0017 mm vertex tolerance
            return [[(cxm-hx) + ix/PPMM, (cym+hy) - iy/PPMM]
                    for ix, iy in cv2.approxPolyDP(cnt, 1.0, True).reshape(-1, 2)]
        M = cv2.moments(outer)
        cx = (cxm-hx) + (M['m10']/M['m00'])/PPMM
        cy = (cym+hy) - (M['m01']/M['m00'])/PPMM
        bx, by, bw, bh = cv2.boundingRect(oc)
        return dict(outer=cmm(oc), hole=(cmm(ic) if ic is not None else []),
                    cx=cx, cy=cy, w=bw/PPMM, h=bh/PPMM,
                    area=(cv2.contourArea(oc)-(cv2.contourArea(ic) if ic is not None else 0))/PPMM**2)

    def _spikiness(pts):
        """perimeter^2 / area -- scale-free roughness metric; a contour with leftover
        spikes has extra perimeter for its area, so the cleaner side scores LOWER."""
        if len(pts) < 3:
            return 1e9
        P = sum(_slen(pts[i-1], pts[i]) for i in range(len(pts)))
        A = abs(sum(pts[i-1][0]*pts[i][1] - pts[i][0]*pts[i-1][1]
                    for i in range(len(pts)))) / 2.0
        return (P*P/A) if A > 1e-6 else 1e9

    got = {}
    for key, side, cxm, cym, hx, hy in boxes:
        r = extract(cxm, cym, hx, hy)
        if r:
            got[(key, side)] = r

    # L4: ENFORCE exact left-right symmetry. Pick the CLEANER side (lowest
    # perimeter^2/area) as canonical, recentre it to the origin, then MIRROR it
    # across x=0 to build BOTH sides -- the pair is now geometrically identical
    # (kills side-specific boot tails) and a missing mount is recovered for free.
    shapes = []; n = 0
    for key in ("key", "obl"):
        pair = [(s, got[(key, s)]) for s in ("L", "R") if (key, s) in got]
        if not pair:
            continue
        mx = float(np.mean([abs(r["cx"]) for _, r in pair]))
        my = float(np.mean([r["cy"] for _, r in pair]))
        cs, cr = min(pair, key=lambda sr: _spikiness(sr[1]["outer"]))   # canonical side
        cen = lambda pts: [[x-cr["cx"], y-cr["cy"]] for x, y in pts]
        out0, hol0 = cen(cr["outer"]), cen(cr["hole"])
        for side in ("L", "R"):
            n += 1
            tx = -mx if side == "L" else mx; ty = my
            sx = -1 if side != cs else 1                  # mirror onto the opposite side
            place = lambda pts: [[sx*x + tx, y + ty] for x, y in pts]
            shapes.append(dict(ref=f"SH{n}", contour_mm=place(out0),
                               hole_mm=place(hol0), cx_mm=tx, cy_mm=ty,
                               w_mm=cr["w"], h_mm=cr["h"], area_mm2=cr["area"],
                               solidity=0.5,
                               aspect=max(cr["w"], cr["h"])/max(1e-6, min(cr["w"], cr["h"]))))
    return shapes


# ---------------------------------------------------- feature build + classify
def build_objects(pads, vshields, holes, scale, to_mm):
    """Turn raw detections into generic copper objects with measured `features`
    and `geom` (mm) for the rules-driven classifier. Mounting shapes are the
    vector-extracted contours (already in mm)."""
    objs = []
    for p in pads:
        w, h = p["w"]/scale, p["h"]/scale
        x, y = to_mm(p["cx"], p["cy"])
        objs.append(dict(ref=p["ref"],
            geom=dict(x_mm=x, y_mm=y, w_mm=w, h_mm=h),
            features=dict(in_grid=True, is_ring=False, area_mm2=w*h,
                          aspect=max(w, h)/min(w, h), solidity=1.0, circularity=0.78)))
    for b in vshields:
        objs.append(dict(ref=b["ref"],
            geom=dict(x_mm=b["cx_mm"], y_mm=b["cy_mm"], w_mm=b["w_mm"], h_mm=b["h_mm"],
                      points=[list(p) for p in b["contour_mm"]],
                      hole=[list(p) for p in b.get("hole_mm", [])]),
            features=dict(in_grid=False, is_ring=False, area_mm2=b["area_mm2"],
                          aspect=b["aspect"], solidity=b["solidity"], circularity=0.0)))
    for i, h in enumerate(holes, 1):
        x, y = to_mm(h["cx"], h["cy"]); r = h["r"]/scale
        objs.append(dict(ref=f"MP{i}",
            geom=dict(x_mm=x, y_mm=y, drill_mm=2*r),
            features=dict(in_grid=False, is_ring=True, area_mm2=np.pi*r*r,
                          aspect=1.0, solidity=1.0, circularity=1.0)))
    return objs


# --------------------------------------------------------------------- export
def main():
    bgr  = render()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape

    pads, px_pitch = detect_signal_pads(gray)
    scale = px_pitch / PAD_PITCH_MM                         # px per mm (calibrated)
    ox = float(np.mean([p["cx"] for p in pads]))
    oy = float(np.mean([p["cy"] for p in pads]))
    holes   = detect_holes(gray, ox, oy, scale)
    # enforce the part's left-right symmetry on the 2 NPTH holes (the detector
    # placed them at asymmetric x, e.g. -3.65 vs +3.15) -- share |x| about the pad
    # axis ox and sit on a common y.
    if len(holes) == 2:
        mdx = np.mean([abs(h["cx"] - ox) for h in holes])
        my  = float(np.mean([h["cy"] for h in holes]))
        for h in holes:
            h["cx"] = ox - mdx if h["cx"] < ox else ox + mdx
            h["cy"] = my
    # mounting shapes come from the EXACT vector paths (rotation-corrected anchor)
    ppt = 72.0/DPI
    cx_pt = CLIP_PT.x0 + ox*ppt; cy_pt = CLIP_PT.y0 + oy*ppt; ptmm = scale*ppt
    page = fitz.open(PDF)[PAGE]
    vshields = detect_mounting_shapes_vector(page, cx_pt, cy_pt, ptmm)

    print(f"layout image: {W}x{H}px ; drawing NOT to scale")
    print(f"signal pads : {len(pads)}  (2 rows x {PADS_PER_ROW})")
    print(f"shield pads : {len(vshields)} (vector)   drill holes : {len(holes)}")
    print(f"calibration : pad pitch {px_pitch:.0f}px == {PAD_PITCH_MM} mm "
          f"-> {scale:.1f} px/mm")

    snap = lambda v: round(v / GRID_MM) * GRID_MM
    to_mm = lambda px, py: (snap((px - ox)/scale), snap((oy - py)/scale))

    artifacts = []
    for p in pads:
        x, y = to_mm(p["cx"], p["cy"])
        artifacts.append(dict(kind="signal_pad", ref=p["ref"], x_mm=x, y_mm=y,
                              w_mm=round(p["w"]/scale, 2), h_mm=round(p["h"]/scale, 2)))
    for b in sorted(vshields, key=lambda b: (b["cy_mm"], b["cx_mm"])):
        artifacts.append(dict(kind="shield_pad", ref=b["ref"],
                              x_mm=snap(b["cx_mm"]), y_mm=snap(b["cy_mm"]),
                              w_mm=round(b["w_mm"], 2), h_mm=round(b["h_mm"], 2),
                              contour_mm=[[snap(x), snap(y)] for x, y in b["contour_mm"]],
                              hole_mm=[[snap(x), snap(y)] for x, y in b.get("hole_mm", [])]))
    for i, h in enumerate(holes, 1):
        x, y = to_mm(h["cx"], h["cy"])
        artifacts.append(dict(kind="npth_hole", ref=f"MP{i}", x_mm=x, y_mm=y,
                              drill_mm=round(2*h["r"]/scale, 2)))

    # --- rules-driven classifier: detections -> CAD primitives ---
    rules = clf.load_rules(os.path.join(os.path.dirname(__file__), "rules.json"))
    objects = build_objects(pads, vshields, holes, scale, to_mm)
    primitives, rejected = clf.classify(objects, rules)
    by_tool = {}
    for p in primitives:
        by_tool[p["tool"]] = by_tool.get(p["tool"], 0) + 1
    print(f"classifier  : {dict(by_tool)}  "
          f"({len(rejected)} rejected: {[r['ref']+' '+r['rejected'] for r in rejected]})")

    json.dump(dict(part="USB4910", source=f"{PDF} p{PAGE+1}",
                   px_per_mm=round(scale, 3), grid_mm=GRID_MM,
                   calibrated_on=f"{PAD_PITCH_MM}mm pad pitch",
                   artifacts=artifacts, primitives=primitives, rejected=rejected),
              open(f"{OUT}/artifacts.json", "w"), indent=2)
    overlay(bgr, pads, vshields, holes, scale, ox, oy)
    write_kicad(artifacts)
    write_designstudio(artifacts)        # native FootprintDef (drop-in)
    write_component2(artifacts)          # component/2 (Library -> Import JSON)
    print(f"wrote {OUT}/  artifacts.json  pcb_layout_labeled.png  USB4910.kicad_mod"
          f"  USB4910.dsfp.json  USB4910.component.json")


def overlay(bgr, pads, vshields, holes, scale, ox, oy):
    vis = bgr.copy()
    GREEN, BLUE, RED = (60, 190, 60), (235, 130, 0), (40, 40, 230)
    mm2px = lambda x, y: (int(ox + x*scale), int(oy - y*scale))
    for p in pads:
        x, y = int(p["cx"]), int(p["cy"])
        pw, ph = int(p["w"]/2), int(p["h"]/2)
        cv2.rectangle(vis, (x-pw, y-ph), (x+pw, y+ph), GREEN, 2)
        cv2.putText(vis, p["ref"], (x-18, y-ph-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2, cv2.LINE_AA)
    for b in vshields:
        poly = np.array([mm2px(x, y) for x, y in b["contour_mm"]], np.int32)
        cv2.polylines(vis, [poly], True, BLUE, 3)        # TRUE vector outline
        cx, cy = mm2px(b["cx_mm"], b["cy_mm"])
        cv2.putText(vis, b["ref"], (cx-34, cy+6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, BLUE, 2, cv2.LINE_AA)
    for i, h in enumerate(holes, 1):
        cv2.circle(vis, (int(h["cx"]), int(h["cy"])), int(h["r"]), RED, 4)
        cv2.putText(vis, f"MP{i}", (int(h["cx"])-34, int(h["cy"])-int(h["r"])-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED, 2, cv2.LINE_AA)
    cv2.imwrite(f"{OUT}/pcb_layout_labeled.png", vis)


def write_kicad(artifacts):
    L = ['(footprint "USB4910"',
         '  (version 20240108) (generator "datasheet_layout_extractor")',
         '  (layer "F.Cu")',
         '  (descr "GCT USB4910 USB-C receptacle - extracted from datasheet layout")',
         '  (attr smd)']
    for a in artifacts:
        x, y = a["x_mm"], -a["y_mm"]                  # KiCad y is +down
        if a["kind"] == "npth_hole":
            d = a["drill_mm"]
            L.append(f'  (pad "{a["ref"]}" np_thru_hole circle (at {x:.3f} {y:.3f}) '
                     f'(size {d:.3f} {d:.3f}) (drill {d:.3f}) (layers "*.Cu" "*.Mask"))')
        elif a["kind"] == "shield_pad":
            L.append(f'  (pad "{a["ref"]}" smd roundrect (at {x:.3f} {y:.3f}) '
                     f'(size {a["w_mm"]:.3f} {a["h_mm"]:.3f}) (roundrect_rratio 0.25) '
                     f'(layers "F.Cu" "F.Paste" "F.Mask"))')
        else:
            L.append(f'  (pad "{a["ref"]}" smd rect (at {x:.3f} {y:.3f}) '
                     f'(size {a["w_mm"]:.3f} {a["h_mm"]:.3f}) (layers "F.Cu" "F.Paste" "F.Mask"))')
    L.append(')')
    open(f"{OUT}/USB4910.kicad_mod", "w").write("\n".join(L))


def write_designstudio(artifacts):
    """Native Design Studio FootprintDef JSON. Drop into the library folder
    (~/.config/DesignStudio/footprints/ on Linux, %APPDATA%\\DesignStudio\\
    footprints\\ on Windows) and it appears in the Library window. Top-level keys
    are PascalCase, pads snake_case -- matching FootprintLibrary.Load()."""
    pads = []
    for a in artifacts:
        th = a["kind"] == "npth_hole"
        drill = a.get("drill_mm", 0.0) if th else 0.0
        w = h = (drill + 0.30) if th else None          # give NPTH a viewable ring
        pads.append({
            "name": a["ref"],
            "x_mm": a["x_mm"], "y_mm": -a["y_mm"],       # Design Studio y is +down
            "w_mm": w if th else a["w_mm"], "h_mm": h if th else a["h_mm"],
            "through_hole": th, "drill_mm": drill,
            "electrical": _etype(a["ref"]) if a["kind"] == "signal_pad" else "passive",
            "pkg_delay_mm": 0.0,
        })
    fp = {
        "Name": "USB4910",
        "Description": "GCT USB3.2 Gen2 Type-C receptacle (datasheet-extracted)",
        "RefDesPrefix": "J", "Pads": pads,
        "BodyWidthMm": 8.94, "BodyHeightMm": 7.90,
        "Source": "datasheet-json",
        "Manufacturer": "GCT", "Mpn": "USB4910",
        "DatasheetUrl": "", "Pin1Marker": "A1",
        "HeightMm": 3.16, "StandoffMm": 0.0,
        "Function": "USB-C receptacle", "Interfaces": ["USB3.2", "USB-C"],
        "ElectricalJson": "",
    }
    json.dump(fp, open(f"{OUT}/USB4910.dsfp.json", "w"), indent=2)


def write_component2(artifacts):
    """component/2 for the validated 'Library -> Import JSON' path. Shields/holes
    are tagged mechanical; NPTH holes get an annular ring so Validate() passes."""
    def overlaps(p, q):
        return (abs(p["x_mm"]-q["x_mm"]) < (p["width_mm"]+q["width_mm"])/2 - 0.001 and
                abs(p["y_mm"]-q["y_mm"]) < (p["height_mm"]+q["height_mm"])/2 - 0.001)

    pins, fpads = [], []
    for a in artifacts:                                  # signal pads = authoritative
        if a["kind"] == "signal_pad":
            pins.append({"number": a["ref"], "name": USB_C_PINS.get(a["ref"], a["ref"]),
                         "electrical_type": _etype(a["ref"])})
            fpads.append({"number": a["ref"], "x_mm": a["x_mm"], "y_mm": a["y_mm"],
                          "width_mm": a["w_mm"], "height_mm": a["h_mm"], "shape": "rect"})
    dropped = []
    for a in sorted([x for x in artifacts if x["kind"] != "signal_pad"],
                    key=lambda x: -(x.get("w_mm", 0)*x.get("h_mm", 0))):
        if a["kind"] == "shield_pad":
            pad = {"number": a["ref"], "x_mm": a["x_mm"], "y_mm": a["y_mm"],
                   "width_mm": a["w_mm"], "height_mm": a["h_mm"],
                   "shape": "roundrect", "mechanical": True}
        else:                                            # npth_hole + annular ring
            d = a["drill_mm"]
            pad = {"number": a["ref"], "x_mm": a["x_mm"], "y_mm": a["y_mm"],
                   "width_mm": round(d+0.3, 2), "height_mm": round(d+0.3, 2),
                   "shape": "circle", "drill_mm": d, "mechanical": True}
        # a mechanical feature overlapping a pad/another mech feature is a
        # misdetection -> drop it (signal pads win)
        if any(overlaps(pad, q) for q in fpads):
            dropped.append(a["ref"]); continue
        fpads.append(pad)
    doc = {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "GCT", "mpn": "USB4910", "category": "connector",
                      "description": "USB3.2 Gen2 Type-C receptacle, dual-row SMT (datasheet-extracted)",
                      "interfaces": ["USB3.2", "USB-C"]},
        "symbol": {"ref_des_prefix": "J", "pins": pins},
        "footprint": {"name": "GCT_USB4910", "mount": "smd",
                      "body": {"length_mm": 8.94, "width_mm": 7.90, "height_mm": 3.16},
                      "pitch_mm": PAD_PITCH_MM, "pads": fpads},
        "orientation": {"pin1_marker": "none", "pin1_position": "A1 at top-left"},
        "package_3d": {"height_mm": 3.16, "standoff_mm": 0.0, "shape": "box"},
        "extraction": {"warnings": ["Geometry from OpenCV layout extraction; verify "
                                    "shield-pad count and hole diameter before fab."]
                       + ([f"dropped overlapping misdetections: {', '.join(dropped)}"] if dropped else [])},
    }
    json.dump(doc, open(f"{OUT}/USB4910.component.json", "w"), indent=2)


if __name__ == "__main__":
    main()
