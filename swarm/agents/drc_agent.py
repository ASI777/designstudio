#!/usr/bin/env python3
"""drc_agent.py — Run the native C++ DRC engine on a .dsproj file.

Builds the exact same native board that BoardDocument.SyncNative() does,
then calls dc_drc_run with the same options as RunDrcDetailed(), and prints
every violation grouped by rule.

Usage:
  python3 drc_agent.py <project.dsproj>
"""
from __future__ import annotations
import ctypes, json, math, os, sys
from collections import defaultdict
from pathlib import Path

# ── Locate libdesigncore.so ───────────────────────────────────────────────────
def _find_lib() -> str:
    explicit = os.environ.get("DESIGNSTUDIO_CORE_LIB", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError(f"DESIGNSTUDIO_CORE_LIB does not exist: {candidate}")
    here = Path(__file__).resolve()
    for d in [here.parent, here.parent.parent, here.parent.parent.parent]:
        for cand in [
            d / "core/build_linux/libdesigncore.so",
            d / "core/build/Release/designcore.dll",
            d / "libdesigncore.so",
        ]:
            if cand.exists():
                return str(cand)
    raise RuntimeError("libdesigncore.so not found — build core first")

LIB_PATH = _find_lib()
NM_PER_MM = 1_000_000

# ── ctypes struct definitions (mirror c_api.h) ────────────────────────────────
class DcPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int64), ("y", ctypes.c_int64)]


class DcPadDef(ctypes.Structure):
    # Mirrors DcPadDef in c_api.h exactly — name is char[16], not 32
    _fields_ = [
        ("x",           ctypes.c_int64),
        ("y",           ctypes.c_int64),
        ("w",           ctypes.c_int64),
        ("h",           ctypes.c_int64),
        ("net_id",      ctypes.c_int32),
        ("through_hole",ctypes.c_int32),
        ("drill",       ctypes.c_int64),
        ("name",        ctypes.c_char * 16),
    ]

class DcDrcOptions(ctypes.Structure):
    _fields_ = [
        ("default_clearance",  ctypes.c_int64),
        ("min_trace_width",    ctypes.c_int64),
        ("min_drill",          ctypes.c_int64),
        ("min_annular_ring",   ctypes.c_int64),
        ("min_drill_to_drill", ctypes.c_int64),
        ("check_connectivity", ctypes.c_int32),
        ("check_skew",         ctypes.c_int32),
        ("min_microvia_drill", ctypes.c_int64),
        ("min_microvia_wall",  ctypes.c_int64),
    ]


class DcDrcOptionsV2(ctypes.Structure):
    _fields_ = [
        ("abi_version",           ctypes.c_uint32),
        ("struct_size",           ctypes.c_uint32),
        ("default_clearance",     ctypes.c_int64),
        ("min_trace_width",       ctypes.c_int64),
        ("min_drill",             ctypes.c_int64),
        ("min_annular_ring",      ctypes.c_int64),
        ("min_drill_to_drill",    ctypes.c_int64),
        ("check_connectivity",    ctypes.c_int32),
        ("check_skew",            ctypes.c_int32),
        ("min_microvia_drill",    ctypes.c_int64),
        ("min_microvia_wall",     ctypes.c_int64),
        ("min_copper_to_edge",    ctypes.c_int64),
        ("min_copper_to_hole",    ctypes.c_int64),
        ("min_courtyard_clearance", ctypes.c_int64),
        ("check_unassigned_copper", ctypes.c_int32),
        ("reserved",              ctypes.c_int32),
    ]

class DcDrcViolation(ctypes.Structure):
    _fields_ = [
        ("rule",     ctypes.c_int32),
        ("_pad",     ctypes.c_int32),
        ("x",        ctypes.c_int64),
        ("y",        ctypes.c_int64),
        ("item_a",   ctypes.c_uint64),
        ("item_b",   ctypes.c_uint64),
        ("message",  ctypes.c_char * 192),
    ]

# ── Load library and declare function signatures ──────────────────────────────
def _load_lib():
    lib = ctypes.CDLL(LIB_PATH)

    lib.dc_board_create.restype        = ctypes.c_void_p
    lib.dc_board_create.argtypes       = []
    lib.dc_board_destroy.restype       = None
    lib.dc_board_destroy.argtypes      = [ctypes.c_void_p]
    lib.dc_board_clear.restype         = ctypes.c_int32
    lib.dc_board_clear.argtypes        = [ctypes.c_void_p]
    lib.dc_board_set_outline.restype   = ctypes.c_int32
    lib.dc_board_set_outline.argtypes  = [ctypes.c_void_p,
                                          ctypes.c_int64, ctypes.c_int64,
                                          ctypes.c_int64, ctypes.c_int64]
    lib.dc_board_set_outline_polygon.restype = ctypes.c_int32
    lib.dc_board_set_outline_polygon.argtypes = [ctypes.c_void_p,
                                                  ctypes.POINTER(DcPoint),
                                                  ctypes.c_int32]
    lib.dc_board_cutout_add.restype = ctypes.c_int32
    lib.dc_board_cutout_add.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(DcPoint),
                                        ctypes.c_int32]
    lib.dc_board_set_copper_layers.restype  = ctypes.c_int32
    lib.dc_board_set_copper_layers.argtypes = [ctypes.c_void_p, ctypes.c_int32]

    lib.dc_class_set.restype  = ctypes.c_int32
    lib.dc_class_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                 ctypes.c_char_p,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int32]
    lib.dc_class_routing_policy_set.restype = ctypes.c_int32
    lib.dc_class_routing_policy_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                                 ctypes.c_uint64, ctypes.c_uint32,
                                                 ctypes.c_int32]
    lib.dc_layer_policy_set.restype = ctypes.c_int32
    lib.dc_layer_policy_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                        ctypes.c_char_p, ctypes.c_int32,
                                        ctypes.c_int32, ctypes.c_int32,
                                        ctypes.c_int64, ctypes.c_char_p,
                                        ctypes.c_char_p]
    lib.dc_copper_zone_set.restype = ctypes.c_int32
    lib.dc_copper_zone_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                       ctypes.c_char_p, ctypes.POINTER(DcPoint),
                                       ctypes.c_int32, ctypes.c_int32,
                                       ctypes.c_int32, ctypes.c_int64,
                                       ctypes.c_double, ctypes.c_int32,
                                       ctypes.c_char_p, ctypes.c_char_p]
    lib.dc_rule_area_set.restype = ctypes.c_int32
    lib.dc_rule_area_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                                     ctypes.c_char_p, ctypes.POINTER(DcPoint),
                                     ctypes.c_int32, ctypes.c_int32,
                                     ctypes.c_int64, ctypes.c_int64,
                                     ctypes.c_int32, ctypes.c_int32,
                                     ctypes.c_int32, ctypes.c_char_p,
                                     ctypes.c_char_p]
    lib.dc_net_set.restype  = ctypes.c_int32
    lib.dc_net_set.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                               ctypes.c_char_p, ctypes.c_int32]

    lib.dc_footprint_add.restype  = ctypes.c_uint64
    lib.dc_footprint_add.argtypes = [ctypes.c_void_p,
                                     ctypes.c_char_p, ctypes.c_char_p,
                                     ctypes.c_int64, ctypes.c_int64,
                                     ctypes.c_double, ctypes.c_int32,
                                     ctypes.c_void_p, ctypes.c_int32]
    lib.dc_pad_clearance_set.restype = ctypes.c_int32
    lib.dc_pad_clearance_set.argtypes = [ctypes.c_void_p, ctypes.c_uint64,
                                         ctypes.c_char_p, ctypes.c_int64]

    lib.dc_trace_add.restype  = ctypes.c_uint64
    lib.dc_trace_add.argtypes = [ctypes.c_void_p,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int64,
                                 ctypes.c_int64, ctypes.c_int32,
                                 ctypes.c_int32, ctypes.c_int32]
    lib.dc_trace_min_width_set.restype = ctypes.c_int32
    lib.dc_trace_min_width_set.argtypes = [ctypes.c_void_p, ctypes.c_uint64,
                                           ctypes.c_int64]

    lib.dc_via_add.restype  = ctypes.c_uint64
    lib.dc_via_add.argtypes = [ctypes.c_void_p,
                               ctypes.c_int64, ctypes.c_int64,
                               ctypes.c_int64, ctypes.c_int64,
                               ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]

    lib.dc_drc_run.restype  = ctypes.c_int32
    lib.dc_drc_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.dc_drc_run_v2.restype = ctypes.c_int32
    lib.dc_drc_run_v2.argtypes = [ctypes.c_void_p,
                                   ctypes.POINTER(DcDrcOptionsV2)]
    lib.dc_drc_get.restype  = ctypes.c_int32
    lib.dc_drc_get.argtypes = [ctypes.c_void_p, ctypes.c_int32,
                               ctypes.POINTER(DcDrcViolation)]
    return lib

# ── Board builder (mirrors BoardDocument.SyncNative) ─────────────────────────
def nm(mm: float) -> int:
    return int(round(mm * NM_PER_MM))


def drc_options_from_project(data: dict, *, check_skew: bool | None = None
                             ) -> DcDrcOptionsV2:
    rules = data.get("pcb_rules", {})
    limits = rules.get("limits_mm", {})
    checks = rules.get("checks", {})
    skew = bool(checks.get("skew", True)) if check_skew is None else check_skew
    options = DcDrcOptionsV2(
        abi_version=2,
        struct_size=ctypes.sizeof(DcDrcOptionsV2),
        default_clearance=nm(float(limits.get("default_clearance", 0.2))),
        min_trace_width=nm(float(limits.get("min_trace_width", 0.1))),
        min_drill=nm(float(limits.get("min_mechanical_drill", 0.15))),
        min_annular_ring=nm(float(limits.get("min_annular_ring", 0.1))),
        min_drill_to_drill=nm(float(limits.get("min_drill_to_drill", 0.25))),
        check_connectivity=1 if checks.get("connectivity", True) else 0,
        check_skew=1 if skew else 0,
        min_microvia_drill=nm(float(limits.get("min_microvia_drill", 0.075))),
        min_microvia_wall=nm(float(limits.get("min_microvia_wall", 0.1))),
        min_copper_to_edge=nm(float(limits.get("min_copper_to_edge", 0.25))),
        min_copper_to_hole=nm(float(limits.get("min_copper_to_hole", 0.25))),
        min_courtyard_clearance=nm(
            float(limits.get("min_courtyard_clearance", 0.25))),
        check_unassigned_copper=1,
        reserved=0,
    )
    return options


def build_board(lib, data: dict) -> ctypes.c_void_p:
    h = lib.dc_board_create()
    if not h:
        raise RuntimeError("dc_board_create failed")
    lib.dc_board_clear(h)

    bw = data.get("board_width_mm",  100.0)
    bh = data.get("board_height_mm",  80.0)
    layers = data.get("copper_layers", 8)
    outline = data.get("board_outline_pts", [])
    if len(outline) >= 3:
        PointArray = DcPoint * len(outline)
        points = PointArray(*(DcPoint(nm(point[0]), nm(point[1]))
                              for point in outline))
        lib.dc_board_set_outline_polygon(h, points, len(outline))
    else:
        lib.dc_board_set_outline(h, 0, 0, nm(bw), nm(bh))
    for cutout in data.get("board_cutouts", []):
        if len(cutout) < 3:
            continue
        PointArray = DcPoint * len(cutout)
        points = PointArray(*(DcPoint(nm(point[0]), nm(point[1]))
                              for point in cutout))
        if lib.dc_board_cutout_add(h, points, len(cutout)) != 0:
            raise RuntimeError("dc_board_cutout_add failed")
    lib.dc_board_set_copper_layers(h, layers)

    # Net classes
    for nc in data.get("net_classes", []):
        lib.dc_class_set(
            h, int(nc["id"]), nc["name"].encode(),
            nm(nc.get("clearance_mm", 0.2)),
            nm(nc.get("trace_width_mm", 0.15)),
            nm(nc.get("via_diameter_mm", nc.get("via_dia_mm", 0.6))),
            nm(nc.get("via_drill_mm", 0.3)),
            nm(nc.get("diff_pair_gap_mm", 0)),
            nm(nc.get("max_skew_mm", 0)),
            1 if nc.get("microvia", False) else 0,
        )
        allowed = nc.get("allowed_layers", list(range(layers)))
        layer_mask = sum(1 << int(layer) for layer in allowed)
        via_mask = 0
        for via_type in nc.get("allowed_via_types",
                               ["through", "blind", "buried", "microvia"]):
            via_mask |= {"through": 1, "blind": 2, "buried": 4,
                         "microvia": 8}.get(str(via_type), 0)
        lib.dc_class_routing_policy_set(
            h, int(nc["id"]), layer_mask, via_mask,
            int(nc.get("max_via_count", 0)))

    for policy in data.get("layer_policies", []):
        role = {"signal": 0, "plane": 1, "mixed": 2}.get(
            str(policy.get("role", "signal")).lower(), 0)
        direction = {"any": 0, "horizontal": 1, "vertical": 2}.get(
            str(policy.get("preferred_direction", "any")).lower(), 0)
        lib.dc_layer_policy_set(
            h, int(policy["layer"]), str(policy.get("name", "")).encode(),
            role, direction, 1 if policy.get("allow_routing", True) else 0,
            nm(policy.get("copper_thickness_mm", 0.035)),
            str(policy.get("source", "project")).encode(),
            str(policy.get("source_revision", "1")).encode())

    for area in data.get("rule_areas", []):
        polygon = area.get("pts", [])
        if len(polygon) < 3:
            continue
        PointArray = DcPoint * len(polygon)
        points = PointArray(*(DcPoint(nm(point[0]), nm(point[1]))
                              for point in polygon))
        lib.dc_rule_area_set(
            h, int(area["id"]), str(area.get("name", "")).encode(),
            points, len(polygon), int(area.get("layer", -1)),
            nm(area.get("clearance_mm", 0.0)),
            nm(area.get("min_trace_width_mm", 0.0)),
            1 if area.get("forbid_routing", False) else 0,
            1 if area.get("forbid_vias", False) else 0,
            1 if area.get("forbid_placement", False) else 0,
            str(area.get("source", "project")).encode(),
            str(area.get("source_revision", "1")).encode())

    # Nets
    for n in data.get("net_table", []):
        lib.dc_net_set(h, int(n["id"]), n["name"].encode(),
                       int(n.get("class_id", n.get("class", 0))))

    for zone in data.get("copper_zones", []):
        polygon = zone.get("pts", [])
        if len(polygon) < 3:
            continue
        PointArray = DcPoint * len(polygon)
        points = PointArray(*(DcPoint(nm(point[0]), nm(point[1]))
                              for point in polygon))
        lib.dc_copper_zone_set(
            h, int(zone["id"]), str(zone.get("name", "")).encode(),
            points, len(polygon), int(zone["net"]), int(zone["layer"]),
            nm(zone.get("clearance_mm", 0.2)),
            float(zone.get("min_island_area_mm2", 0.0)),
            1 if zone.get("require_connection", True) else 0,
            str(zone.get("source", "project")).encode(),
            str(zone.get("source_revision", "1")).encode())

    # Footprints
    for fp in data.get("footprints", []):
        pads = fp.get("pads", [])
        PadArray = DcPadDef * len(pads)
        pad_arr = PadArray()
        for i, p in enumerate(pads):
            pad_arr[i].x           = nm(p.get("x_mm", 0))
            pad_arr[i].y           = nm(p.get("y_mm", 0))
            pad_arr[i].w           = nm(p.get("w_mm", 1))
            pad_arr[i].h           = nm(p.get("h_mm", 1))
            pad_arr[i].net_id      = int(p.get("net", -1))
            pad_arr[i].through_hole= 1 if p.get("th", False) else 0
            pad_arr[i].drill       = nm(p.get("drill_mm", 0))
            pad_arr[i].name        = p.get("name", "")[:15].encode()
        footprint_id = lib.dc_footprint_add(
            h,
            fp["ref"].encode(),
            fp.get("lib", "").encode(),
            nm(fp.get("x_mm", 0)),
            nm(fp.get("y_mm", 0)),
            float(fp.get("rot_deg", 0)),
            int(fp.get("side", 0)),
            pad_arr if pads else None,
            len(pads),
        )
        for pad in pads:
            clearance = float(pad.get("clearance_mm", 0))
            if footprint_id and clearance > 0:
                lib.dc_pad_clearance_set(h, footprint_id,
                                         str(pad.get("name", "")).encode(),
                                         nm(clearance))

    # Traces — key names from the .dsproj schema: ax_mm/ay_mm/bx_mm/by_mm/w_mm/pour
    for t in data.get("traces", []):
        trace_id = lib.dc_trace_add(
            h,
            nm(t.get("ax_mm", 0)), nm(t.get("ay_mm", 0)),
            nm(t.get("bx_mm", 0)), nm(t.get("by_mm", 0)),
            nm(t.get("w_mm", 0.15)),
            int(t.get("layer", 0)),
            int(t.get("net", -1)),
            1 if t.get("pour", False) else 0,
        )
        override = float(t.get("min_width_override_mm", 0))
        if trace_id and override > 0:
            lib.dc_trace_min_width_set(h, trace_id, nm(override))

    # Vias
    for v in data.get("vias", []):
        lib.dc_via_add(
            h,
            nm(v.get("x_mm", 0)), nm(v.get("y_mm", 0)),
            nm(v.get("dia_mm", 0.6)), nm(v.get("drill_mm", 0.3)),
            int(v.get("net", -1)),
            int(v.get("from", 0)),
            int(v.get("to",   7)),
        )

    return h

# ── Rule name lookup ──────────────────────────────────────────────────────────
RULE_NAMES = {
    1:  "TraceTraceClearance",
    2:  "PadTraceClearance",
    3:  "PadPadClearance",
    4:  "BoardEdge",
    5:  "TraceWidth",
    6:  "DrillSize",
    7:  "ViaTraceClearance",
    8:  "ViaPadClearance",
    9:  "ViaViaClearance",
    10: "AnnularRing",
    11: "DrillToDrill",
    12: "UnconnectedNet",
    13: "SkewExceeded",
    14: "UnassignedCopper",
    15: "CopperToEdge",
    16: "CopperToHole",
    17: "CourtyardOverlap",
    18: "RuleAreaViolation",
    19: "HeightConstraint",
    20: "ThermalSpacing",
    21: "TestAccess",
    22: "LayerPolicyViolation",
    23: "ViaPolicyViolation",
    24: "ZoneValidity",
    25: "RightAngleBend",
}

# ── Main ─────────────────────────────────────────────────────────────────────
def main(project_path: str) -> None:
    print(f"[drc-agent] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps   = data.get("footprints", [])
    traces= data.get("traces", [])
    vias  = data.get("vias", [])
    nets  = data.get("net_table", [])
    ncs   = data.get("net_classes", [])
    print(f"[drc-agent] {len(fps)} footprints, {len(traces)} traces, "
          f"{len(vias)} vias, {len(nets)} nets, {len(ncs)} net classes")

    lib = _load_lib()
    print(f"[drc-agent] Loaded {LIB_PATH}")

    h = build_board(lib, data)
    print(f"[drc-agent] Native board built — running DRC...")

    # Use the project's versioned manufacturing-rule contract. This keeps the
    # command-line agent aligned with the Qt unified verifier.
    opt = drc_options_from_project(data)

    total = lib.dc_drc_run_v2(h, ctypes.byref(opt))
    if total < 0:
        print(f"[drc-agent] dc_drc_run returned error {total}")
        lib.dc_board_destroy(h)
        return

    violations = []
    v = DcDrcViolation()
    for i in range(total):
        if lib.dc_drc_get(h, i, ctypes.byref(v)) == 0:
            violations.append({
                "rule":    v.rule,
                "name":    RULE_NAMES.get(v.rule, f"Rule{v.rule}"),
                "x_mm":    round(v.x / NM_PER_MM, 3),
                "y_mm":    round(v.y / NM_PER_MM, 3),
                "item_a":  v.item_a,
                "item_b":  v.item_b,
                "message": v.message.decode("utf-8", errors="replace"),
            })

    lib.dc_board_destroy(h)

    # ── Report ────────────────────────────────────────────────────────────────
    by_rule: dict[str, list] = defaultdict(list)
    for viol in violations:
        by_rule[viol["name"]].append(viol)

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  DRC complete — {total} violation{'s' if total != 1 else ''}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if total == 0:
        print("  ✓ No violations")
    else:
        for rule_name, viols in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            print(f"\n  [{rule_name}]  {len(viols)} violation{'s' if len(viols)>1 else ''}")
            shown = viols[:8]
            for viol in shown:
                loc = f"({viol['x_mm']:.2f}, {viol['y_mm']:.2f})"
                print(f"    {loc:20s}  {viol['message']}")
            if len(viols) > 8:
                print(f"    … and {len(viols)-8} more")

    print()
    print("  Summary by rule:")
    for rule_name, viols in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        print(f"    {rule_name:25s}  {len(viols):4d}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: drc_agent.py <project.dsproj>")
        sys.exit(1)
    if not os.path.exists(sys.argv[1]):
        print(f"error: not found: {sys.argv[1]}")
        sys.exit(1)
    main(sys.argv[1])
