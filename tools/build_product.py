#!/usr/bin/env python3
"""One-command industrial-product builder for DesignStudio workspaces.

Usage (inside FreeCAD-hosted python, e.g. DesignStudioCmd):
    build_product.py <workspace> <bom.json>

Chains the enforced pipeline stages end-to-end so every new product follows
the same process: ERC corrections -> component solids -> optimal placement ->
instance swap -> generative ribs -> pipeline report.
"""
import sys, os, json, math, random, pathlib
import numpy as np
from scipy import ndimage as ndi
import scipy.sparse as sp
import scipy.sparse.linalg as spla

import FreeCAD as App
import Part

# FreeCAD's own parser consumes trailing positional arguments, so the
# workspace/BOM are passed through environment variables instead.
WS   = pathlib.Path(os.environ.get("DESIGNSTUDIO_WS", "")).resolve() if os.environ.get("DESIGNSTUDIO_WS") else None
BOMF = pathlib.Path(os.environ.get("DESIGNSTUDIO_BOM", "")).resolve() if os.environ.get("DESIGNSTUDIO_BOM") else None
ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

if WS is None or BOMF is None or not WS.is_dir() or not BOMF.is_file():
    print("BUILD_FAILED: set DESIGNSTUDIO_WS and DESIGNSTUDIO_BOM")
    raise SystemExit(1)

bom = json.load(open(BOMF))
board = bom["board"]; parts = bom["parts"]
BW, BH, BT = board["width"], board["height"], board.get("thickness", 1.6)
OX, OZ = board.get("origin_body", [55.0, 14.0])
Y0 = board.get("y0", 8.0)
P = "one-command build_product pass"
GND_OK = {"GND", "AGND", "DGND", "PGND"}

print(f"== build_product: {WS.name} | {len(parts)} parts ==")

# ---------------- stage 1: netlist + ERC + auto-apply ------------------------
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("erc_engine", TOOLS / "erc_engine.py")
erc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(erc)

elp = WS / "electronics"; elp.mkdir(parents=True, exist_ok=True)
nlp = elp / "netlist.json"
if not nlp.is_file():
    comps = []
    for p in parts:
        pins = {}
        if p["role"] in ("mcu", "flash"): pins = {"VCC": "3V3", "GND": "GND"}
        elif p["role"] == "buck": pins = {"VIN": "VBATT", "VOUT": "3V3", "GND": "GND"}
        elif p["role"] == "charger": pins = {"VIN": "5V_USB", "VBAT": "VBATT", "GND": "GND"}
        elif p["role"] == "usb": pins = {"VBUS": "5V_USB", "GND": "GND"}
        elif p["role"] == "crystal": pins = {"XTAL1": "OSC_IN", "XTAL2": "OSC_OUT", "GND": "GND"}
        comps.append({"ref": p["ref"], "mpn": p["mpn"], "value": p.get("value", ""), "pins": pins})
    json.dump({"schema": "design-studio.netlist/0",
               "components": comps, "nets": {"GND": []}}, open(nlp, "w"), indent=2)

raw = json.load(open(nlp))
report = erc.run_erc({"nets": raw.get("nets", {}),
                      "components": {c["ref"]: c for c in raw.get("components", [])}},
                     component_dir=TOOLS.parent)
applied = []
for f in report["warnings"]:
    prop = f.get("proposal") or {}
    cb = prop.get("connect_between") or []
    if not cb:
        continue                                   # unresolvable wiring → human review
    if any((t not in raw.get("nets", {})) and t.upper() not in GND_OK for t in cb):
        continue
    newref = f"AUTO{len(applied)+90}"
    if erc.apply_proposal(raw, prop, newref):
        applied.append(newref)
json.dump(raw, open(nlp, "w"), indent=2)
json.dump(report, open(elp / "erc-report.json", "w"), indent=2)
print(f"[1] netlist ok · erc errors={report['counts']['errors']} · auto-corrections={len(applied)}")

# ---------------- stage 2: component solid models ----------------------------
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))
from DesignStudio.vector_native_cad import execute_vector_program, control_datum_digest

cdir = WS / "mechanical" / "components"; cdir.mkdir(parents=True, exist_ok=True)
unique = {}
for p in parts:
    key = p["mpn"].replace("/", "-").replace(" ", "-")
    if key in unique: continue
    unique[key] = p
built = {}
for name, p in unique.items():
    L, W, H = p["l"], p["w"], p["h"]
    prog = {"schema": "design-studio.mechanical-cad-program/2",
            "program_id": f"component-{name}", "units": "mm",
            "author": "build_product feature.box",
            "envelope": {"min_mm": [-L/2, -W/2, 0.0], "max_mm": [L/2, W/2, H]},
            "control_datums": [{"id": "seat-plane-origin", "point_mm": [0.0, 0.0, 0.0],
                                "locked": True,
                                "digest": control_datum_digest([0.0, 0.0, 0.0])}],
            "commands": [{"id": "body", "op": "feature.box", "params": {
                "length_mm": L, "width_mm": W, "height_mm": H,
                "origin_mm": [-L/2, -W/2, 0.0]}, "provenance": [P]}],
            "checks": [{"kind": "valid_shape", "target": "body"},
                       {"kind": "watertight", "target": "body"}]}
    library_root = pathlib.Path(os.environ.get(
        "DESIGNSTUDIO_COMPONENT_LIBRARY",
        ROOT / "output" / "component-library",
    )).expanduser().resolve()
    d = library_root / name
    d.mkdir(parents=True, exist_ok=True)
    json.dump(prog, open(d / "model-program.json", "w"), indent=2)
    doc = App.newDocument(f"C_{name}")
    execute_vector_program(doc, prog)
    doc.recompute()
    solids = [s for o in doc.Objects if o.TypeId != "App::Part"
              for s in getattr(getattr(o, "Shape", None), "Solids", [])]
    comp = Part.makeCompound(solids)
    comp.exportStep(str(cdir / f"{name}.step"))
    built[name] = round(sum(s.Volume for s in solids), 1)
    App.closeDocument(doc.Name)
print(f"[2] component models built: {len(built)} · volumes(mm3) {built}")

# ---------------- stage 3: optimal placement ---------------------------------
BY_REF = {p["ref"]: p for p in parts}
KEEP_OUTS = bom.get("keep_outs", [])
AFFINITY = [tuple(a) for a in bom.get("affinity", [])]
POWER_NEAR = bom.get("power_near", ["J1"])

def wh(r, rot):
    part = BY_REF[r]
    return (part["w"], part["l"]) if rot == 90 else (part["l"], part["w"])

def rect(r, p):
    w, h = wh(r, p["rot"])
    return (p["x"]-w/2, p["y"]-h/2, p["x"]+w/2, p["y"]+h/2)

def overlap(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0]); dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx*dy if dx > 0 and dy > 0 else 0.0

def cost(state):
    c = 0.0
    rects = {r: rect(r, p) for r, p in state.items()}
    ids = list(state)
    for i, a in enumerate(ids):
        for b in ids[i+1:]:
            ov = overlap(rects[a], rects[b])
            if ov > 1e-6: c += 5000*ov
    for r, p in state.items():
        w, h = wh(r, p["rot"])
        if not (p["x"]-w/2 >= -0.01 and p["x"]+w/2 <= BW+0.01 and
                p["y"]-h/2 >= -0.01 and p["y"]+h/2 <= BH+0.01):
            c += 20000
        for ko in KEEP_OUTS:
            if ko.get("except") == r: continue
            pr = rects[r]
            ix = max(0, min(pr[2], ko["x1"]) - max(pr[0], ko["x0"]))
            iy = max(0, min(pr[3], ko["y1"]) - max(pr[1], ko["y0"]))
            if ix*iy > 1e-6: c += 8000*(ix*iy)
    def D(a, b):
        pa, pb = state[a], state[b]
        return math.hypot(pa["x"]-pb["x"], pa["y"]-pb["y"])
    for a, b, dmax in AFFINITY:
        if a in state and b in state and D(a, b) > dmax:
            c += (D(a, b)-dmax)*120
    for r in POWER_NEAR:
        if r in state and "J1" in state:
            c += max(0.0, D(r, "J1")-30.0)*40
    return c

random.seed(bom.get("seed", 42))
state = {p["ref"]: dict(p.get("anchor") or p.get("fixed") or
                        {"x": BW/2, "y": BH/2, "rot": 0}) for p in parts}
for p in parts:
    if p.get("fixed"): state[p["ref"]] = dict(p["fixed"]); state[p["ref"]].setdefault("rot", 0)
    elif p.get("anchor"): state[p["ref"]] |= {"rot": 0}
cur = cost(state); best, bestc = dict(state), cur
T0, T1, ITERS_P = 400.0, 1.0, 60000
movable = [p["ref"] for p in parts if not p.get("fixed")]
for it in range(ITERS_P):
    T = T0*((T1/T0)**(it/ITERS_P))
    cand = {k: dict(v) for k, v in state.items()}
    r = random.choice(movable)
    if random.random() < 0.75:
        cand[r]["x"] += random.uniform(-6, 6); cand[r]["y"] += random.uniform(-6, 6)
    else:
        cand[r]["rot"] = 90-cand[r]["rot"]
    cc = cost(cand)
    if cc < cur or random.random() < math.exp((cur-cc)/max(T, 1e-9)):
        state = cand; cur = cc
        if cur < bestc: best, bestc = dict(state), cur
for r, p in best.items():
    w, h = wh(r, p["rot"])
    p["x"] = min(max(p["x"], w/2), BW-w/2)
    p["y"] = min(max(p["y"], h/2), BH-h/2)

placements = [{"ref": r, "mpn": BY_REF[r]["mpn"],
               "x_mm": round(p["x"], 3), "y_mm": round(p["y"], 3),
               "rot_deg": p["rot"], "side": "top",
               "w_mm": wh(r, p["rot"])[0], "l_mm": wh(r, p["rot"])[1],
               "h_mm": BY_REF[r]["h"]} for r, p in sorted(best.items())]
json.dump({"board_mm": [BW, BH, BT], "final_cost": round(cost(best), 1),
           "placements": placements},
          open(elp / "placement-report.json", "w"), indent=2)

ds_p = elp / "product.dsproj"
ds = json.load(open(ds_p)) if ds_p.is_file() else {
    "board_cutouts": [], "assembly": {"joints": [], "tree": []},
    "document_id": "project:pipeline-build", "revision": 1}
ds["board_width_mm"] = BW; ds["board_height_mm"] = BH
ds["footprints"] = [{"ref": pl["ref"], "lib": pl["mpn"], "pads": [],
                     "x_mm": pl["x_mm"], "y_mm": pl["y_mm"], "rot_deg": pl["rot_deg"],
                     "side": "top", "h3d_mm": pl["h_mm"],
                     "w_mm": pl["w_mm"], "l_mm": pl["l_mm"]} for pl in placements]
json.dump(ds, open(ds_p, "w"), indent=2)
print(f"[3] placement ok · cost={round(cost(best),1)}")

# ---------------- stage 4: instance swap into body ---------------------------
body_path = WS / "mechanical" / "product.FCStd"
if body_path.is_file():
    doc = App.openDocument(str(body_path))
    added = updated = 0
    def _comp_shape(pl):
        name = pl["mpn"].replace("/", "-").replace(" ", "-")
        sh = Part.Shape(); sh.read(str(cdir / f"{name}.step"))
        bb = sh.BoundBox
        sh.translate(App.Vector(-(bb.XMin+bb.XLength/2),
                                -(bb.YMin+bb.YLength/2), -bb.ZMin))
        m = App.Matrix(1,0,0,0,  0,0,1,0,  0,1,0,0,  0,0,0,1)
        sh = sh.transformGeometry(m)
        sh.Placement = App.Placement(App.Vector(0,0,0),
            App.Rotation(App.Vector(0,1,0), math.radians(pl["rot_deg"])))
        cx, cz = OX+pl["x_mm"], OZ+pl["y_mm"]
        sh.translate(App.Vector(cx-(sh.BoundBox.XMin+sh.BoundBox.XLength/2),
                                Y0+BT,
                                cz-(sh.BoundBox.ZMin+sh.BoundBox.ZLength/2)))
        return sh
    if doc.getObject("BOARD_SLAB") is None:
        slab = doc.addObject("Part::Feature", "BOARD_SLAB")
        slab.Shape = Part.makeBox(BW, BT, BH, App.Vector(OX, Y0, OZ))
        added += 1
    for pl in placements:
        nname = f"COMP_{pl['ref']}"
        sh = _comp_shape(pl)
        obj = doc.getObject(nname)
        if obj is None:
            obj = doc.addObject("Part::Feature", nname); added += 1
        else:
            updated += 1
        obj.Shape = sh
        obj.Label = f"{pl['ref']} · {pl['mpn']}"
    doc.recompute(); doc.save()
    print(f"[4] instance swap: +{added} new · {updated} updated in body")
else:
    print("[4] no mechanical/product.FCStd — body stage pending; skipped")

# ---------------- stage 5: manufacturable rib network -------------------------
os.environ["DESIGNSTUDIO_WS"] = str(WS)
try:
    from build_molded_structure import build_workspace as build_molded_structure
    structure = build_molded_structure(WS, board={
        "width": BW, "height": BH, "thickness": BT,
        "origin_body": [OX, OZ], "y0": Y0})
    prog = structure["program"]
    prog = json.load(open(WS / "mechanical" / "structure-program.json"))
    ctrl = "DS_V2_" + prog["program_id"].replace("-", "_")
    if doc.getObject(ctrl) is not None:
        for o in list(doc.Objects):
            if o.Name.startswith("DS_V2_rib"):
                doc.removeObject(o.Name)
        doc.removeObject(ctrl)
        doc.recompute()
    execute_vector_program(doc, prog)
    doc.recompute()
    doc.save()
    ribs = [o.Name for o in doc.Objects if o.Name.startswith("DS_V2_rib")
            or o.Name.startswith("Rib_")]
    print(f"[5] structure ribs in body: {len(ribs)}")
except SystemExit as e:
    print(f"[5] structure stage issue: exit {e}")
except Exception as e:
    print(f"[5] structure stage issue: {e}")

# ---------------- stage 6: pipeline report -----------------------------------
spec2 = _ilu.spec_from_file_location(
    "pipeline", ROOT / "freecad" / "DesignStudioWorkbench" / "DesignStudio" / "pipeline.py")
pl_mod = _ilu.module_from_spec(spec2); spec2.loader.exec_module(pl_mod)
rep = pl_mod.inspect(WS)
print(pl_mod.format_report(rep))
print("BUILD_OK")
