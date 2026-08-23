#!/usr/bin/env python3
"""
downsize_component.py — take the datasheet recommended-layout SVG (drawn at an
arbitrary illustration zoom), read its two-arrow dimension + value, and SCALE the
component geometry back down to its true physical size in millimetres, dropped on
a 1 mm grid so it lands at real scale in Design Studio's grid layout.

Technique:
  1. flatten every SVG <path> through its transform matrix -> page-mm.
  2. recover the real scale (mm-real per page-mm) from the dimension network
     (two arrowheads spanning a known value), via pad_dimensioner.
  3. transform the component view -> real mm, datum-centred on the connector,
     Y up; clip to the body so the dimension/witness lines fall away.
  4. emit a real-size SVG (1 user unit = 1 mm) with a 1 mm grid + overall
     dimension callouts, and report the measured extent.
"""
import re
import sys
from pathlib import Path
import pad_dimensioner as pd

# CLI:  downsize_component.py <svg> [--crop x0 y0 x1 y1]
# The crop box is given in the SVG's own user units (viewBox coordinates) — this
# is what the Footprint Lib tab passes from a rubber-band selection, so only the
# reference dimension + component under the drag are downsized.
_args = sys.argv[1:]
CROP_UU = None
if "--crop" in _args:
    i = _args.index("--crop")
    CROP_UU = tuple(float(v) for v in _args[i + 1:i + 5])
    del _args[i:i + 5]
# --print-scale: just solve the dimension network (optionally cropped) and print
# `scale <mm-real per page-mm>`, no SVG. The app uses this to scale the SELECTED
# native canvas items directly (full fidelity, all elements), instead of
# round-tripping through a regenerated SVG.
PRINT_SCALE = "--print-scale" in _args
if PRINT_SCALE:
    _args.remove("--print-scale")
SVG = _args[0] if _args else "usb4910_page2.svg"
OUT = str(Path(SVG).with_suffix(".component_realsize.svg"))


def subpaths_mm(d, mat):
    """Flatten an SVG path 'd' into a list of polylines in page-mm, one per
    subpath (split at M/m so disjoint copper strokes never connect)."""
    out, poly, cur, cmd = [], [], [0.0, 0.0], None
    toks = re.findall(r'[MLHVZmlhvz]|-?\d*\.?\d+(?:e-?\d+)?', d)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in 'MLHVZmlhvz':
            cmd = t
            i += 1
            if cmd in 'Zz' and poly:
                poly.append(poly[0])
            if cmd in 'Mm' and poly:
                out.append(poly)
                poly = []
            continue
        if cmd in ('M', 'L'):
            cur = [float(toks[i]), float(toks[i + 1])]; i += 2
        elif cmd in ('m', 'l'):
            cur = [cur[0] + float(toks[i]), cur[1] + float(toks[i + 1])]; i += 2
        elif cmd == 'H':
            cur[0] = float(toks[i]); i += 1
        elif cmd == 'h':
            cur[0] += float(toks[i]); i += 1
        elif cmd == 'V':
            cur[1] = float(toks[i]); i += 1
        elif cmd == 'v':
            cur[1] += float(toks[i]); i += 1
        else:
            i += 1; continue
        poly.append(tuple(cur))
    if poly:
        out.append(poly)
    return [[pd.to_mm(*pd.apply_mat(mat, *p)) for p in pl] for pl in out]


def main():
    # ── 1+2. real scale + datum from the dimension network ───────────────────
    # crop (user units) → page-mm, the space pad_dimensioner works in.
    crop_mm = tuple(v * pd.PT2MM for v in CROP_UU) if CROP_UU else None
    res = pd.dimension_pads(SVG, crop=crop_mm)
    if crop_mm is not None:
        x0, y0, x1, y1 = crop_mm
        crop_mm = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if not res['views']:
        raise ValueError("no dimensioned views found in " + SVG)
    scale = res['scale']                       # mm-real per page-mm
    if PRINT_SCALE:                            # scale-only mode for the app
        print(f"scale {scale:.8f}")
        return
    # datum: prefer the land-pattern view; fall back to whichever view exists
    view = res['views'].get('left') or next(iter(res['views'].values()))
    ax, ay = view['anchor_svg']                # connector/part centre, page-mm
    # overall envelope = the largest dimension value on each axis (DERIVED from
    # the extraction, not hardcoded). This is the body we downsize to and clip
    # against, so it adapts to whatever part the SVG describes.
    alldims = [d for v in res['views'].values() for d in v['dims']]
    hvals = [d['val'] for d in alldims if d['axis'] == 'H']
    vvals = [d['val'] for d in alldims if d['axis'] == 'V']
    if not hvals or not vvals:
        raise ValueError("need both H and V dimensions to size the component")
    W, H = max(hvals), max(vvals)
    HW, HH = W / 2, H / 2

    svg = Path(SVG).read_text(encoding='utf-8', errors='replace')

    # ── 3. transform the left (component) view to real mm, clip to body ──────
    kept = []
    bx0 = by0 = 1e9; bx1 = by1 = -1e9
    for pm in re.finditer(r'<path([^/]*/?>)', svg, re.S):
        p = pm.group(1)
        if pd._solid_fill(p):                          # arrowheads -> drop
            continue
        tr = re.search(r'transform="([^"]*)"', p)
        dm = re.search(r'\bd="([^"]*)"', p)
        if not dm:
            continue
        mat = pd.parse_matrix(tr.group(1) if tr else '')
        for pl in subpaths_mm(dm.group(1), mat):
            # spatial scope: ignore strokes outside the selected crop so other
            # components / views on the same sheet never leak in.
            if crop_mm is not None and not any(
                    pd._in_crop(pt, crop_mm, margin=0.0) for pt in pl):
                continue
            real = [((x - ax) * scale, -(y - ay) * scale) for x, y in pl]
            # keep only strokes that stay inside the body (drops the witness/
            # extension/dimension lines that fan outward to the arrows + the
            # right-hand solder-mask view).
            if not real or any(abs(rx) > HW + 0.15 or abs(ry) > HH + 0.15
                               for rx, ry in real):
                continue
            kept.append(real)
            for rx, ry in real:
                bx0, by0 = min(bx0, rx), min(by0, ry)
                bx1, by1 = max(bx1, rx), max(by1, ry)

    # ── 4. emit real-size SVG (1 unit = 1 mm) on a 1 mm grid ─────────────────
    M = 2.0                                          # mm margin
    vx, vy = -HW - M, -HH - M
    vw, vh = 2 * HW + 2 * M, 2 * HH + 2 * M
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}mm" height="{vh}mm" '
         f'viewBox="{vx:.3f} {vy:.3f} {vw:.3f} {vh:.3f}">',
         f'  <!-- component scaled to original size: {W}x{H} mm body, '
         f'scale={scale:.5f} mm/page-mm -->']
    # 1 mm grid (Y is up in our model; SVG Y is down so we just draw lines)
    L.append('  <g stroke="#d8e6ff" stroke-width="0.03" fill="none">')
    gx = int(vx) - 1
    while gx <= vx + vw:
        L.append(f'    <line x1="{gx}" y1="{vy:.2f}" x2="{gx}" y2="{vy+vh:.2f}"/>')
        gx += 1
    gy = int(vy) - 1
    while gy <= vy + vh:
        L.append(f'    <line x1="{vx:.2f}" y1="{gy}" x2="{vx+vw:.2f}" y2="{gy}"/>')
        gy += 1
    L.append('  </g>')
    # the component, at true mm (flip Y to SVG down)
    L.append('  <g stroke="#000000" stroke-width="0.04" fill="none" '
             'stroke-linecap="round" stroke-linejoin="round">')
    for pl in kept:
        d = "M " + " L ".join(f"{x:.4f},{-y:.4f}" for x, y in pl)
        L.append(f'    <path d="{d}"/>')
    L.append('  </g>')
    # overall dimension callouts proving real size — LINES ONLY, with tiny end
    # ticks. We deliberately emit NO <text>: a sub-pixel font-size in this small
    # (mm) viewBox makes Qt's SVG font/glyph path crash, and the value is already
    # the grid + line extent. The W×H value lives in the SVG comment + filename.
    yb = HH + 1.0
    xb = HW + 1.0
    L.append('  <g stroke="#cc0000" stroke-width="0.04" fill="none">')
    L.append(f'    <line x1="{-HW}" y1="{-yb}" x2="{HW}" y2="{-yb}"/>')        # width bar
    L.append(f'    <line x1="{-HW}" y1="{-yb-0.3}" x2="{-HW}" y2="{-yb+0.3}"/>')
    L.append(f'    <line x1="{HW}"  y1="{-yb-0.3}" x2="{HW}"  y2="{-yb+0.3}"/>')
    L.append(f'    <line x1="{xb}" y1="{-HH}" x2="{xb}" y2="{HH}"/>')          # height bar
    L.append(f'    <line x1="{xb-0.3}" y1="{-HH}" x2="{xb+0.3}" y2="{-HH}"/>')
    L.append(f'    <line x1="{xb-0.3}" y1="{HH}"  x2="{xb+0.3}" y2="{HH}"/>')
    L.append('  </g>')
    L.append('</svg>')
    Path(OUT).write_text("\n".join(L))

    print(f"source view drawn-zoom scale : {scale:.5f} mm-real per page-mm")
    print(f"derived envelope (largest dim) : {W:.2f} x {H:.2f} mm")
    print(f"kept component strokes         : {len(kept)}")
    print(f"measured real-size extent      : "
          f"{bx1-bx0:.3f} x {by1-by0:.3f} mm  "
          f"(x[{bx0:.2f},{bx1:.2f}] y[{by0:.2f},{by1:.2f}])")
    print(f"wrote {OUT}  ({vw:.1f} x {vh:.1f} mm, 1 unit = 1 mm)")


if __name__ == "__main__":
    main()
