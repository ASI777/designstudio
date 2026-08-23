"""Schematic-symbol builder — generates the .dsproj `schematic` block from the
datasheet-extracted component/2 `symbol` data (real pin names + electrical_type),
instead of letting the app re-derive symbols from PCB footprint pad numbers.

This makes schematic pins show their true function (VCC, GP4, SDA…) on the
correct side, driven by the datasheet's `electrical_type` (#3) rather than a
name regex. Pins are placed by convention: power-in top, ground bottom, outputs
right, inputs left, bidirectional/passive split left/right.

Output matches ProjectModel's SchSymbol/SchPin JSON:
  schematic.symbols[].pins[] = {num, name, etype, side(0=L,1=R,2=T,3=B), order, net}
"""
from __future__ import annotations
import math

# PinSide ints mirror ProjectModel.h: Left=0, Right=1, Top=2, Bottom=3
_LEFT, _RIGHT, _TOP, _BOTTOM = 0, 1, 2, 3


def _is_ground(name: str) -> bool:
    u = (name or "").upper()
    return any(g in u for g in ("GND", "VSS", "AGND", "PGND", "VSSQ", "DGND")) or u in ("EP", "PAD")


def _side_for(etype: str, name: str):
    """Assign a side from the datasheet electrical_type (primary, #3), with the
    pin name disambiguating power-in into supply (top) vs ground (bottom).
    Returns a fixed side, or None to let the caller alternate left/right."""
    if _is_ground(name):
        return _BOTTOM
    et = (etype or "").lower()
    if et == "power_in":   return _TOP
    if et == "power_out":  return _RIGHT
    if et == "output":     return _RIGHT
    if et == "input":      return _LEFT
    # bidir / passive / clock / nc → balance across left/right
    return None


def build_schematic_block(components: list[dict], netlist: list[dict],
                          net_id: dict[str, int], resolve) -> dict:
    """components: [{ref,mpn}], netlist: [{ref,pin,net}], net_id: net→id,
    resolve: mpn → component/2 dict. Returns {"symbols": [...], "wires": []}."""
    # (ref, pin-key) → net name, keyed by both number and name forms.
    rpn: dict[tuple, str] = {}
    for a in netlist:
        rpn[(a.get("ref", ""), str(a.get("pin", "")).upper())] = a.get("net", "")

    n = len(components)
    cols = max(1, int(math.ceil(math.sqrt(n))))
    PITCH = 45.0
    symbols = []
    for i, c in enumerate(components):
        ref = c.get("ref", f"U{i+1}")
        mpn = c.get("mpn", "")
        comp2 = resolve(mpn) or {}
        sympins = (comp2.get("symbol", {}) or {}).get("pins", [])
        if not sympins:
            continue                                   # no extracted symbol → app fallback
        sx = (i % cols) * PITCH + 20.0
        sy = (i // cols) * PITCH + 20.0
        order = {_LEFT: 0, _RIGHT: 0, _TOP: 0, _BOTTOM: 0}
        toggle = 0
        pins = []
        for p in sympins:
            num = str(p.get("number", ""))
            name = p.get("name", num) or num
            et = p.get("electrical_type", "") or "passive"
            side = _side_for(et, name)
            if side is None:                           # alternate L/R for signals
                side = _LEFT if toggle % 2 == 0 else _RIGHT
                toggle += 1
            o = order[side]; order[side] += 1
            net = (rpn.get((ref, num.upper())) or rpn.get((ref, name.upper())) or "")
            pins.append({"num": num, "name": name, "etype": et,
                         "side": side, "order": o, "net": net_id.get(net, -1)})
        symbols.append({
            "ref": ref,
            "lib": (comp2.get("footprint", {}) or {}).get("name", "") or mpn,
            "x_mm": round(sx, 2), "y_mm": round(sy, 2),
            "rot_deg": 0.0, "unit": 1, "pins": pins,
        })
    return {"symbols": symbols, "wires": []}


if __name__ == "__main__":
    import json, glob, tempfile
    # Build a schematic symbol from a real extracted component/2 and show pin sides.
    from swarm.runtime_paths import component_fixture_dir
    f = next(g for g in glob.glob(str(component_fixture_dir() / "*.json"))
             if "tps7a20" in g.lower() or "drv8353" in g.lower())
    d = json.load(open(f))
    mpn = d["component"]["mpn"]
    block = build_schematic_block(
        [{"ref": "U1", "mpn": mpn}],
        [{"ref": "U1", "pin": "GND", "net": "GND"}],
        {"GND": 0}, lambda m: d)
    sym = block["symbols"][0]
    sidename = {0: "L", 1: "R", 2: "T", 3: "B"}
    print(f"{mpn} schematic symbol — pins by side (electrical_type driven):")
    for p in sym["pins"]:
        print(f"  {p['name']:8s} {p['etype']:10s} side={sidename[p['side']]} "
              f"net={'GND' if p['net']>=0 else '-'}")
    print("\nschematic_symbols OK — real pin names, electrical-type sides")
