#!/usr/bin/env python3
"""LLM<->CV bridge: VERIFY and REFINE an LLM-discovered footprint against the
actual drawing geometry.

The vision-LLM (repo EXTRACTOR_PROMPT -> component/2) discovers structure -- how
many pads, where, what pitch -- on any part, including scans. That output can be
wrong (hallucinated pad, mis-read pitch). This module GROUNDS it: at each
LLM-given pad anchor it checks the drawing actually has copper there and measures
the real pad box, so CV never has to DISCOVER the grid blind (the thing that
doesn't generalize). It only refines/verifies at known anchors -- which is robust.

Frame-agnostic: caller supplies a `gray` raster of the figure plus the calibration
(scale px/mm, origin ox,oy in px, y_up=True if part-y increases upward).
"""
import numpy as np
import cv2


def _ink(gray):
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]


def verify_and_refine(gray, pads, scale, ox=None, oy=None, y_up=True, search_mm=0.35,
                      pitch_mm=None, row_axis=None, to_px_fn=None, swap_wh=False):
    """pads: [{number/ref, x_mm, y_mm, width_mm/w_mm, height_mm/h_mm}] (LLM output).
    Pass either (ox,oy[,y_up]) for a simple scale/translate map, or `to_px_fn`
    (e.g. register.register()['to_px']) for a full scale+rotation+flip transform.
    Returns (report_list, summary): per-pad anchor, copper-present?, CV box (mm),
    deltas vs the LLM box."""
    ink = _ink(gray)
    H, W = ink.shape

    def _default(xmm, ymm):
        px = ox + xmm * scale
        py = (oy - ymm * scale) if y_up else (oy + ymm * scale)
        return int(round(px)), int(round(py))
    to_px = to_px_fn or _default

    def field(p, *names):
        for k in names:
            if k in p:
                return p[k]
        return None

    margin = 0.2                                  # mm window margin around the LLM box
    report = []
    for p in pads:
        ref = field(p, "number", "ref", "name")
        xmm, ymm = field(p, "x_mm"), field(p, "y_mm")
        lw, lh = field(p, "width_mm", "w_mm") or 0.0, field(p, "height_mm", "h_mm") or 0.0
        px, py = to_px(xmm, ymm)
        present, cw_mm, ch_mm, dcx, dcy = False, None, None, None, None
        if 0 <= px < W and 0 <= py < H:
            # window sized to the LLM pad (+margin) so the measurement is bounded to
            # ONE pad, not the whole hatch-merged row. Under a 90/270 registration the
            # part's w/h map to image h/w -> use image-axis dims (swap_wh).
            ilw, ilh = (lh, lw) if swap_wh else (lw, lh)
            hw = int(round((ilw/2 + margin) * scale)); hh = int(round((ilh/2 + margin) * scale))
            hw = max(hw, int(0.25*scale)); hh = max(hh, int(0.25*scale))
            # cap the within-row half-window below the half-pitch so a measurement
            # can't bleed into the neighbouring pad (densely packed rows).
            if pitch_mm and row_axis in ("x", "y"):
                cap = int(round(0.45 * pitch_mm * scale))
                if row_axis == "x":
                    hw = min(hw, cap)
                else:
                    hh = min(hh, cap)
            x0, x1 = max(0, px-hw), min(W, px+hw+1)
            y0, y1 = max(0, py-hh), min(H, py+hh+1)
            sub = ink[y0:y1, x0:x1].copy()
            # strip the long centre-line that bridges pads, in BOTH axes, within the window
            if sub.shape[1] > 3:
                hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(sub.shape[1]*0.8)), 1))
                sub = cv2.subtract(sub, cv2.morphologyEx(sub, cv2.MORPH_OPEN, hk))
            if sub.shape[0] > 3:
                vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, int(sub.shape[0]*0.8))))
                sub = cv2.subtract(sub, cv2.morphologyEx(sub, cv2.MORPH_OPEN, vk))
            ys, xs = np.where(sub > 0)
            present = len(xs) > 0
            if present:
                cw_mm = round((xs.max()-xs.min()+1) / scale, 3)
                ch_mm = round((ys.max()-ys.min()+1) / scale, 3)
                dcx = round((xs.mean()+x0 - px) / scale, 3)
                dcy = round((ys.mean()+y0 - py) / scale, 3)
        # report CV dims in PART axes (un-swap the image-axis measurement)
        cvw, cvh = (ch_mm, cw_mm) if swap_wh else (cw_mm, ch_mm)
        report.append(dict(
            ref=ref, anchor_px=(px, py), present=present,
            llm_w=lw, llm_h=lh, cv_w=cvw, cv_h=cvh,
            dw=(round(cvw-lw, 3) if (cvw and lw) else None),
            dh=(round(cvh-lh, 3) if (cvh and lh) else None),
            center_off_mm=(dcx, dcy)))

    npads = len(report)
    present = sum(1 for r in report if r["present"])
    dws = [abs(r["dw"]) for r in report if r["dw"] is not None]
    dhs = [abs(r["dh"]) for r in report if r["dh"] is not None]
    summary = dict(
        n=npads, copper_present=present,
        presence_rate=round(present/npads, 3) if npads else 0.0,
        missing=[r["ref"] for r in report if not r["present"]],
        size_mae_w_mm=round(float(np.mean(dws)), 3) if dws else None,
        size_mae_h_mm=round(float(np.mean(dhs)), 3) if dhs else None)
    return report, summary


if __name__ == "__main__":
    import sys, os, json
    sys.path.insert(0, os.path.dirname(__file__))
    import extract_layout as E, cv2 as _cv2, numpy as _np
    # reuse the proven USB4910 render + calibration as the demo frame
    bgr = E.render(); gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
    pads_det, pitch = E.detect_signal_pads(gray)
    scale = pitch / E.PAD_PITCH_MM
    ox = float(_np.mean([p["cx"] for p in pads_det]))
    oy = float(_np.mean([p["cy"] for p in pads_det]))
    comp = json.load(open(os.path.join(E.OUT, "USB4910.component.json")))
    llm_pads = comp["footprint"]["pads"]                 # stand-in for LLM discovery
    rep, summ = verify_and_refine(gray, llm_pads, scale, ox, oy,
                                  pitch_mm=E.PAD_PITCH_MM, row_axis="x")
    print(f"calibration: scale={scale:.1f}px/mm  origin=({ox:.0f},{oy:.0f})")
    print(f"summary: {summ}")
    for r in rep[:6]:
        print(f"  {str(r['ref']):4} present={r['present']!s:5} "
              f"llm={r['llm_w']}x{r['llm_h']} cv={r['cv_w']}x{r['cv_h']} "
              f"d=({r['dw']},{r['dh']}) off={r['center_off_mm']}")
