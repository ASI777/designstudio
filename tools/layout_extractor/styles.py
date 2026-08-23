#!/usr/bin/env python3
"""OpenCV half of the hybrid legend system: detect the STYLE of every region in
the land-pattern figure from the PDF's own vector data, WITHOUT deciding what the
style means. A vision-LLM (see docs/datasheet-extractor/LEGEND_PROMPT.md) reads the
legend/notes and supplies the per-datasheet style->meaning map; `legend.py` then
joins the two.

Three style cues are machine-readable straight from `page.get_drawings()`:
  - FILL   : a path with a non-null `fill` colour      -> a solid-filled area.
  - DASH   : a stroked path with a non-empty `dashes`   -> a dashed outline.
  - HATCH  : a spatial cluster of SHORT segments at a non-axis angle (the diagonal
             shading) -- detected geometrically, since hatch is drawn as many tiny
             parallel strokes, not as a fill.
"""
import numpy as np
import cv2
import fitz


def _pts_of(item):
    """All fitz.Points referenced by a drawing item ('l','re','c','qu')."""
    out = []
    for p in item[1:]:
        if isinstance(p, fitz.Point):
            out.append((p.x, p.y))
        elif isinstance(p, fitz.Rect):
            out += [(p.x0, p.y0), (p.x1, p.y1)]
    return out


def _bbox(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def _in(clip, r, margin=4):
    return (r.x0 >= clip.x0 - margin and r.y0 >= clip.y0 - margin and
            r.x1 <= clip.x1 + margin and r.y1 <= clip.y1 + margin)


def _cluster(points, pr, cell=2.0, dilate_pt=4.0, min_n=6):
    """Cluster 2D points (pt) into blobs; return list of (bbox_rect, count)."""
    if not points:
        return []
    W = max(1, int(pr.width / cell)); H = max(1, int(pr.height / cell))
    occ = np.zeros((H, W), np.uint8)
    gx = lambda x: int(np.clip((x - pr.x0) / cell, 0, W - 1))
    gy = lambda y: int(np.clip((y - pr.y0) / cell, 0, H - 1))
    for x, y in points:
        occ[gy(y), gx(x)] = 255
    k = max(1, int(dilate_pt / cell)) | 1
    occ = cv2.dilate(occ, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(occ)
    pa = np.array(points)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        bb = fitz.Rect(pr.x0 + x * cell, pr.y0 + y * cell,
                       pr.x0 + (x + w) * cell, pr.y0 + (y + h) * cell)
        cnt = int(sum(1 for px, py in pa if bb.x0 <= px <= bb.x1 and bb.y0 <= py <= bb.y1))
        if cnt >= min_n:
            out.append((bb, cnt))
    return out


def detect_styles(page, clip, axis_tol=12.0, hatch_len=(1.0, 8.0)):
    """Return {'fill':[...], 'dash':[...], 'hatch':[...]}, each item a dict with
    'bbox' (fitz.Rect, pt), 'centroid' (x,y pt), and style-specific extras."""
    filled, dashed, hatch_mids, hatch_ang = [], [], [], []
    for d in page.get_drawings():
        items = d["items"]
        pts = [p for it in items for p in _pts_of(it)]
        if not pts:
            continue
        bb = _bbox(pts)
        if not _in(clip, bb):
            continue
        if d.get("fill") is not None:
            filled.append(dict(bbox=bb, centroid=((bb.x0+bb.x1)/2, (bb.y0+bb.y1)/2),
                               color=d.get("fill")))
        if d.get("dashes") not in (None, "", "[] 0"):
            dashed.append(dict(bbox=bb, centroid=((bb.x0+bb.x1)/2, (bb.y0+bb.y1)/2),
                               dashes=d.get("dashes")))
        for it in items:
            if it[0] == "l":
                dx, dy = it[2].x - it[1].x, it[2].y - it[1].y
                L = (dx*dx + dy*dy) ** 0.5
                if hatch_len[0] < L < hatch_len[1]:
                    a = np.degrees(np.arctan2(dy, dx)) % 180.0
                    if min(a, abs(a-90), abs(a-180)) > axis_tol:   # non-axis = hatch
                        hatch_mids.append(((it[1].x+it[2].x)/2, (it[1].y+it[2].y)/2))
                        hatch_ang.append(a)
    hatched = []
    for bb, cnt in _cluster(hatch_mids, page.rect):
        angs = [a for (px, py), a in zip(hatch_mids, hatch_ang)
                if bb.x0 <= px <= bb.x1 and bb.y0 <= py <= bb.y1]
        hatched.append(dict(bbox=bb, centroid=((bb.x0+bb.x1)/2, (bb.y0+bb.y1)/2),
                            angle_deg=round(float(np.median(angs)), 1), n_strokes=cnt))
    return dict(fill=filled, dash=dashed, hatch=hatched)


def render_figure(page, clip, dpi=600, path=None):
    """Render just the located figure (for attaching to the vision-LLM)."""
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), clip=clip)
    if path:
        pix.save(path)
    return path


if __name__ == "__main__":
    import sys, locate
    from runtime_paths import sample_datasheet
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    fig = locate.locate_layout(pdf)
    print(fig)
    page = fitz.open(pdf)[fig.page]
    st = detect_styles(page, fig.clip)
    for k in ("fill", "dash", "hatch"):
        print(f"\n{k.upper()}: {len(st[k])} region(s)")
        for r in st[k][:8]:
            c = r["centroid"]; extra = ""
            if k == "hatch":
                extra = f" angle={r['angle_deg']}deg n={r['n_strokes']}"
            print(f"   centroid=({c[0]:.0f},{c[1]:.0f}) "
                  f"bbox=({r['bbox'].x0:.0f},{r['bbox'].y0:.0f},{r['bbox'].x1:.0f},{r['bbox'].y1:.0f}){extra}")
