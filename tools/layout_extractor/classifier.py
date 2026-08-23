#!/usr/bin/env python3
"""Constrained-primitive classifier (cv2-free).

Takes detected copper objects -- each carrying measured `features` and `geom`
(in mm) -- and maps every one onto a CAD primitive (pad / hole / region) using
the externalised grammar in rules.json: the first matching rule decides the tool
and its parameter snapping/adjustment. A final pass drops primitives that a rule
marks `reject_if_overlaps` when they collide with a higher-confidence primitive
(e.g. a "hole" that landed on a signal pad is a misdetection).
"""
import json


def load_rules(path):
    return json.load(open(path))


def _match_cond(v, cond):
    if isinstance(cond, bool):
        return bool(v) == cond
    lo, hi = cond
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _matches(features, when):
    return all(k in features and _match_cond(features[k], c) for k, c in when.items())


def _snap(v, g):
    return round(round(v / g) * g, 4)


def _dedupe(pts, eps=1e-4):
    """Drop consecutive coincident vertices (zero-length segments) so a smooth
    outline isn't padded with degenerate points."""
    out = []
    for q in pts:
        if not out or abs(q[0]-out[-1][0]) > eps or abs(q[1]-out[-1][1]) > eps:
            out.append(q)
    if len(out) > 1 and abs(out[0][0]-out[-1][0]) <= eps and abs(out[0][1]-out[-1][1]) <= eps:
        out.pop()
    return out


def _emit(obj, rule, grid, nominal):
    g, e, snap = obj["geom"], rule["emit"], rule.get("snap", {})
    x, y = g["x_mm"], g["y_mm"]
    if snap.get("grid"):
        x, y = _snap(x, grid), _snap(y, grid)
    p = {"tool": e["tool"], "ref": obj["ref"], "rule": rule["name"],
         "x_mm": round(x, 4), "y_mm": round(y, 4)}

    if e["tool"] == "pad":
        p["shape"] = e.get("shape", "rect")
        w, h = g["w_mm"], g["h_mm"]
        if "size_to_nominal" in snap:
            wk, hk = snap["size_to_nominal"]
            w, h = nominal[wk], nominal[hk]
        p["w_mm"], p["h_mm"] = round(w, 3), round(h, 3)
        p["corner_r_mm"] = round(e.get("corner_r", 0.0), 3)

    elif e["tool"] == "hole":
        d = g.get("drill_mm", 0.0)
        if "drill_to_nominal" in snap:
            d = nominal[snap["drill_to_nominal"]]
        p["drill_mm"] = round(d, 3)
        p["plated"] = e.get("plated", False)
        # NPTH = a drill hit only, NO copper annular ring (plastic alignment peg)
        p["w_mm"] = p["h_mm"] = round(d, 3)
        p["shape"] = "circle"

    elif e["tool"] == "region":
        # NB: curve vertices are NEVER grid-snapped -- snapping a smooth R0.45/R0.40
        # arc onto a 0.05 mm grid quantises it into a visible staircase and collapses
        # neighbouring vertices into zero-length segments. Only the placement centroid
        # (x,y above) snaps; the outline keeps full float precision, then de-duped.
        p["points"] = _dedupe([[round(px, 4), round(py, 4)] for px, py in g["points"]])
        p["hole"] = _dedupe([[round(px, 4), round(py, 4)]
                             for px, py in g.get("hole", [])])           # hollow centre

    if rule.get("reject_if_overlaps"):
        p["_reject_if_overlaps"] = True
    return p


def _point_in_poly(x, y, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]; x1, y1 = pts[(i+1) % n]
        if (y0 > y) != (y1 > y):
            xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xc:
                inside = not inside
    return inside


def _contains(host, x, y):
    """Is point (x,y) inside host's actual copper (rect for pad/hole, polygon for
    region)? Used to reject a feature drilled on top of real copper -- accurate
    where a bounding box would falsely collide with a non-convex keyhole."""
    if host["tool"] == "region":
        return _point_in_poly(x, y, host["points"])
    w, h = host.get("w_mm", 0), host.get("h_mm", 0)
    return abs(x - host["x_mm"]) <= w/2 and abs(y - host["y_mm"]) <= h/2


def classify(objects, rules):
    """Return (accepted_primitives, rejected_primitives)."""
    grid = rules.get("grid_mm", 0.05)
    nominal = rules.get("nominal", {})
    prims = []
    for obj in objects:
        for rule in rules["rules"]:
            if _matches(obj["features"], rule["when"]):
                prims.append(_emit(obj, rule, grid, nominal))
                break                                      # first match wins

    accepted, rejected = [], []
    for p in prims:
        confident = [q for q in accepted if not q.get("_reject_if_overlaps")]
        hits = [q for q in confident if _contains(q, p["x_mm"], p["y_mm"])]
        if p.get("_reject_if_overlaps") and hits:
            p["rejected"] = f"centre inside {hits[0]['ref']}"
            rejected.append(p)
        else:
            accepted.append(p)
    for p in accepted:
        p.pop("_reject_if_overlaps", None)
    return accepted, rejected
