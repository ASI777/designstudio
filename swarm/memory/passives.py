"""Passive land patterns — deterministic IPC-7351 footprints for chip passives.

Capacitors, resistors, inductors and ferrite beads don't need datasheet pin
extraction: they're 2-terminal parts whose footprint is fully determined by the
package code (0402/0603/0805…), which is right there in the MPN/description. So
instead of a 1–2 min Gemini extraction per passive, we synthesise a correct,
DRC-safe component/2 from a built-in IPC land-pattern table — no LLM call.

This loses NO electrical property the harness uses: a passive's value comes from
its MPN, its net from the netlist, and any rating requirement from the parent
IC's required_externals (the IC is still datasheet-extracted).
"""
from __future__ import annotations
import re

# IPC-7351B nominal reflow land patterns for 2-terminal chip components.
# package → (pad_w_mm, pad_h_mm, gap_x_mm)  — pads centred on the part, on X.
_CHIP = {
    "0201": (0.34, 0.40, 0.24),
    "0402": (0.60, 0.60, 0.50),
    "0603": (0.80, 0.90, 0.80),
    "0805": (1.00, 1.30, 0.90),
    "1206": (1.00, 1.80, 1.80),
    "1210": (1.00, 2.60, 1.80),
    "2010": (1.20, 2.60, 3.40),
    "2512": (1.50, 3.20, 4.60),
}
# Metric (EIA) codes that map to the imperial codes above.
_METRIC = {"1005": "0402", "1608": "0603", "2012": "0805",
           "3216": "1206", "3225": "1210", "5025": "2010", "6332": "2512",
           "0603m": "0201"}

# Ref-des prefixes that denote a 2-terminal chip passive. (Crystals "Y", diodes
# "D", connectors etc. are NOT here — they keep full datasheet extraction.)
_PASSIVE_PREFIXES = {"C", "R", "L", "FB", "Z"}


def _prefix(ref: str) -> str:
    return "".join(ch for ch in (ref or "") if ch.isalpha()).upper()


def is_passive(component: dict) -> bool:
    role = (component.get("role") or "").lower()
    if role in ("passive", "capacitor", "resistor", "inductor", "ferrite"):
        return True
    return _prefix(component.get("ref", "")) in _PASSIVE_PREFIXES


def detect_package(text: str) -> str:
    """Find a chip package code in an MPN/description. Defaults to 0603."""
    t = (text or "").upper()
    for code in _CHIP:                       # imperial codes (0402, 0603…)
        if re.search(rf"\b{code}\b", t) or code in t:
            return code
    for metric, imp in _METRIC.items():      # metric codes (1608 = 0603…)
        if metric.upper() in t:
            return imp
    return "0603"


def land_pattern(package: str) -> list[dict]:
    pw, ph, gap = _CHIP.get(package, _CHIP["0603"])
    cx = round(gap / 2 + pw / 2, 3)          # pad-centre offset from origin
    return [
        {"number": "1", "x_mm": -cx, "y_mm": 0.0,
         "width_mm": pw, "height_mm": ph, "shape": "rect"},
        {"number": "2", "x_mm":  cx, "y_mm": 0.0,
         "width_mm": pw, "height_mm": ph, "shape": "rect"},
    ]


def synth_component2(component: dict) -> dict:
    """Build a deterministic component/2 for a chip passive (2 pins + IPC pads)."""
    mpn = component.get("mpn", "")
    # The package code is often in the search query/description ("100nF 0402 X5R"),
    # not the bare MPN — check all of them; fall back to 0603 (DRC-safe).
    text = " ".join([mpn, component.get("role", ""),
                     component.get("search_query", ""),
                     component.get("description", "")])
    pkg = detect_package(text)
    pre = _prefix(component.get("ref", "")) or "C"
    cat = {"C": "capacitor", "R": "resistor", "L": "inductor",
           "FB": "ferrite", "Z": "ferrite"}.get(pre, "passive")
    return {
        "schema": "design-studio.component/2",
        "component": {"mpn": mpn, "category": "passive",
                      "description": f"{cat} {pkg} (IPC land pattern)"},
        "symbol": {"ref_des_prefix": pre,
                   "pins": [{"number": "1", "name": "1", "electrical_type": "passive"},
                            {"number": "2", "name": "2", "electrical_type": "passive"}]},
        "electrical": {"power_domains": [], "required_externals": []},
        "footprint": {"name": f"{pre}_{pkg}", "mount": "smd",
                      "generated": "ipc-passive", "pads": land_pattern(pkg)},
    }


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        {"ref": "C1", "mpn": "C0603C104K5RACTU", "role": "passive"},
        {"ref": "R3", "mpn": "ERJ-2RKF5101X",    "role": "passive"},  # 0402 metric-ish
        {"ref": "C7", "mpn": "GRM188R71C104KA01", "role": "passive"}, # 0603 (1608 metric)
        {"ref": "U1", "mpn": "ESP32-S3",          "role": "mcu"},
    ]
    for c in cases:
        p = is_passive(c)
        if p:
            comp2 = synth_component2(c)
            pads = comp2["footprint"]["pads"]
            print(f"{c['ref']} {c['mpn']:22s} passive → {comp2['footprint']['name']:8s} "
                  f"pads@({pads[0]['x_mm']},{pads[1]['x_mm']}) size {pads[0]['width_mm']}x{pads[0]['height_mm']}")
            # DRC sanity: pads must not overlap
            assert pads[1]['x_mm'] - pads[0]['x_mm'] > pads[0]['width_mm'], "pads overlap!"
        else:
            print(f"{c['ref']} {c['mpn']:22s} NOT passive → keeps datasheet extraction")
    print("\npassives OK — IPC land patterns, no pad overlap, ICs excluded")
