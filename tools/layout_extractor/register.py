#!/usr/bin/env python3
"""mm->px REGISTRATION: align an LLM-discovered footprint (pads in part-mm) to the
located figure raster (pixels), solving scale + orientation + translation so
`verify.py` runs on ANY part -- not just USB4910's hard-coded calibration.

We do NOT assume the orientation (datasheets render at 0/90/180/270, sometimes
mirrored, and the raster frame may differ). Instead we SEARCH the discrete
similarity group and pick the transform that lands the most LLM pads on real
copper -- presence is the robust objective (the same signal verify trusts), so a
noisy blob detector is never needed.
"""
import numpy as np
import cv2

# dihedral-4 rotations of a centred point (covers 0/90/180/270; combined with a
# y-flip this is all 8 axis-aligned orientations incl. mirrored datasheets).
_ROT = (lambda x, y: (x, y), lambda x, y: (-y, x),
        lambda x, y: (-x, -y), lambda x, y: (y, -x))


def _ink(gray):
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]


def _field(p, *names):
    for k in names:
        if k in p:
            return p[k]
    return None


def register(gray, pads, scale_steps=15, off_steps=5, off_mm=0.6):
    """Return a Transform dict with `to_px(xmm,ymm)`, the recovered scale/rotation/
    flip/origin, and `present_rate`. Maximises pad-on-copper presence over
    (rotation x flip x scale x small translation)."""
    ink = _ink(gray)
    H, W = ink.shape
    ys, xs = np.where(ink > 0)
    if len(xs) == 0:
        return None
    oximg, oyimg = float(xs.mean()), float(ys.mean())          # copper centroid (px)

    P = [(float(_field(p, "x_mm")), float(_field(p, "y_mm"))) for p in pads]
    cmx = float(np.mean([p[0] for p in P])); cmy = float(np.mean([p[1] for p in P]))
    Pc = [(x-cmx, y-cmy) for x, y in P]                        # centre the constellation

    # scale init from overall extent ratio (px per mm), then search around it
    llm_ext = max(1e-3, max(max(x for x, _ in Pc)-min(x for x, _ in Pc),
                            max(y for _, y in Pc)-min(y for _, y in Pc)))
    cv_ext = max(xs.max()-xs.min(), ys.max()-ys.min())
    s0 = cv_ext / llm_ext
    scales = s0 * np.linspace(0.55, 1.7, scale_steps)
    offs = np.linspace(-off_mm, off_mm, off_steps)

    # integral image of copper for O(1) window-presence tests
    integ = cv2.integral((ink > 0).astype(np.uint8))
    def hit(px, py, r):
        x0, y0 = max(0, px-r), max(0, py-r)
        x1, y1 = min(W, px+r+1), min(H, py+r+1)
        if x1 <= x0 or y1 <= y0:
            return False
        s = integ[y1, x1] - integ[y0, x1] - integ[y1, x0] + integ[y0, x0]
        return s > 0

    best = None
    for k in range(4):
        rot = _ROT[k]
        for flip in (1, -1):
            tp = [rot(x, flip*y) for x, y in Pc]
            for s in scales:
                r = max(2, int(0.10 * s))
                for dx in offs:
                    for dy in offs:
                        ox = oximg + dx*s; oy = oyimg + dy*s
                        present = sum(hit(int(ox + x*s), int(oy + y*s), r) for x, y in tp)
                        if best is None or present > best[0]:
                            best = (present, k, flip, s, ox, oy)
    present, k, flip, s, ox, oy = best
    rot = _ROT[k]
    def to_px(xmm, ymm):
        x, y = rot(xmm-cmx, flip*(ymm-cmy))
        return int(round(ox + x*s)), int(round(oy + y*s))
    return dict(to_px=to_px, scale=round(s, 2), rot_k=k, flip=flip,
                origin_px=(round(ox, 1), round(oy, 1)),
                present=present, n=len(P), present_rate=round(present/len(P), 3))


if __name__ == "__main__":
    import sys, os, json, fitz
    sys.path.insert(0, os.path.dirname(__file__))
    import locate, detect
    from runtime_paths import layout_output_dir, sample_datasheet
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    comp_path = sys.argv[2] if len(sys.argv) > 2 else \
        str(layout_output_dir() / "USB4910.component.json")
    fig = locate.locate_layout(pdf)
    print(fig)
    page = fitz.open(pdf)[fig.page]
    gray, dpi = detect.render_clip(page, fig.clip)
    pads = json.load(open(comp_path))["footprint"]["pads"]
    reg = register(gray, pads)
    print(f"  registration: scale={reg['scale']}px/mm rot={reg['rot_k']*90}deg "
          f"flip={reg['flip']} origin={reg['origin_px']}  "
          f"present={reg['present']}/{reg['n']} ({reg['present_rate']})")
