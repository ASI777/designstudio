#!/usr/bin/env python3
"""Generic copper-feature detection -- the piece that removes the USB4910-specific
hard-coding (PADS_PER_ROW=12, two rows, fixed mount boxes, fixed hole ROI).

Given the located figure raster, detect features by SHAPE, inferring structure
from the data:
  * pad grid   : blobs of a consensus size, clustered into lines -> rows x cols,
                 pitch measured (no count assumed; orientation-agnostic).
  * rings      : blobs whose filled contour has an inner hole (hollow mounts).
  * holes      : small round solid blobs (NPTH drills).

Everything is measured in PIXELS here; calibration (px->mm) is applied by the
caller, so this module is independent of the not-to-scale / multi-scale problem.
"""
import numpy as np
import cv2
import fitz


def render_clip(page, clip, dpi=600):
    """Render just the located figure, rotation removed so the raster is in the
    same (unrotated) frame as locate's clip. Returns (gray, dpi)."""
    rot = page.rotation
    try:
        page.set_rotation(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), clip=clip)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        gray = a[:, :, 0] if pix.n == 1 else cv2.cvtColor(
            np.ascontiguousarray(a[:, :, :3]), cv2.COLOR_RGB2GRAY)
    finally:
        page.set_rotation(rot)
    return gray, dpi


def _ink(gray, strip_frac=0.33):
    """Binary ink with long rules (frame, centre/dimension lines) removed, then
    hatch/outline solidified into filled blobs."""
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    H, W = ink.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, int(strip_frac*W)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(1, int(strip_frac*H))))
    longs = cv2.bitwise_or(cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk),
                           cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk))
    return cv2.subtract(ink, longs)


def _cluster_1d(vals, tol):
    """Group sorted scalars whose gaps are <= tol; return (centers, sizes)."""
    if len(vals) == 0:
        return [], []
    vals = sorted(vals)
    centers, sizes, grp = [], [], [vals[0]]
    for v in vals[1:]:
        if v - grp[-1] <= tol:
            grp.append(v)
        else:
            centers.append(float(np.mean(grp))); sizes.append(len(grp)); grp = [v]
    centers.append(float(np.mean(grp))); sizes.append(len(grp))
    return centers, sizes


def _blobs(ink, fill_frac=0.10):
    """Connected components after a small solidifying close; return blob dicts."""
    H, W = ink.shape
    k = max(1, int(fill_frac * min(H, W) / 20)) | 1     # tiny close to seal hatch
    solid = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(solid)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        out.append(dict(cx=float(cent[i][0]), cy=float(cent[i][1]),
                        w=int(w), h=int(h), area=int(area),
                        fill=area/float(w*h) if w*h else 0.0, label=i))
    return out, lab


def detect_pad_grid(gray):
    """Detect a pad grid of ANY dimensions. Returns dict(rows, cols, pitch_px,
    axis, pads=[{cx,cy,w,h,row,col,ref}]). Orientation-agnostic: the axis with
    FEWER clusters is taken as the row-separation axis."""
    ink = _ink(gray)
    blobs, _ = _blobs(ink)
    if len(blobs) < 4:
        return dict(rows=0, cols=0, pitch_px=0.0, pads=[], note="too few blobs")

    # consensus pad size: pads are the dominant repeated rectangle. Use median area
    # of the middle band of blobs (drops tiny text and huge outlines).
    areas = np.array([b["area"] for b in blobs])
    med = float(np.median(areas))
    cand = [b for b in blobs if 0.45*med < b["area"] < 2.2*med
            and 0.25 < (min(b["w"], b["h"])/max(b["w"], b["h"])) ]   # not a sliver
    if len(cand) < 4:
        cand = blobs
    mw = float(np.median([b["w"] for b in cand]))
    mh = float(np.median([b["h"] for b in cand]))

    # cluster centroids on each axis; tol = ~0.6 of the pad's cross-size on that axis
    cxs = [b["cx"] for b in cand]; cys = [b["cy"] for b in cand]
    xcols, xsz = _cluster_1d(cxs, 0.6*mw)
    yrows, ysz = _cluster_1d(cys, 0.6*mh)
    # the row-separation axis is the one with fewer distinct lines
    if len(yrows) <= len(xcols):
        rows_c, cols_c, row_key, col_key, axis = yrows, xcols, "cy", "cx", "y"
    else:
        rows_c, cols_c, row_key, col_key, axis = xcols, yrows, "cx", "cy", "x"

    def nearest(centers, v):
        return int(np.argmin([abs(v-c) for c in centers]))

    pads = []
    for b in cand:
        r = nearest(rows_c, b[row_key]); c = nearest(cols_c, b[col_key])
        pads.append(dict(cx=b["cx"], cy=b["cy"], w=b["w"], h=b["h"], row=r, col=c))
    # pitch = median neighbour gap along the within-row (col) axis
    col_centers = sorted(cols_c)
    pitch = float(np.median(np.diff(col_centers))) if len(col_centers) > 1 else 0.0
    # de-dup to one pad per (row,col), assign refs row-letter + col-number
    grid = {}
    for p in pads:
        grid.setdefault((p["row"], p["col"]), p)
    pads = []
    for (r, c), p in sorted(grid.items()):
        p = dict(p); p["ref"] = f"{chr(ord('A')+r)}{c+1}"
        pads.append(p)
    return dict(rows=len(rows_c), cols=len(cols_c), pitch_px=round(pitch, 1),
                axis=axis, pad_w_px=round(mw, 1), pad_h_px=round(mh, 1), pads=pads)


if __name__ == "__main__":
    import sys, locate
    from runtime_paths import sample_datasheet
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    fig = locate.locate_layout(pdf)
    print(fig)
    if not fig:
        sys.exit("no figure")
    page = fitz.open(pdf)[fig.page]
    gray, dpi = render_clip(page, fig.clip)
    g = detect_pad_grid(gray)
    print(f"  grid: {g['rows']} x {g['cols']}  ({len(g['pads'])} pads)  "
          f"pitch={g['pitch_px']}px  pad~{g['pad_w_px']}x{g['pad_h_px']}px  axis={g.get('axis')}")
    refs = [p["ref"] for p in g["pads"]]
    print("  refs:", " ".join(refs[:30]) + (" ..." if len(refs) > 30 else ""))
