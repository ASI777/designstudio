#!/usr/bin/env python3
"""
build_footprint.py — turn the extracted USB4910 land-pattern parameters into a
correct, manufacturable footprint:  copper pads + shield/mount tabs + component
outline.  Emits a KiCad .kicad_mod (interchange format) + a preview render + JSON.

Parameters are taken from pad_dimensioner's extraction of usb4910_page2.svg
(pitch, pad sizes, row spans, tab sizes) with the corner tabs resolved to their
dominant size and the contact array regenerated cleanly to 2×12.
"""
import json, math
from pathlib import Path

# ── Extracted/resolved land-pattern configuration (Connector / USB-C) ─────────
# datum = connector centre (the 8.64 width midpoint), Y up, millimetres.
PITCH        = 0.50
N_PER_ROW    = 12
ROW_TOP_Y    = 3.73 ; ROW_TOP_H = 1.10      # 12x1.10
ROW_BOT_Y    = 2.38 ; ROW_BOT_H = 0.85      # 24x0.85 (the shorter row)
PAD_W        = 0.30                          # contact width
PAD_RRATIO   = 0.20

# shield / mount tabs: left side; mirrored to +x. (x, y, w, h)
TABS = [
    (-4.32,  3.10, 1.00, 1.60),   # upper corner shield tab
    (-4.32, -2.27, 1.00, 2.30),   # lower corner mount tab
]
TAB_RRATIO   = 0.30                          # R0.40/R0.45 corners

OUTLINE_W    = 8.64                          # component outline (body)
OUTLINE_H    = 6.20
COURTYARD_M  = 0.25                          # margin beyond copper

NAME = "USBC_USB4910_RA_SMT"


def contacts():
    pads = []
    half = (N_PER_ROW - 1) / 2.0
    xs = [round((i - half) * PITCH, 4) for i in range(N_PER_ROW)]
    # bottom row = B1..B12, top row = A1..A12 (datasheet convention)
    for i, x in enumerate(xs):
        pads.append((f"B{i+1}", x, ROW_BOT_Y, PAD_W, ROW_BOT_H, PAD_RRATIO))
    for i, x in enumerate(xs):
        pads.append((f"A{i+1}", x, ROW_TOP_Y, PAD_W, ROW_TOP_H, PAD_RRATIO))
    return pads


def shields():
    pads = []
    n = 1
    for (x, y, w, h) in TABS:
        for sx in (x, -x):                    # mirror across connector centre
            pads.append((f"S{n}", round(sx, 4), y, w, h, TAB_RRATIO)); n += 1
    return pads


def build():
    pads = contacts() + shields()
    # copper bounding box → courtyard
    xs = [p[1]-p[3]/2 for p in pads] + [p[1]+p[3]/2 for p in pads]
    ys = [p[2]-p[4]/2 for p in pads] + [p[2]+p[4]/2 for p in pads]
    cb = (min(xs), min(ys), max(xs), max(ys))
    crt = (cb[0]-COURTYARD_M, cb[1]-COURTYARD_M, cb[2]+COURTYARD_M, cb[3]+COURTYARD_M)
    return pads, cb, crt


# ── KiCad .kicad_mod writer (note: KiCad Y is DOWN, so we negate Y) ───────────
def to_kicad(pads, cb, crt):
    L = []
    L.append(f'(footprint "{NAME}" (version 20240108) (generator "designstudio")')
    L.append('  (layer "F.Cu")')
    L.append(f'  (descr "USB-C receptacle, extracted from USB4910 datasheet recommended land pattern")')
    L.append(f'  (attr smd)')
    L.append(f'  (fp_text reference "J**" (at 0 {-(crt[3]+0.8):.3f}) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
    L.append(f'  (fp_text value "{NAME}" (at 0 {-(crt[1]-0.8):.3f}) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
    # component outline (F.Fab)
    ow, oh = OUTLINE_W/2, OUTLINE_H/2
    for (x1,y1,x2,y2) in [(-ow,-oh, ow,-oh),(ow,-oh, ow,oh),(ow,oh,-ow,oh),(-ow,oh,-ow,-oh)]:
        L.append(f'  (fp_line (start {x1:.3f} {-y1:.3f}) (end {x2:.3f} {-y2:.3f}) '
                 f'(stroke (width 0.1) (type solid)) (layer "F.Fab"))')
    # courtyard (F.CrtYd)
    for (x1,y1,x2,y2) in [(crt[0],crt[1],crt[2],crt[1]),(crt[2],crt[1],crt[2],crt[3]),
                          (crt[2],crt[3],crt[0],crt[3]),(crt[0],crt[3],crt[0],crt[1])]:
        L.append(f'  (fp_line (start {x1:.3f} {-y1:.3f}) (end {x2:.3f} {-y2:.3f}) '
                 f'(stroke (width 0.05) (type solid)) (layer "F.CrtYd"))')
    # pin-1 silk marker (near A1 / B1, leftmost)
    L.append(f'  (fp_circle (center {-(N_PER_ROW-1)/2*PITCH-0.4:.3f} {-(ROW_TOP_Y+0.2):.3f}) '
             f'(end {-(N_PER_ROW-1)/2*PITCH-0.2:.3f} {-(ROW_TOP_Y+0.2):.3f}) '
             f'(stroke (width 0.12) (type solid)) (fill none) (layer "F.SilkS"))')
    # pads
    for (name, x, y, w, h, rr) in pads:
        L.append(f'  (pad "{name}" smd roundrect (at {x:.4f} {-y:.4f}) (size {w:.3f} {h:.3f}) '
                 f'(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio {rr}))')
    L.append(')')
    return "\n".join(L)


# ── DesignStudio .fp.svg writer (so the Footprint Lib tab can Load it) ────────
# loadSvg() reads: <rect ds:type="pad"> as pads, <path> as outline; SVG is Y-DOWN
# so a pad centred (cx,cy,w,h) → x=cx-w/2, y=-cy-h/2.
def to_fp_svg(pads):
    ow, oh = OUTLINE_W/2, OUTLINE_H/2
    body = []
    body.append('<?xml version="1.0" encoding="UTF-8"?>')
    body.append('<svg xmlns="http://www.w3.org/2000/svg"')
    body.append('     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"')
    body.append('     xmlns:ds="https://designstudio/footprint"')
    body.append(f'     ds:format="designstudio.footprint/1" ds:component="{NAME}.props.json"')
    body.append(f'     width="{OUTLINE_W+4}mm" height="{OUTLINE_H+4}mm" '
                f'viewBox="{-(OUTLINE_W+4)/2} {-(OUTLINE_H+4)/2} {OUTLINE_W+4} {OUTLINE_H+4}">')
    body.append('  <g inkscape:groupmode="layer" inkscape:label="copper-top" ds:layer="copper-top">')
    for (name, x, y, w, h, _rr) in pads:
        sx = x - w/2; sy = -y - h/2     # scene mm → SVG Y-down
        body.append(f'    <rect x="{sx:.4f}" y="{sy:.4f}" width="{w:.3f}" height="{h:.3f}" '
                    f'ds:type="pad" ds:pad="{name}" ds:net="" ds:shape="rect" '
                    f'ds:layer="copper-top" style="fill:#c89632;stroke:none"/>')
    body.append('  </g>')
    body.append('  <g inkscape:groupmode="layer" inkscape:label="silk-top" ds:layer="silk-top">')
    # component outline as a closed path (Y-down)
    pts = [(-ow,-oh),(ow,-oh),(ow,oh),(-ow,oh)]
    d = "M " + " L ".join(f"{px:.3f},{-py:.3f}" for px,py in pts) + " Z"
    body.append(f'    <path d="{d}" ds:type="outline" ds:layer="silk-top" '
                f'style="fill:none;stroke:#4b4b4b;stroke-width:0.1"/>')
    body.append('  </g>')
    body.append('</svg>')
    return "\n".join(body)


if __name__ == "__main__":
    pads, cb, crt = build()
    mod = to_kicad(pads, cb, crt)
    Path(f"{NAME}.kicad_mod").write_text(mod)
    Path(f"{NAME}.fp.svg").write_text(to_fp_svg(pads))

    # JSON config (the per-pad model, also feeds the app)
    cfg = {"name": NAME, "datum": "connector_centre", "units": "mm",
           "pitch": PITCH, "outline_mm": [OUTLINE_W, OUTLINE_H],
           "copper_bbox": [round(v,3) for v in cb],
           "pads": [{"name":n,"x":x,"y":y,"w":w,"h":h,"shape":"roundrect"} for (n,x,y,w,h,_) in pads]}
    Path(f"{NAME}.fp.json").write_text(json.dumps(cfg, indent=2))

    # sanity checks
    contacts_only = [p for p in pads if p[0][0] in "AB"]
    print(f"footprint: {NAME}")
    print(f"  pads: {len(pads)}  ({len(contacts_only)} contacts + {len(pads)-len(contacts_only)} shield/mount)")
    print(f"  copper bbox: {cb[2]-cb[0]:.2f} × {cb[3]-cb[1]:.2f} mm")
    print(f"  outline: {OUTLINE_W} × {OUTLINE_H} mm")
    # checks
    span = (N_PER_ROW-1)*PITCH
    print(f"  CHECK count×pitch: {N_PER_ROW}×{PITCH} → row span {span:.2f} mm")
    print(f"  CHECK pad_w < pitch: {PAD_W} < {PITCH} → {'OK' if PAD_W<PITCH else 'FAIL (short!)'}")
    print(f"  CHECK gap: {PITCH-PAD_W:.2f} mm between contacts")
    print(f"  wrote {NAME}.kicad_mod + {NAME}.fp.svg + {NAME}.fp.json")
