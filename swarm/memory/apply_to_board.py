"""Apply-to-board — turn a harness design (components + netlist) into a .dsproj.

The final bridge from "the harness designed it" to "the user sees it on the
canvas". For each component it instantiates a PCB footprint (pads with real
geometry from the extracted component/2 JSON), builds the net table, assigns each
pad's net from the netlist, sizes the board, and writes the .dsproj. The Qt app's
file-watcher reloads it live; the subsystem-placer and router then take over.

Footprint geometry comes from the component/2 JSONs the datasheet phase extracts;
they are persisted to a component library so this step can read them back.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path

from swarm.runtime_paths import (component_fixture_dir, component_library_dir,
                                 component_library_db_path)

_VALID_LAYERS = {1, 2, 4, 6, 8, 10, 12}


def _validate_mechanical_contract(contract: object) -> None:
    if not isinstance(contract, dict) \
            or contract.get("schema") != "design-studio.mechanical-contract/1" \
            or contract.get("status") != "locked" \
            or contract.get("units") != "mm":
        raise ValueError("project mechanical_contract exists but is not a locked v1 contract")
    expected = contract.get("contract_digest")
    payload = copy.deepcopy(contract)
    payload.pop("contract_digest", None)

    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(value) for key, value in item.items()}
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    actual = hashlib.sha256(json.dumps(
        normalize(payload), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    if expected != actual:
        raise ValueError("project mechanical_contract digest mismatch")


def detect_layers(text: str, default: int = 2) -> int:
    """Pull a copper-layer count from an intent string ("4-layer board" → 4)."""
    if not text:
        return default
    m = re.search(r"(\d+)\s*-?\s*layer", text.lower())
    if m:
        n = int(m.group(1))
        if n in _VALID_LAYERS:
            return n
    return default

_LIB_DIR = component_library_dir()
_REPO_LIB = component_fixture_dir()


# ── Component library (raw component/2 JSON, keyed by MPN) ─────────────────────
def _safe(mpn: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn) or "part"


def save_component(comp2: dict) -> None:
    """Persist a component/2 JSON so apply-to-board can read its footprint later."""
    mpn = comp2.get("component", {}).get("mpn", "")
    if not mpn:
        return
    _LIB_DIR.mkdir(parents=True, exist_ok=True)
    (_LIB_DIR / f"{_safe(mpn)}.json").write_text(json.dumps(comp2))
    try:
        from .component_library import ComponentLibrary
        approved = ((comp2.get("extraction") or {}).get("verification") or {}).get("state") == "approved" \
            and (comp2.get("preview") or {}).get("state") == "published"
        with ComponentLibrary() as database, database.connection:
            database.upsert_component(
                comp2, status="approved" if approved else "draft",
                evidence_state=str((comp2.get("evidence") or {}).get("mpn_match") or "incomplete"))
    except Exception:
        # The existing JSON projection remains available during migration; the
        # caller's engineering gate still rejects unpublished extracted parts.
        pass


def resolve_component(mpn: str) -> dict | None:
    """Find a component/2 JSON by MPN across the user library and the repo library."""
    if not mpn:
        return None
    try:
        from .component_library import ComponentLibrary, normalize_mpn
        with ComponentLibrary() as database:
            component = database.get_component(mpn, approved_only=True)
            if component is not None:
                return component
            # A draft/blocked authoritative record must not be bypassed through
            # an older filesystem projection.
            known = database.connection.execute(
                "SELECT status FROM components WHERE mpn_key=?", (normalize_mpn(mpn),)).fetchone()
            if known is not None:
                return None
    except Exception:
        pass
    cand = _LIB_DIR / f"{_safe(mpn)}.json"
    if cand.exists():
        try:
            component = json.loads(cand.read_text())
            from .component_3d_assets import ensure_component_asset
            component, changed = ensure_component_asset(component)
            if changed:
                cand.write_text(json.dumps(component))
            from .footprint_verify import placement_allowed
            allowed, _ = placement_allowed(component)
            return component if allowed else None
        except Exception:
            pass
    # Fall back to scanning immutable repository fixtures by MPN field.
    for f in _REPO_LIB.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            if d.get("component", {}).get("mpn", "") == mpn:
                from .component_3d_assets import ensure_component_asset
                d, _ = ensure_component_asset(d)
                return d
        except Exception:
            pass
    return None


def resolve_bound_component(mpn: str) -> tuple[dict, dict] | None:
    """Return (complete binding, compact instance reference), if installed."""
    if not mpn:
        return None
    from .component_binding import BoundComponentStore, binding_digest, validate_binding
    try:
        from .component_library import ComponentLibrary
        with ComponentLibrary() as database:
            binding = database.get_binding(mpn, approved_only=True)
            asset = database.asset_for(mpn, "model_3d")
            if binding is not None and asset is not None:
                cache = database.path.parent / "materialized" / "models" / \
                    f"{_safe(mpn)}-{asset['sha256'][:12]}.step"
                database.materialize_asset(asset["sha256"], cache)
                binding["model_3d"]["asset_uri"] = str(cache.resolve())
                binding["binding_digest"] = binding_digest(binding)
                validate_binding(binding, require_complete=True, verify_asset=True)
                model = copy.deepcopy(binding["model_3d"])
                reference = {
                    "schema": "design-studio.bound-component-ref/1",
                    "binding_id": binding["binding_id"],
                    "binding_digest": binding["binding_digest"],
                    "record_uri": f"{database.path}#component={_safe(mpn)}",
                    "model_3d": model,
                }
                return binding, reference
    except Exception:
        pass
    store = BoundComponentStore()
    binding = store.get(mpn, require_complete=True)
    return (binding, store.reference(binding)) if binding else None


# ── Footprint construction ────────────────────────────────────────────────────
def _default_pads(n_pins: int) -> list[dict]:
    """Generic footprint when a part has no extracted geometry (e.g. a passive
    named only by value): a row of pads at 1.27 mm pitch."""
    n = max(n_pins, 2)
    return [{"number": str(i + 1), "x_mm": round(i * 1.27, 3), "y_mm": 0.0,
             "width_mm": 0.6, "height_mm": 0.9, "shape": "rect"}
            for i in range(n)]


def _pads_for(comp2: dict | None, ref_pins: set[str]) -> tuple[list[dict], bool]:
    """Return (pads, through_hole) for a component, preferring a deterministic
    IPC land pattern for recognised IC packages (QFN/QFP/SOIC/DFN/SOT/BGA) over
    the LLM-extracted pad coordinates, which are often wrong or missing."""
    # IPC land pattern keyed by the part's REAL extracted pins — used for IC
    # packages (QFN/QFP/SOIC…) where the standard land pattern is exact and the
    # extracted pad coordinates are unreliable. The pins themselves come from the
    # datasheet symbol, so this stays "exact pins from the datasheet".
    if comp2:
        try:
            from . import ic_footprints
            gen = ic_footprints.regenerate_footprint(comp2)
            if gen and gen.get("pads"):
                return gen["pads"], False     # SMD
        except Exception:
            pass
    # Otherwise use the datasheet-extracted pads as-is (connectors etc.). NO
    # generic connector-library or package guessing: parts without a datasheet
    # are left out by the harness, so they never reach here. A passive that only
    # has a synthesised IPC pad set still resolves below.
    fp = (comp2 or {}).get("footprint", {})
    raw = fp.get("pads")
    mount = (fp.get("mount") or "smd").lower()
    th = mount in ("through", "tht", "through-hole", "thru")
    return raw or [], th


def build_dsproj(components: list[dict], netlist: list[dict],
                 base: dict | None = None, layers: int | None = None,
                 incremental_analysis: dict | None = None) -> dict:
    """Build a .dsproj document dict from a harness design. `layers` (if given)
    sets the copper stackup, e.g. 4 for a 4-layer board."""
    base = dict(base or {})
    mechanical = base.get("mechanical_contract")
    if mechanical is not None:
        _validate_mechanical_contract(mechanical)
    fixed_board = mechanical is not None

    # 1) Net table from the netlist's net names.
    net_id: dict[str, int] = {}
    net_table = []
    for a in netlist:
        n = a.get("net", "")
        if n and n not in net_id:
            net_id[n] = len(net_table)
            net_table.append({"id": net_id[n], "name": n, "class": 0})

    # pin → net per component ref
    ref_pin_net: dict[str, dict[str, str]] = {}
    for a in netlist:
        ref_pin_net.setdefault(a.get("ref", ""), {})[str(a.get("pin", ""))] = a.get("net", "")

    # 2) Build each footprint and measure its courtyard extent.
    COURTYARD = 0.5            # mm halo around a component
    items = []                # {ref, lib, h3d, pads, w, h, area, lminx, lminy}
    unresolved = []
    for i, c in enumerate(components):
        ref = c.get("ref", f"U{i+1}")
        mpn = c.get("mpn", "")
        resolved_binding = resolve_bound_component(mpn)
        binding_ref = None
        if resolved_binding:
            comp2, binding_ref = resolved_binding
        else:
            # A verified STEP binding is optional for 2D PCB placement.  Luna's
            # extracted SMT/THT land pattern is authoritative for the PCB; an
            # absent 3D model simply means FreeCAD has no component body to sync.
            comp2 = resolve_component(mpn)
        if not comp2:
            unresolved.append({"ref": ref, "mpn": mpn,
                               "reason": "no validated component/2 library record"})
            continue
        from .footprint_verify import placement_allowed
        allowed, placement_reason = placement_allowed(comp2)
        if not allowed:
            unresolved.append({"ref": ref, "mpn": mpn,
                               "reason": "footprint extraction is incomplete: "
                                         + placement_reason})
            continue
        pin_net = ref_pin_net.get(ref, {})
        raw_pads, th = _pads_for(comp2, set(pin_net.keys()))
        if not raw_pads:
            unresolved.append({"ref": ref, "mpn": mpn,
                               "reason": "validated component has no explicit land pattern"})
            continue

        pads = []
        lminx = lminy = 1e18; lmaxx = lmaxy = -1e18
        for p in raw_pads:
            num = str(p.get("number", ""))
            net = pin_net.get(num, "")
            pw = p.get("width_mm", 0.6); ph = p.get("height_mm", 0.6)
            px = p.get("x_mm", 0.0);     py = p.get("y_mm", 0.0)
            pads.append({
                "name": num, "x_mm": px, "y_mm": py, "w_mm": pw, "h_mm": ph,
                "net": net_id.get(net, -1), "th": th,
                "drill_mm": p.get("drill_mm", 0.3 if th else 0.0),
            })
            lminx = min(lminx, px - pw/2); lmaxx = max(lmaxx, px + pw/2)
            lminy = min(lminy, py - ph/2); lmaxy = max(lmaxy, py + ph/2)
        if lmaxx < lminx:               # no pads → tiny default
            lminx = lminy = -1.0; lmaxx = lmaxy = 1.0
        # Body outline (silkscreen): the EXACT body from the datasheet dims when we
        # have it, else the pad bounding box — centred on the pad centroid.
        fpbody = (comp2 or {}).get("footprint", {}).get("body") or {}
        body_w = fpbody.get("width_mm") or (lmaxx - lminx)
        body_h = fpbody.get("length_mm") or (lmaxy - lminy)
        # Extend the courtyard bbox to include the body — a part whose body is
        # bigger than its pad span (modules, connectors) must not overlap/overflow.
        bcx = (lminx + lmaxx) / 2; bcy = (lminy + lmaxy) / 2
        lminx = min(lminx, bcx - body_w / 2); lmaxx = max(lmaxx, bcx + body_w / 2)
        lminy = min(lminy, bcy - body_h / 2); lmaxy = max(lmaxy, bcy + body_h / 2)
        w = (lmaxx - lminx) + 2 * COURTYARD
        h = (lmaxy - lminy) + 2 * COURTYARD
        items.append({
            "ref": ref, "pads": pads, "w": w, "h": h, "area": w * h,
            "lminx": lminx - COURTYARD, "lminy": lminy - COURTYARD,
            "body_w": round(body_w, 3), "body_h": round(body_h, 3),
            "body_cx": round((lminx + lmaxx) / 2, 3),
            "body_cy": round((lminy + lmaxy) / 2, 3),
            "lib": (comp2 or {}).get("footprint", {}).get("name", "") or mpn,
            "h3d": (comp2 or {}).get("package_3d", {}).get("height_mm", 1.0),
            "mpn": mpn,
            "manufacturer": (comp2 or {}).get("component", {}).get("manufacturer", ""),
            "datasheet_evidence": (comp2 or {}).get("evidence", {}),
            "courtyard_pts": (comp2 or {}).get("footprint", {}).get("courtyard_pts", []),
            "bound_component": binding_ref,
            "asset_3d": (comp2 or {}).get("package_3d", {}).get("asset"),
        })

    # 3) Size the board from total component area, reserving 30% for routing/vias:
    #    target board_area = component_area / 0.70  (components ~70%, 30% free).
    #    This is the TARGET; an overlap-free packing may need a touch more area
    #    when component sizes are very uneven — never less, so routing room is
    #    guaranteed. The sub-system placer then optimises the actual placement.
    ROUTING_RESERVE = 0.30
    total_area = sum(it["area"] for it in items) or 1.0
    side = math.sqrt(total_area / (1.0 - ROUTING_RESERVE))   # target square side
    MARGIN = 2.0
    GAP = 1.0                          # routing channel between components (mm)

    # 4) Shelf-pack components (tallest first) into the target width — overlap-free
    #    and compact (dense placement keeps connected pads close → short routes).
    items.sort(key=lambda it: -it["h"])
    fps = []
    cx = MARGIN; cy = MARGIN; row_h = 0.0; max_x = max_y = 0.0
    for it in items:
        if cx > MARGIN and cx + it["w"] > side + MARGIN:
            cx = MARGIN; cy += row_h + GAP; row_h = 0.0       # next shelf row
        # origin so the component's courtyard box top-left sits at (cx, cy)
        fps.append({"ref": it["ref"], "lib": it["lib"],
                    "mpn": it["mpn"], "manufacturer": it["manufacturer"],
                    "datasheet_evidence": it["datasheet_evidence"],
                    "x_mm": round(cx - it["lminx"], 3),
                    "y_mm": round(cy - it["lminy"], 3),
                    "rot_deg": 0.0, "side": 0, "h3d_mm": it["h3d"],
                    "body_w_mm": it["body_w"], "body_h_mm": it["body_h"],
                    "body_cx_mm": it["body_cx"], "body_cy_mm": it["body_cy"],
                    "pads": it["pads"],
                    "courtyard_pts": it["courtyard_pts"],
                    "bound_component": it["bound_component"],
                    "asset_3d": it["asset_3d"]})
        if fps[-1]["bound_component"] is None:
            fps[-1].pop("bound_component")
        if fps[-1]["asset_3d"] is None:
            fps[-1].pop("asset_3d")
        cx += it["w"] + GAP
        row_h = max(row_h, it["h"])
        max_x = max(max_x, cx); max_y = max(max_y, cy + row_h)

    needed_w = round(max(side, max_x) + MARGIN, 1)
    needed_h = round(max(side, max_y) + MARGIN, 1)
    contract_w = float(base.get("board_width_mm", 0)) if fixed_board else 0.0
    contract_h = float(base.get("board_height_mm", 0)) if fixed_board else 0.0
    # Draft generation is not constrained to the enclosure's current PCB volume.
    # Grow the provisional substrate to contain every extracted courtyard; the
    # optimizer can compact it and mechanical reconciliation can resize the
    # enclosure later. This prevents components repeatedly appearing off-board.
    board_w = max(contract_w, needed_w) if fixed_board else needed_w
    board_h = max(contract_h, needed_h) if fixed_board else needed_h
    if fixed_board and (board_w <= 0 or board_h <= 0):
        raise ValueError("locked mechanical contract needs positive board dimensions")

    # A mechanical contract is a reconciliation target during this draft phase,
    # not a size/position cage. Keep useful role and edge intent from a matching
    # reference, but deliberately release its old lock and coordinates so the
    # connectivity/rotation optimizer can place the real datasheet footprint.
    # The unchanged mechanical contract remains available for the later enclosure
    # reconciliation pass.
    previous_by_ref = {fp.get("ref"): fp for fp in base.get("footprints", [])
                       if fp.get("ref")}
    for fp in fps:
        previous = previous_by_ref.get(fp["ref"])
        placement = dict((previous or {}).get("placement", {}))
        if placement:
            placement["locked"] = False
            fp["placement"] = placement

    # 5) Board document.
    doc = base
    doc.update({
        "version": 2,
        "board_width_mm": board_w,
        "board_height_mm": board_h,
        "copper_layers": layers or doc.get("copper_layers", 2),
        "net_table": net_table,
        "net_classes": doc.get("net_classes",
                               [{"id": 0, "name": "Default", "clearance_mm": 0.2,
                                 "trace_width_mm": 0.25, "via_diameter_mm": 0.6,
                                 "via_drill_mm": 0.3}]),
        "footprints": fps,
        "traces": [],
        "vias": [],
        "unresolved_components": unresolved,
        "placement_state": {
            "mode": "draft",
            "status": "awaiting_optimization",
            "routing": "deferred",
            "reason": "datasheet-derived footprints require connectivity and rotation optimization",
            "substrate_sizing": "expanded_to_component_courtyards",
            "mechanical_reconciliation": "pending" if fixed_board else "not_required",
            "mechanical_target_mm": [contract_w, contract_h] if fixed_board else None,
        },
    })
    if fixed_board and (board_w > contract_w or board_h > contract_h):
        doc["board_outline_pts"] = [[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]]

    # 6) Schematic block from the extracted symbol data (real pin names +
    #    electrical-type sides), so the schematic shows true pin functions
    #    (VCC, GP4, GND) instead of footprint pad numbers.
    try:
        sch = None
        analysis_schematic = (incremental_analysis or {}).get("schematic")
        if isinstance(analysis_schematic, dict) and analysis_schematic.get("symbols"):
            sch = copy.deepcopy(analysis_schematic)
            for symbol in sch.get("symbols", []):
                for pin in symbol.get("pins", []):
                    pin["net"] = net_id.get(pin.pop("net_name", ""), -1)
        else:
            from .schematic_symbols import build_schematic_block
            sch = build_schematic_block(components, netlist, net_id, resolve_component)
        if sch["symbols"]:
            doc["schematic"] = sch
    except Exception:
        pass
    return doc


def apply(project_path: str, components: list[dict], netlist: list[dict],
          layers: int | None = None, incremental_analysis: dict | None = None) -> dict:
    """Write the design to project_path (backing up any existing file). `layers`
    sets the copper stackup (e.g. 4 for a 4-layer board). Returns a summary dict."""
    p = Path(project_path)
    base = {}
    if p.exists() and p.stat().st_size > 4:
        try:
            base = json.loads(p.read_text())
        except Exception:
            base = {}
        # Back up non-empty existing project before replacing.
        if base.get("footprints"):
            shutil.copy2(p, str(p) + ".bak")

    # Pass the complete project through. build_dsproj replaces electronics that
    # are derived from the new component/netlist set, while retaining rule
    # profiles and the FreeCAD-owned mechanical contract/outline/keepouts.
    doc = build_dsproj(components, netlist, layers=layers, base=base,
                       incremental_analysis=incremental_analysis)
    tmp = str(p) + ".tmp"
    Path(tmp).write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, p)
    placed = sum(1 for fp in doc["footprints"] for _ in fp["pads"])
    return {"project": str(p), "footprints": len(doc["footprints"]),
            "pads": placed, "nets": len(doc["net_table"]),
            "unresolved_components": doc.get("unresolved_components", []),
            "board_mm": [doc["board_width_mm"], doc["board_height_mm"]],
            "mechanical_contract": bool(doc.get("mechanical_contract")),
            "backed_up": Path(str(p) + ".bak").exists()}


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile
    # Use a real extracted part (DRV8353 has full footprint geometry).
    drv = resolve_component(next(
        (json.loads(f.read_text())["component"]["mpn"]
         for f in _REPO_LIB.glob("*.json")
         if "DRV8353" in f.read_text()), "DRV8353MRTA"))
    mpn = drv["component"]["mpn"] if drv else "DRV8353MRTA"
    components = [{"ref": "U1", "mpn": mpn},
                  {"ref": "C1", "mpn": "0.1uF 0603"}]   # absent library record → unresolved
    netlist = [{"ref": "U1", "pin": "4", "net": "VM"},
               {"ref": "U1", "pin": "40", "net": "GND"},
               {"ref": "C1", "pin": "1", "net": "VM"},
               {"ref": "C1", "pin": "2", "net": "GND"}]
    proj = Path(tempfile.mktemp(suffix=".dsproj"))
    summary = apply(str(proj), components, netlist)
    print("apply summary:", json.dumps(summary, indent=2))
    doc = json.loads(proj.read_text())
    u1 = next(f for f in doc["footprints"] if f["ref"] == "U1")
    print(f"U1: {len(u1['pads'])} pads (real geometry: "
          f"{u1['pads'][0]['x_mm']},{u1['pads'][0]['y_mm']})")
    print("C1: deliberately unresolved (no fabricated generic footprint)")
    print(f"net VM id wired to U1.4: "
          f"{next(p['net'] for p in u1['pads'] if p['name']=='4') >= 0}")
    assert len(doc["footprints"]) == 1 and len(doc["net_table"]) == 2
    assert doc["unresolved_components"][0]["ref"] == "C1"
    print("apply_to_board OK — validated footprints placed; unresolved part reported")
