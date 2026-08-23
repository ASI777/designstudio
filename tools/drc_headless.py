#!/usr/bin/env python3
"""Headless DRC harness: loads a .dsproj, rebuilds the native board exactly like
CoreBridge::rebuildBoard, runs DRC with the same options as CoreBridge::runDrc,
and prints categorized violations. Pure ctypes against libdesigncore.so."""
import sys, json, ctypes as C, math, collections

NM = 1_000_000  # nm per mm

class DcPadDef(C.Structure):
    _fields_ = [("x", C.c_int64), ("y", C.c_int64), ("w", C.c_int64), ("h", C.c_int64),
                ("netId", C.c_int32), ("throughHole", C.c_int32), ("drill", C.c_int64),
                ("name", C.c_char * 16)]

class DcDrcOptions(C.Structure):
    _fields_ = [("defaultClearance", C.c_int64), ("minTraceWidth", C.c_int64),
                ("minDrill", C.c_int64), ("minAnnularRing", C.c_int64),
                ("minDrillToDrill", C.c_int64),
                ("checkConnectivity", C.c_int32), ("checkSkew", C.c_int32),
                ("minMicroviaDrill", C.c_int64), ("minMicroviaWall", C.c_int64)]

class DcDrcViolation(C.Structure):
    _fields_ = [("rule", C.c_int32), ("_pad", C.c_int32),
                ("x", C.c_int64), ("y", C.c_int64),
                ("itemA", C.c_uint64), ("itemB", C.c_uint64),
                ("message", C.c_char * 192)]

# Index = dc::DrcRule value (drc.h). Rule codes are 1-based; slot 0 unused.
RULE_NAMES = ["?","TRACE_CLEARANCE","PAD_TRACE_CLEARANCE","PAD_CLEARANCE","BOARD_EDGE",
              "MIN_WIDTH","MIN_DRILL","VIA_TRACE_CLEARANCE","VIA_PAD_CLEARANCE",
              "VIA_CLEARANCE","ANNULAR_RING","DRILL_TO_DRILL","NET_ISLAND","SKEW"]

def load_lib(path):
    lib = C.CDLL(path)
    lib.dc_board_create.restype = C.c_void_p
    lib.dc_footprint_add.restype = C.c_uint64
    lib.dc_footprint_add.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int64,
        C.c_int64, C.c_double, C.c_int32, C.POINTER(DcPadDef), C.c_int32]
    lib.dc_trace_add.restype = C.c_uint64
    lib.dc_trace_add.argtypes = [C.c_void_p]+[C.c_int64]*5+[C.c_int32]*3
    lib.dc_via_add.restype = C.c_uint64
    lib.dc_via_add.argtypes = [C.c_void_p]+[C.c_int64]*4+[C.c_int32]*3
    lib.dc_class_set.argtypes = [C.c_void_p, C.c_int32, C.c_char_p]+[C.c_int64]*6+[C.c_int32]
    lib.dc_net_set.argtypes = [C.c_void_p, C.c_int32, C.c_char_p, C.c_int32]
    lib.dc_board_set_outline.argtypes = [C.c_void_p]+[C.c_int64]*4
    lib.dc_board_set_copper_layers.argtypes = [C.c_void_p, C.c_int32]
    lib.dc_drc_run.argtypes = [C.c_void_p, C.POINTER(DcDrcOptions)]
    lib.dc_drc_run.restype = C.c_int32
    lib.dc_drc_get.argtypes = [C.c_void_p, C.c_int32, C.POINTER(DcDrcViolation)]
    lib.dc_drc_get.restype = C.c_int32
    return lib

def build(lib, d):
    b = lib.dc_board_create()
    lib.dc_board_set_copper_layers(b, int(d.get("copper_layers", 2)))
    lib.dc_board_set_outline(b, 0, 0, int(d["board_width_mm"]*NM), int(d["board_height_mm"]*NM))
    for nc in d.get("net_classes", []):
        lib.dc_class_set(b, nc["id"], nc.get("name","Default").encode(),
            int(nc.get("clearance_mm",0.2)*NM), int(nc.get("trace_width_mm",0.25)*NM),
            int(nc.get("via_diameter_mm",0.6)*NM), int(nc.get("via_drill_mm",0.3)*NM), 0,0,0)
    for n in d.get("net_table", []):
        lib.dc_net_set(b, n["id"], n["name"].encode(), n.get("class",0))
    for fp in d.get("footprints", []):
        pads = (DcPadDef*len(fp.get("pads",[])))()
        for i,p in enumerate(fp.get("pads",[])):
            pads[i] = DcPadDef(int(p["x_mm"]*NM), int(p["y_mm"]*NM),
                int(p.get("w_mm",0.6)*NM), int(p.get("h_mm",0.25)*NM),
                p.get("net",-1), 1 if p.get("th",False) else 0,
                int(p.get("drill_mm",0)*NM), p.get("name","").encode()[:15])
        lib.dc_footprint_add(b, fp["ref"].encode(), fp.get("lib","").encode(),
            int(fp["x_mm"]*NM), int(fp["y_mm"]*NM), float(fp.get("rot_deg",0)),
            fp.get("side",0), pads, len(pads))
    for t in d.get("traces", []):
        lib.dc_trace_add(b, int(t["ax_mm"]*NM), int(t["ay_mm"]*NM),
            int(t["bx_mm"]*NM), int(t["by_mm"]*NM), int(t.get("w_mm",0.25)*NM),
            t.get("layer",0), t.get("net",-1), 1 if t.get("pour",False) else 0)
    for v in d.get("vias", []):
        lib.dc_via_add(b, int(v["x_mm"]*NM), int(v["y_mm"]*NM),
            int(v.get("dia_mm",0.6)*NM), int(v.get("drill_mm",0.3)*NM),
            v.get("net",-1), v.get("from",0), v.get("to",1))
    return b

def run(lib, b):
    opt = DcDrcOptions(int(0.2*NM), int(0.1*NM), int(0.2*NM), int(0.05*NM),
                       int(0.5*NM), 1, 0, int(0.075*NM), int(0.1*NM))
    n = lib.dc_drc_run(b, C.byref(opt))
    if n < 0:
        print(f"dc_drc_run error: {n}"); return []
    out = []
    for i in range(n):
        v = DcDrcViolation()
        if lib.dc_drc_get(b, i, C.byref(v)) < 0: break
        rule = RULE_NAMES[v.rule] if 1 <= v.rule <= 13 else f"RULE_{v.rule}"
        out.append((rule, v.x/NM, v.y/NM, v.itemA, v.itemB,
                    v.message.decode("utf-8","replace")))
    return out

def main():
    proj = sys.argv[1] if len(sys.argv)>1 else "single_axis_FOC_Field_Oriented_Control_SD2.dsproj"
    libp = sys.argv[2] if len(sys.argv)>2 else "dist_linux/libdesigncore.so"
    d = json.load(open(proj))
    lib = load_lib(libp)
    b = build(lib, d)
    viol = run(lib, b)
    by = collections.Counter(v[0] for v in viol)
    print(f"=== DRC on {proj}: {len(viol)} violations ===")
    for r,c in by.most_common():
        print(f"  {r:18} {c}")
    print()
    # sample messages per rule
    seen = collections.defaultdict(int)
    for rule,x,y,a,bb,msg in viol:
        if seen[rule] < 4:
            print(f"[{rule}] ({x:.2f},{y:.2f}) A={a} B={bb} :: {msg}")
            seen[rule]+=1
    return viol

if __name__ == "__main__":
    main()
