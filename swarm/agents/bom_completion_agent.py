#!/usr/bin/env python3
"""bom_completion_agent.py — BOM gap analysis + DigiKey sourcing + footprint insertion.

Identifies every passive and connector missing from the PCB, queries DigiKey
for the best in-stock part for each spec, generates the footprint geometry,
assigns the correct nets, and places the component adjacent to the IC it serves.
Writes the .dsproj progressively so the canvas updates live.

Usage:
  python3 bom_completion_agent.py <project.dsproj>

DigiKey credentials (optional — falls back to well-known MPNs if absent):
  DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET in ~/.config/designstudio/env
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Component spec ────────────────────────────────────────────────────────────

@dataclass
class CompSpec:
    category:     str          # "cap_100n", "cap_10u", "res_10r", etc.
    count:        int
    value:        str          # "100nF", "10Ω"
    package:      str          # "0402", "0603", "1206", "TH_2PIN", "TH_6PIN"
    search_query: str          # DigiKey keyword search
    fallback_mpn: str          # used when DigiKey is unavailable
    net1_name:    str          # pad 1 net
    net2_name:    str          # pad 2 net
    near_ic:      str          # ref of the IC this decouples
    offset_x:     float = 8.0  # mm from IC centre (placement start)
    offset_y:     float = -8.0
    description:  str = ""

# ── Pad geometry (IPC-7351 Nominal) ──────────────────────────────────────────

_PAD_GEOM = {
    "0402": [(-0.4875, 0, 0.56, 0.62), ( 0.4875, 0, 0.56, 0.62)],
    "0603": [(-0.775,  0, 0.90, 0.90), ( 0.775,  0, 0.90, 0.90)],
    "1206": [(-1.475,  0, 1.60, 1.80), ( 1.475,  0, 1.60, 1.80)],
    # Through-hole 2-pin power connector, 2.54 mm pitch
    "TH_2PIN": [(-1.27, 0, 1.8, 1.8, True, 1.0),
                ( 1.27, 0, 1.8, 1.8, True, 1.0)],
    # 2×3 ARM JTAG-10 / generic SWD/JTAG header, 2.54 mm pitch
    "TH_6PIN": [
        (-1.27, -2.54, 1.8, 1.8, True, 1.0),  # 1 TMS
        ( 1.27, -2.54, 1.8, 1.8, True, 1.0),  # 2 VCC
        (-1.27,  0.00, 1.8, 1.8, True, 1.0),  # 3 TCK
        ( 1.27,  0.00, 1.8, 1.8, True, 1.0),  # 4 TDI (NC here)
        (-1.27,  2.54, 1.8, 1.8, True, 1.0),  # 5 TDO (NC here)
        ( 1.27,  2.54, 1.8, 1.8, True, 1.0),  # 6 GND
    ],
    # 3-pin motor output connector (7.62mm pitch, screw terminal)
    "TH_3PIN": [
        (-7.62, 0, 3.5, 3.5, True, 1.5),
        ( 0.00, 0, 3.5, 3.5, True, 1.5),
        ( 7.62, 0, 3.5, 3.5, True, 1.5),
    ],
    # 5-pin encoder connector (2.54mm pitch, single row)
    "TH_5PIN": [
        (-5.08, 0, 1.8, 1.8, True, 1.0),  # 1 VCC (encoder power)
        (-2.54, 0, 1.8, 1.8, True, 1.0),  # 2 ENC_A+
        ( 0.00, 0, 1.8, 1.8, True, 1.0),  # 3 ENC_B+
        ( 2.54, 0, 1.8, 1.8, True, 1.0),  # 4 ENC_Z+ (index)
        ( 5.08, 0, 1.8, 1.8, True, 1.0),  # 5 GND
    ],
}

# ── Full BOM gap definition ───────────────────────────────────────────────────
# Every passive and connector missing from the FOC drive design.

BOM_GAPS: list[CompSpec] = [

    # ── U1 PolarFire SoC — core / aux power decoupling ────────────────────────
    CompSpec("cap_100n", 4,  "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDD_CORE", "GND", "U1", 8.0, -8.0,
             "U1 VDD_CORE bypass (0.9 V core)"),

    CompSpec("cap_10u",  2, "10µF/6.3V/X5R", "0603",
             "Murata GRM188R60J106KE47D 10uF 0603",
             "GRM188R60J106KE47D",
             "VDD_CORE", "GND", "U1", 8.0, -5.5,
             "U1 VDD_CORE bulk"),

    CompSpec("cap_100n", 4, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "V1P8", "GND", "U1", 8.0, -3.0,
             "U1 V1P8 (1.8 V I/O) bypass"),

    CompSpec("cap_100n", 4, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDD", "GND", "U1", 8.0, -0.5,
             "U1 VDD (I/O bank) bypass"),

    CompSpec("cap_100n", 4, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDD18", "GND", "U1", 8.0, 2.0,
             "U1 VDD18 bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDD25", "GND", "U1", 8.0, 4.5,
             "U1 VDD25 bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDDA", "GND", "U1", 8.0, 7.0,
             "U1 VDDA analog bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDDAUX1", "GND", "U1", 11.0, -8.0,
             "U1 VDDAUX1 bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDDAUX2", "GND", "U1", 11.0, -5.5,
             "U1 VDDAUX2 bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDDAUX4", "GND", "U1", 11.0, -3.0,
             "U1 VDDAUX4 bypass"),

    # ── DDR4 (U5) — VDDQ + VPP decoupling ─────────────────────────────────────
    CompSpec("cap_100n", 8, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDDQ", "GND", "U5", 7.5, -5.0,
             "U5 DDR4 VDDQ bypass (1.2 V)"),

    CompSpec("cap_10u",  2, "10µF/6.3V/X5R", "0603",
             "Murata GRM188R60J106KE47D 10uF 0603",
             "GRM188R60J106KE47D",
             "VDDQ", "GND", "U5", 7.5, -2.0,
             "U5 DDR4 VDDQ bulk"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VPP", "GND", "U5", 7.5,  1.0,
             "U5 DDR4 VPP bypass (2.5 V activation)"),

    # ── DRV8353 (U8) — VM bulk + DVDD bypass + bootstrap caps ─────────────────
    CompSpec("cap_100n", 4, "100nF/100V/X7R", "0402",
             "Murata GRM155R72A104KE14D 100nF 100V 0402",
             "GRM155R72A104KE14D",
             "VM", "GND", "U8", 7.5, -5.0,
             "U8 DRV8353 VM (motor bus) bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VCC", "GND", "U8", 7.5, -2.5,
             "U8 DRV8353 DVDD bypass (3.3 V)"),

    # Bootstrap caps: BST pins connect between CBOOT+ (VM via diode) and SW node.
    # We only have VCC and GND nets — use VCC/GND as the nearest approximation.
    CompSpec("cap_100n", 3, "100nF/100V/X7R", "0402",
             "Murata GRM155R72A104KE14D 100nF 100V 0402",
             "GRM155R72A104KE14D",
             "VM", "GND", "U8", 7.5, 0.0,
             "DRV8353 bootstrap caps (CBST phase A/B/C — connect to BST/SW pins when phase nets added)"),

    # ── Ethernet PHY (U10) — power bypass ─────────────────────────────────────
    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "3V3", "GND", "U10", 6.5, -4.0,
             "U10 DP83822 3.3 V analog (AVD) bypass"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "V1P8", "GND", "U10", 6.5, -1.5,
             "U10 DP83822 VDDIO (1.8 V) bypass"),

    # ── SoC oscillator (U7) ────────────────────────────────────────────────────
    CompSpec("cap_100n", 1, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "V1P8", "GND", "U7", 3.5, -2.0,
             "U7 SiT9120 oscillator VDD bypass"),

    # ── Power supply sections ──────────────────────────────────────────────────
    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "3V3", "GND", "U2", 5.0, -4.5,
             "U2 MPM3833C output filter bypass"),

    CompSpec("cap_10u",  1, "10µF/16V/X5R", "0603",
             "Murata GRM188R61C106MAALD 10uF 16V 0603",
             "GRM188R61C106MAALD",
             "3V3", "GND", "U2", 5.0, -2.0,
             "U2 3.3 V output bulk cap"),

    CompSpec("cap_100n", 2, "100nF/16V/X7R", "0402",
             "Murata GRM155R71C104KA88D 100nF 0402",
             "GRM155R71C104KA88D",
             "VDD_CORE", "GND", "U3", 5.0, -4.5,
             "U3 MPM3610 output filter bypass"),

    CompSpec("cap_10u",  1, "10µF/16V/X5R", "0603",
             "Murata GRM188R61C106MAALD 10uF 16V 0603",
             "GRM188R61C106MAALD",
             "VDD_CORE", "GND", "U3", 5.0, -2.0,
             "U3 VDD_CORE output bulk cap"),

    # ── Motor bus (VM) bulk capacitors ────────────────────────────────────────
    CompSpec("cap_100u", 2, "100µF/50V", "1206",
             "TDK CGA5L1X5R1H476M250AC 47uF 50V 1206",
             "CGA5L1X5R1H476M250AC",
             "VM", "GND", "U8", -10.0, 0.0,
             "VM motor bus bulk capacitor (50 V, near power input)"),

    # ── Connectors ────────────────────────────────────────────────────────────
    CompSpec("conn_pwr", 1, "2-pin screw terminal 5.08mm", "TH_2PIN",
             "Phoenix Contact 1935161 2-pin 5.08mm screw terminal",
             "1935161",
             "IN_12V", "GND", "U2", -18.0, 0.0,
             "J1 — 12V power input connector (place near board edge)"),

    CompSpec("conn_jtag", 1, "6-pin 2×3 JTAG/SWD header 2.54mm", "TH_6PIN",
             "Amphenol 87606-306LF 6-pin 2x3 2.54mm header",
             "87606-306LF",
             "JTAG_TMS", "GND", "U1", -18.0, -4.0,
             "J2 — JTAG/SWD debug header"),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _grid_positions(anchor_x: float, anchor_y: float, n: int,
                    offset_x: float, offset_y: float,
                    pitch: float = 1.5, cols: int = 5) -> list[tuple[float, float]]:
    """Generate n grid positions starting at (anchor_x+offset_x, anchor_y+offset_y)."""
    positions = []
    for i in range(n):
        row = i // cols
        col = i % cols
        x = anchor_x + offset_x + col * pitch
        y = anchor_y + offset_y + row * pitch
        x = max(2.0, min(98.0, x))
        y = max(2.0, min(78.0, y))
        positions.append((x, y))
    return positions


def _make_footprint(ref: str, lib: str, x: float, y: float,
                    pad_nets: list[int],  # net_id per pad
                    package: str,
                    value: str, mpn: str) -> dict:
    """Build a .dsproj footprint dict from pad geometry + net assignments."""
    geom = _PAD_GEOM[package]
    pads = []
    for i, g in enumerate(geom):
        px, py, pw, ph = g[0], g[1], g[2], g[3]
        is_th   = g[4] if len(g) > 4 else False
        drill   = g[5] if len(g) > 5 else 0.0
        net_id  = pad_nets[i] if i < len(pad_nets) else -1
        pads.append({
            "name":        str(i + 1),
            "x_mm":        round(px, 4),
            "y_mm":        round(py, 4),
            "w_mm":        round(pw, 4),
            "h_mm":        round(ph, 4),
            "net":         net_id,
            "th":          is_th,
            "drill_mm":    drill,
            "pkg_delay_mm": 0,
        })
    return {
        "ref":       ref,
        "lib":       lib,
        "x_mm":      round(x, 3),
        "y_mm":      round(y, 3),
        "rot_deg":   0.0,
        "side":      0,
        "h3d_mm":    0.5,
        "value":     value,
        "mpn":       mpn,
        "pads":      pads,
    }


def _next_ref(fps: list[dict], prefix: str) -> str:
    import re
    maxn = 0
    for fp in fps:
        m = re.match(rf'^{prefix}(\d+)$', fp.get("ref", ""))
        if m:
            maxn = max(maxn, int(m.group(1)))
    return f"{prefix}{maxn + 1}"


# ── DigiKey sourcing ──────────────────────────────────────────────────────────

_part_cache: dict[str, tuple[str, str, float | None, int]] = {}  # query → (mpn, desc, price, stock)


def _source_part(spec: CompSpec, agg) -> tuple[str, str]:
    """Return (mpn, short_desc). Uses cache; falls back to spec.fallback_mpn."""
    key = spec.category
    if key in _part_cache:
        mpn, desc, price, stock = _part_cache[key]
        return mpn, desc

    if agg is None:
        return spec.fallback_mpn, spec.description

    try:
        results = agg.search(spec.search_query, limit=6)
        best = agg.best(results) if results else None
        if best and best.mpn:
            mpn   = best.mpn
            desc  = best.description[:60] if best.description else spec.description
            price = best.price_inr
            stock = best.stock or 0
            _part_cache[key] = (mpn, desc, price, stock)
            stock_str = f"{stock:,}" if stock else "?"
            price_str = f"₹{price:.2f}" if price else "?"
            print(f"  DigiKey → {mpn}  stock={stock_str}  {price_str}/unit")
            return mpn, desc
    except Exception as e:
        print(f"  DigiKey search failed ({e}) — using fallback MPN")

    _part_cache[key] = (spec.fallback_mpn, spec.description, None, 0)
    return spec.fallback_mpn, spec.description


# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path: str) -> None:
    print(f"[bom-completion] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps        = data.setdefault("footprints", [])
    net_table  = data.get("net_table", [])
    net_lookup = {n["name"]: n["id"] for n in net_table}

    # Build a map of IC ref → centroid
    ic_pos: dict[str, tuple[float, float]] = {
        fp["ref"]: (fp["x_mm"], fp["y_mm"]) for fp in fps
    }

    # Try to connect to DigiKey
    try:
        from vendors.aggregator import Aggregator
        agg = Aggregator()
        vendors = agg.vendors_online
        if vendors:
            print(f"[bom-completion] Vendors online: {', '.join(vendors)}")
        else:
            print("[bom-completion] No vendor credentials configured — using fallback MPNs")
            agg = None
    except Exception as e:
        print(f"[bom-completion] Vendor init failed ({e}) — using fallback MPNs")
        agg = None

    # Statistics
    total_added = 0
    bom_lines: list[tuple[str, str, str, str]] = []  # (ref, mpn, value, description)

    print()
    print(f"[bom-completion] Processing {len(BOM_GAPS)} component categories…")
    print()

    # Group specs by phase label for live-update batching.
    # Connectors are a separate phase — exclude them from IC-proximity phases
    # so they don't get placed twice.
    def _is_passive(s: CompSpec) -> bool:
        return "conn" not in s.category

    phase_groups = {
        "U1 SoC decoupling":     [s for s in BOM_GAPS if s.near_ic == "U1"              and _is_passive(s)],
        "DDR4 decoupling":       [s for s in BOM_GAPS if s.near_ic == "U5"               and _is_passive(s)],
        "Gate driver & MOSFETs": [s for s in BOM_GAPS if s.near_ic in ("U8","Q1","Q2")  and _is_passive(s)],
        "Ethernet PHY & OSC":    [s for s in BOM_GAPS if s.near_ic in ("U10","U7")       and _is_passive(s)],
        "Power supply sections": [s for s in BOM_GAPS if s.near_ic in ("U2","U3","U4")   and _is_passive(s)],
        "Connectors":            [s for s in BOM_GAPS if not _is_passive(s)],
    }

    for phase_name, specs in phase_groups.items():
        if not specs:
            continue
        print(f"━━━━ {phase_name} ━━━━")
        phase_added = 0

        for spec in specs:
            ic_x, ic_y = ic_pos.get(spec.near_ic, (50.0, 40.0))
            positions   = _grid_positions(ic_x, ic_y, spec.count,
                                          spec.offset_x, spec.offset_y)

            # Source the MPN from DigiKey (or fallback)
            print(f"  Sourcing {spec.count}× {spec.value} {spec.package} ({spec.description})")
            mpn, desc = _source_part(spec, agg)

            net1_id = net_lookup.get(spec.net1_name, -1)
            net2_id = net_lookup.get(spec.net2_name, -1)

            # Determine lib name and ref prefix
            ref_prefix = "C" if "cap" in spec.category else (
                         "R" if "res" in spec.category else "J")
            lib_name = {
                "0402": f"C_{spec.value.replace('/', '_')}_{spec.package}",
                "0603": f"C_{spec.value.replace('/', '_')}_{spec.package}",
                "1206": f"C_{spec.value.replace('/', '_')}_{spec.package}",
            }.get(spec.package, f"Connector_{spec.package}")
            if ref_prefix == "R":
                lib_name = f"R_{spec.value.replace('/', '_')}_{spec.package}"
            if ref_prefix == "J":
                lib_name = f"Connector_{spec.package}"

            for x, y in positions:
                ref = _next_ref(fps, ref_prefix)

                # Multi-pad connectors need net per pin
                if spec.package == "TH_6PIN":
                    # JTAG: TMS, VCC, TCK, TDI(NC), TDO(NC), GND
                    pad_nets = [
                        net_lookup.get("JTAG_TMS", -1),
                        net_lookup.get("VCC",      -1),
                        net_lookup.get("JTAG_TCK", -1),
                        -1,
                        -1,
                        net_lookup.get("GND",      16),
                    ]
                elif spec.package == "TH_3PIN":
                    pad_nets = [-1, -1, net_lookup.get("GND", 16)]  # phase nets TBD
                elif spec.package == "TH_5PIN":
                    pad_nets = [
                        net_lookup.get("VCC", -1),
                        -1, -1, -1,  # ENC signals TBD
                        net_lookup.get("GND", 16),
                    ]
                else:
                    pad_nets = [net1_id, net2_id]

                fp = _make_footprint(ref, lib_name, x, y,
                                     pad_nets, spec.package,
                                     spec.value, mpn)
                fps.append(fp)
                bom_lines.append((ref, mpn, spec.value, spec.description))
                phase_added += 1
                total_added += 1

        # Write after each phase so the canvas updates live
        with open(project_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))

        print(f"  → Added {phase_added} components (total so far: {total_added})")
        time.sleep(0.5)

    # ── BOM summary ───────────────────────────────────────────────────────────
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  BOM Completion Summary — {total_added} components added")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    # Group by MPN for BOM
    from collections import Counter
    mpn_count: Counter = Counter()
    mpn_desc:  dict[str, str] = {}
    for ref, mpn, val, desc in bom_lines:
        mpn_count[mpn] += 1
        mpn_desc[mpn] = f"{val} — {desc}"

    print(f"  {'QTY':>4}  {'MPN':<30}  {'VALUE / DESCRIPTION'}")
    print(f"  {'---':>4}  {'---':<30}  {'---'}")
    for mpn, qty in sorted(mpn_count.items(), key=lambda kv: -kv[1]):
        print(f"  {qty:>4}×  {mpn:<30}  {mpn_desc[mpn][:55]}")

    # Save BOM CSV alongside the project
    bom_path = Path(project_path).parent / (Path(project_path).stem + "-bom.csv")
    with open(bom_path, "w") as f:
        f.write("Ref,MPN,Value,Description\n")
        for ref, mpn, val, desc in bom_lines:
            f.write(f"{ref},{mpn},{val},{desc}\n")
    print()
    print(f"  BOM CSV saved → {bom_path}")
    print()
    print("  Next: run 'Advise Circuit' for SI review of the populated board")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: bom_completion_agent.py <project.dsproj>")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"error: not found: {path}")
        sys.exit(1)
    main(path)
