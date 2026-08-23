#!/usr/bin/env python3
"""live_design_agent.py — Progressive PCB netlist assignment agent.

Reads the existing .dsproj, assigns pad→net connections phase by phase,
and writes the file after each phase. The app's QFileSystemWatcher detects
each write and refreshes the PCB canvas, giving live visual feedback.

Usage:
  python3 live_design_agent.py <project.dsproj>
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

# ── Component → pin_number → net_name assignments ────────────────────────────
# Each entry maps: component ref → {pad_number_string: net_name}
# Derived from component-library fixtures + circuit schematic knowledge.

# Power/ground pin name → net name (for generic matching across components)
_POWER_MAP: dict[str, str] = {
    # Ground
    "GND": "GND", "AGND": "GND", "PGND": "GND", "VSS": "GND",
    "VSSQ": "GND", "VSSA": "GND", "EP": "GND",
    # Digital 3.3 V
    "VCC": "VCC", "AVCC": "VCC", "DVCC": "VCC",
    # 3.3 V output rail
    "3V3": "3V3",
    # DDR4
    "VDDQ": "VDDQ", "VPP": "VPP",
}

# Component-specific assignments (pad number → net name).
# Built from component-library pin tables + design intent for FOC board.
_COMP_NETS: dict[str, dict[str, str]] = {

    # ── U2: Monolithic Power MPM3833C — 12 V → 3.3 V step-down ──────────────
    # IN (pins 13,14) = 12 V bus; OUT (pins 8,9,10) = 3.3 V; SW = internal
    "U2": {
        "1":  "GND",   # AGND
        "8":  "3V3",   # OUT
        "9":  "3V3",   # OUT
        "10": "3V3",   # OUT
        "13": "IN_12V",  # VIN
        "14": "IN_12V",  # VIN
        "16": "GND",   # PGND (exposed pad)
    },

    # ── U3: Monolithic Power MPM3610GQV — 12 V → VDD_CORE (1.0 V) step-down ─
    # VCC output (pin 2) feeds the PolarFire SoC core.
    "U3": {
        "3":  "GND",        # AGND
        "7":  "VDD_CORE",   # OUT
        "8":  "VDD_CORE",   # OUT
        "9":  "VDD_CORE",   # OUT
        "12": "GND",        # PGND
        "13": "GND",        # PGND
        "14": "GND",        # PGND
        "16": "IN_12V",     # VIN
    },

    # ── U4: TI TPS7A20 — 3.3 V → 1.8 V LDO (logic I/O rail) ────────────────
    # 5-pad X2SON: OUT, GND, EN, IN, EP
    "U4": {
        "1": "V1P8",   # OUT
        "2": "GND",    # GND
        "3": "3V3",    # EN — tie high to enable
        "4": "3V3",    # IN (3.3 V input to the LDO)
        "5": "GND",    # EP (exposed pad)
    },

    # ── U5: Micron MT40A512M8 DDR4 SDRAM (BGA-78) ────────────────────────────
    # Power-only assignment — data/address signals connect to U1 (PolarFire)
    # which already has its own assignments. Data lanes left unconnected here
    # to avoid conflicting with the partial U1 netlist.
    "U5": {
        # VDD pins (1.2 V DRAM core)
        "A1": "VDDQ", "B9": "VDDQ", "F1": "VDDQ", "F9": "VDDQ",
        "H1": "VDDQ", "H9": "VDDQ", "K9": "VDDQ", "M1": "VDDQ",
        # VDDQ pins (also 1.2 V)
        "B2": "VDDQ", "B8": "VDDQ", "C1": "VDDQ", "C9": "VDDQ",
        "E2": "VDDQ", "E8": "VDDQ",
        # VPP (2.5 V activation voltage)
        "B1": "VPP", "M9": "VPP",
        # VSS / VSSQ ground
        "A2": "GND", "A8": "GND", "A9": "GND",
        "D1": "GND", "D9": "GND",
        "E1": "GND", "E9": "GND",
        "G1": "GND", "G9": "GND",
        "J9": "GND", "K1": "GND", "N9": "GND",
    },

    # ── U6: Micron MT25QL128 SPI NOR Flash (SOIC-8) ──────────────────────────
    "U6": {
        "1": "BOOT_SS",   # S# chip-select
        "4": "GND",       # VSS
        "7": "BOOT_SCK",  # C (clock) — driven by U1 SPI boot master
        "8": "VCC",       # VCC (3.3 V)
    },

    # ── U7: SiTime SiT9120 LVDS oscillator (6-pad SMD) ───────────────────────
    # CLK output goes to PolarFire XCVR reference clock input.
    "U7": {
        "1": "VCC",   # OE — tie to VCC to always-enable
        "3": "GND",   # GND
        "6": "VDD18", # VDD — 1.8 V supply for LVDS oscillator
    },

    # ── U8: TI DRV8353 three-phase smart gate driver (QFN-41) ────────────────
    "U8": {
        "4":  "VM",      # VM — motor bus (48 V / 24 V)
        "5":  "VM",      # VDRAIN — high-side drain sense (same as VM rail here)
        "27": "VCC",     # DVDD output bypass — decouple to VCC
        "29": "GND",     # AGND
        "30": "PWM_AH",  # INHA — PWM A high from FPGA
        "31": "PWM_AL",  # INLA — PWM A low  from FPGA
        "40": "GND",     # GND
        "41": "GND",     # EP  — exposed pad to GND
    },

    # ── U9: TI AM26LV32E quad differential receiver (TSSOP-16) ───────────────
    # Receives encoder quadrature / index signals from the encoder connector.
    "U9": {
        "8":  "GND",  # GND
        "16": "VCC",  # VCC (3.3 V)
    },

    # ── U10: TI DP83822IF Ethernet PHY (QFN-32+EP) ───────────────────────────
    "U10": {
        "11": "TD_M",   # TD_M — Ethernet TX−
        "12": "TD_P",   # TD_P — Ethernet TX+
        "14": "3V3",    # AVD  — 3.3 V analog supply
        "21": "V1P8",   # VDDIO — 1.8 V I/O supply (IF variant)
        "EP": "GND",    # exposed pad
    },

    # ── Q1: Infineon BSC0902NSI PMOS 25 V (TDSON-8) — high-side switch ───────
    # Source (pads 1–3) tied to motor bus; gate driven by DRV8353 GHA output.
    "Q1": {
        "1": "VM",  # S1 — source to motor bus
        "2": "VM",  # S2
        "3": "VM",  # S3
    },

    # ── Q2: Infineon BSC040N08NS5 NMOS 80 V (TDSON-8) — low-side switch ──────
    # Source (pads 1–3) to GND; gate driven by DRV8353 GLA output.
    "Q2": {
        "1": "GND",  # S1 — source to GND
        "2": "GND",  # S2
        "3": "GND",  # S3
    },
}

# ── Net name → id lookup built at runtime from the project ───────────────────

def _build_net_lookup(net_table: list[dict]) -> dict[str, int]:
    return {n["name"]: n["id"] for n in net_table}


def _apply_u1_from_circuit_state(fps: list[dict], net_lookup: dict[str, int],
                                  circuit_state_path: str) -> int:
    """Apply the 116 known U1 pin→net assignments from the saved circuit-state."""
    cs_path = Path(circuit_state_path)
    if not cs_path.exists():
        print(f"  [warn] circuit-state not found at {cs_path}")
        return 0

    with open(cs_path) as f:
        cs = json.load(f)

    u1_pins: dict[str, str] = {}
    for comp in cs.get("components", []):
        if comp.get("ref") == "U1":
            for pin in comp.get("pins", []):
                net_name = pin.get("net")
                if net_name and net_name in net_lookup:
                    u1_pins[pin["pin"]] = net_name
            break

    u1_fp = next((fp for fp in fps if fp.get("ref") == "U1"), None)
    if not u1_fp:
        print("  [warn] U1 footprint not found in project")
        return 0

    assigned = 0
    for pad in u1_fp.get("pads", []):
        name = pad.get("name", "")
        if name in u1_pins:
            new_id = net_lookup[u1_pins[name]]
            if pad["net"] != new_id:
                pad["net"] = new_id
                assigned += 1
    return assigned


def _apply_component(ref: str, fps: list[dict],
                     net_lookup: dict[str, int],
                     pad_net_map: dict[str, str]) -> int:
    """Apply a pad→net map to one component's footprint. Returns # assigned."""
    fp = next((f for f in fps if f.get("ref") == ref), None)
    if not fp:
        return 0
    assigned = 0
    for pad in fp.get("pads", []):
        name = pad.get("name", "")
        if name in pad_net_map:
            net_name = pad_net_map[name]
            net_id = net_lookup.get(net_name, -1)
            if net_id >= 0 and pad["net"] != net_id:
                pad["net"] = net_id
                assigned += 1
    return assigned


def _write(project_path: str, data: dict) -> None:
    with open(project_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))


def _count_assigned(fps: list[dict]) -> tuple[int, int]:
    total = assigned = 0
    for fp in fps:
        for pad in fp.get("pads", []):
            total += 1
            if pad.get("net", -1) >= 0:
                assigned += 1
    return assigned, total


# ── Main ─────────────────────────────────────────────────────────────────────

def main(project_path: str) -> None:
    print(f"[live-design] Loading {project_path}")
    with open(project_path) as f:
        data = json.load(f)

    fps        = data.get("footprints", [])
    net_table  = data.get("net_table", [])
    net_lookup = _build_net_lookup(net_table)

    # Locate the SD2 circuit-state for U1 pin assignments
    from swarm.runtime_paths import component_fixture_dir, component_library_dir
    cs_candidates = [
        component_library_dir() / "single_axis_FOC_Field_Oriented_Control_SD2-circuit-state.json",
        component_fixture_dir() / "single_axis_FOC_Field_Oriented_Control_SD2-circuit-state.json",
    ]
    cs_path = next((str(p) for p in cs_candidates if p.exists()), "")

    a, t = _count_assigned(fps)
    print(f"[live-design] Start: {a}/{t} pads assigned")
    print()

    # ── Phase 1: U1 power rail connections (from circuit-state) ──────────────
    print("━━━━ Phase 1/4 — U1 (PolarFire SoC) power rails ━━━━")
    n = _apply_u1_from_circuit_state(fps, net_lookup, cs_path)
    a, t = _count_assigned(fps)
    print(f"  Assigned {n} U1 pads  →  total {a}/{t}")
    _write(project_path, data)
    time.sleep(0.6)

    # ── Phase 2: Power converters + passives ──────────────────────────────────
    print()
    print("━━━━ Phase 2/4 — Power supply components ━━━━")
    for ref in ("U2", "U3", "U4"):
        n = _apply_component(ref, fps, net_lookup, _COMP_NETS.get(ref, {}))
        a, t = _count_assigned(fps)
        print(f"  {ref}: assigned {n} pads  →  total {a}/{t}")
        _write(project_path, data)
        time.sleep(0.4)

    # ── Phase 3: DDR4, flash, oscillator, PHY, receivers ─────────────────────
    print()
    print("━━━━ Phase 3/4 — Memory, clock, and I/O components ━━━━")
    for ref in ("U5", "U6", "U7", "U9", "U10"):
        n = _apply_component(ref, fps, net_lookup, _COMP_NETS.get(ref, {}))
        a, t = _count_assigned(fps)
        print(f"  {ref}: assigned {n} pads  →  total {a}/{t}")
        _write(project_path, data)
        time.sleep(0.4)

    # ── Phase 4: Gate driver + MOSFETs ───────────────────────────────────────
    print()
    print("━━━━ Phase 4/4 — Motor drive path (U8 + Q1/Q2) ━━━━")
    for ref in ("U8", "Q1", "Q2"):
        n = _apply_component(ref, fps, net_lookup, _COMP_NETS.get(ref, {}))
        a, t = _count_assigned(fps)
        print(f"  {ref}: assigned {n} pads  →  total {a}/{t}")
        _write(project_path, data)
        time.sleep(0.4)

    # ── Summary ───────────────────────────────────────────────────────────────
    a, t = _count_assigned(fps)
    unconnected = t - a
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Netlist assignment complete")
    print(f"  Connected:   {a}/{t} pads  ({100*a//t}%)")
    print(f"  Unconnected: {unconnected} pads  (DDR4/SPI data signals — need schematic)")
    print()
    print("  Next steps:")
    print("  1. Click Tools → Auto Route to route all connected nets")
    print("  2. Click Tools → Run DRC to check clearances")
    print("  3. Use 'Advise Circuit' for AI signal-integrity review")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: live_design_agent.py <project.dsproj>")
        sys.exit(1)
    project_path = sys.argv[1]
    if not os.path.exists(project_path):
        print(f"error: file not found: {project_path}")
        sys.exit(1)
    main(project_path)
