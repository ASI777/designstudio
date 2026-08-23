#!/usr/bin/env python3
"""Resolve cross-net via->pad copper overlaps under the BGA by nudging each
offending power-stitch via into the nearest clear channel. Plane connection is
provided by the copper pour, so a sub-0.35mm move preserves connectivity; we
verify that separately via the headless DRC (NET_ISLAND must not increase).

Writes <proj>.fixed.dsproj. Does not touch the original."""
import json, math, sys, copy

def world_pads(d):
    NL = d["copper_layers"]
    pads = []
    for fp in d["footprints"]:
        r = math.radians(fp.get("rot_deg", 0)); side = fp.get("side", 0)
        lay = 0 if side == 0 else NL - 1
        for p in fp.get("pads", []):
            x = fp["x_mm"] + p["x_mm"]*math.cos(r) - p["y_mm"]*math.sin(r)
            y = fp["y_mm"] + p["x_mm"]*math.sin(r) + p["y_mm"]*math.cos(r)
            pads.append((x, y, p.get("w_mm",0.6), p.get("h_mm",0.25),
                         p.get("net",-1), p.get("th",False), lay))
    return pads

def ptquad(vx, vy, px, py, pw, ph):
    dx = max(abs(vx-px) - pw/2, 0); dy = max(abs(vy-py) - ph/2, 0)
    return math.hypot(dx, dy)

def via_layers(v):
    a, b = v.get("from",0), v.get("to",1)
    return set(range(min(a,b), max(a,b)+1))

def min_crossnet_gap(vx, vy, vr, vnet, vl, pads, real_only):
    g = 1e9
    for (px, py, pw, ph, pn, th, play) in pads:
        if pn == vnet: continue
        if real_only and pn < 0: continue
        if not (th or play in vl): continue
        g = min(g, ptquad(vx, vy, px, py, pw, ph) - vr)
    return g

def main():
    proj = sys.argv[1] if len(sys.argv) > 1 else "single_axis_FOC_Field_Oriented_Control_SD2.dsproj"
    d = json.load(open(proj))
    pads = world_pads(d)
    MARGIN = 0.10            # required clearance to any foreign pad
    moved, failed = [], []
    for i, v in enumerate(d["vias"]):
        vr = v.get("dia_mm",0.6)/2; vnet = v.get("net",-1); vl = via_layers(v)
        x0, y0 = v["x_mm"], v["y_mm"]
        if min_crossnet_gap(x0, y0, vr, vnet, vl, pads, True) >= 0:   # no real overlap
            continue
        # search nearby offsets; minimize displacement s.t. all REAL foreign
        # pads clear by MARGIN and NC pads at least don't overlap.
        best = None
        rng = [k*0.05 for k in range(-7, 8)]
        for dy in rng:
            for dx in rng:
                nx, ny = x0+dx, y0+dy
                gr = min_crossnet_gap(nx, ny, vr, vnet, vl, pads, True)
                ga = min_crossnet_gap(nx, ny, vr, vnet, vl, pads, False)
                if gr >= MARGIN and ga >= 0.0:
                    disp = math.hypot(dx, dy)
                    if best is None or disp < best[0]:
                        best = (disp, nx, ny)
        if best:
            v["x_mm"], v["y_mm"] = round(best[1], 4), round(best[2], 4)
            moved.append((i, round(x0,3), round(y0,3), round(best[1],3), round(best[2],3), round(best[0],3)))
        else:
            failed.append(i)
    out = proj.replace(".dsproj", ".fixed.dsproj")
    json.dump(d, open(out, "w"), separators=(",", ":"))
    print(f"nudged {len(moved)} shorting vias, {len(failed)} unresolved -> {out}")
    for m in moved:
        print(f"  via#{m[0]} ({m[1]},{m[2]}) -> ({m[3]},{m[4]})  moved {m[5]} mm")
    if failed:
        print("  UNRESOLVED via indices:", failed)

if __name__ == "__main__":
    main()
