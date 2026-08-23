#!/usr/bin/env python3
"""Physics analysis MCP server — AI agents run SI/PI/thermal analysis.

User space: signal & power integrity
Tools:
  microstrip_impedance    — single-ended microstrip Z0 (Hammerstad-Jensen)
  stripline_impedance     — embedded stripline Z0 (Cohn formula)
  diff_pair_impedance     — differential pair Zdiff
  trace_resistance        — DC resistance for current capacity
  ipc_2221_current        — max current vs trace width (IPC-2221A)
  via_impedance           — via L/C/R/theta
  crosstalk_estimate      — near-end/far-end crosstalk
  skin_depth              — skin depth at frequency
  thermal_resistance      — via/trace thermal resistance
  power_budget            — full board power budget from BOM
"""

import json
import math
import sys
from pathlib import Path

_root = Path(__file__).parents[2]
_site = Path.home() / ".local/lib/python3.14/site-packages"
for p in [str(_root), str(_site)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "physics-analysis",
    instructions=(
        "Signal integrity, power integrity, and thermal analysis. "
        "Use microstrip_impedance or stripline_impedance to check trace impedance. "
        "Use ipc_2221_current for current capacity. Use power_budget for total power."
    ),
)

_MU0 = 4e-7 * math.pi     # H/m
_EPS0 = 8.854e-12          # F/m
_RHO_CU = 1.724e-8         # Ω·m (copper resistivity at 20°C)


@mcp.tool()
def microstrip_impedance(
    width_mm: float,
    height_mm: float,
    er: float = 4.4,
    copper_oz: float = 1.0,
) -> str:
    """Single-ended microstrip impedance (Hammerstad-Jensen).

    width_mm:  trace width
    height_mm: dielectric height (core/prepreg thickness)
    er:        relative permittivity (FR-4 ≈ 4.4, PTFE ≈ 2.2)
    copper_oz: copper weight (1 oz = 35 µm thick)
    Returns Z0 (Ω), effective εr, propagation delay (ps/mm).
    """
    t_mm = 0.035 * copper_oz   # copper thickness in mm
    w = width_mm
    h = height_mm

    # Width correction for finite thickness
    if t_mm > 0:
        dw = t_mm / math.pi * (1 + math.log(2 * h / t_mm))
        w_eff = w + dw
    else:
        w_eff = w

    u = w_eff / h
    # Effective permittivity
    er_eff = (er + 1) / 2 + (er - 1) / 2 * (1 + 12 / u) ** -0.5

    if u <= 1:
        z0 = 60 / math.sqrt(er_eff) * math.log(8 / u + u / 4)
    else:
        z0 = 120 * math.pi / (math.sqrt(er_eff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))

    c_light = 3e11   # mm/s
    tpd_ps_mm = math.sqrt(er_eff) / c_light * 1e12

    return json.dumps({
        "topology": "microstrip",
        "Z0_ohm": round(z0, 2),
        "er_effective": round(er_eff, 3),
        "propagation_delay_ps_per_mm": round(tpd_ps_mm, 3),
        "width_mm": width_mm,
        "height_mm": height_mm,
        "note": "50Ω target: adjust width_mm until Z0 ≈ 50Ω",
    })


@mcp.tool()
def stripline_impedance(
    width_mm: float,
    b_mm: float,
    er: float = 4.4,
    copper_oz: float = 1.0,
) -> str:
    """Centered stripline impedance (Cohn formula).

    width_mm: trace width
    b_mm:     total dielectric height (between GND planes)
    er:       relative permittivity
    copper_oz: copper weight
    """
    t_mm = 0.035 * copper_oz
    w = width_mm
    b = b_mm

    # Effective width
    x = 4 * math.exp(1) / (t_mm / b * (1 - t_mm / (2 * b))) if t_mm > 0 else 1e9
    dw = t_mm / math.pi * (1 + math.log(x)) if t_mm > 0 else 0
    we = w + dw

    z0 = 60 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi * (0.8 * we + t_mm)))

    c_light = 3e11
    tpd = math.sqrt(er) / c_light * 1e12

    return json.dumps({
        "topology": "stripline",
        "Z0_ohm": round(z0, 2),
        "propagation_delay_ps_per_mm": round(tpd, 3),
        "width_mm": width_mm,
        "b_mm": b_mm,
    })


@mcp.tool()
def diff_pair_impedance(
    width_mm: float,
    gap_mm: float,
    height_mm: float,
    er: float = 4.4,
    topology: str = "microstrip",
) -> str:
    """Differential pair impedance (IPC-2141A odd-mode method).

    width_mm: single trace width
    gap_mm:   gap between traces (edge to edge)
    height_mm: dielectric height
    topology: 'microstrip' | 'stripline'
    """
    # Calculate single-ended Z0 first
    se_data = json.loads(
        microstrip_impedance(width_mm, height_mm, er) if topology == "microstrip"
        else stripline_impedance(width_mm, height_mm * 2, er)
    )
    z0 = se_data["Z0_ohm"]

    # Odd-mode coupling factor approximation
    q = 2 * math.log(2) / math.pi * (gap_mm / height_mm)
    zdiff = 2 * z0 * (1 - 0.347 * math.exp(-2.9 * gap_mm / height_mm))

    return json.dumps({
        "topology": f"differential_{topology}",
        "Zdiff_ohm": round(zdiff, 2),
        "Z0_single_ohm": round(z0, 2),
        "width_mm": width_mm,
        "gap_mm": gap_mm,
        "note": "USB 2.0 target: Zdiff ≈ 90Ω; USB 3.0/HDMI: Zdiff ≈ 100Ω",
    })


@mcp.tool()
def trace_resistance(
    width_mm: float,
    length_mm: float,
    copper_oz: float = 1.0,
    temp_c: float = 25.0,
) -> str:
    """DC resistance of a copper trace (mΩ).

    width_mm, length_mm: trace geometry
    copper_oz: 1 oz = 35 µm thick
    temp_c: operating temperature
    """
    t_m = 0.035e-3 * copper_oz   # m
    w_m = width_mm * 1e-3
    l_m = length_mm * 1e-3
    rho = _RHO_CU * (1 + 0.00393 * (temp_c - 20))
    R = rho * l_m / (w_m * t_m)
    return json.dumps({
        "resistance_mohm": round(R * 1000, 3),
        "resistance_ohm": round(R, 6),
        "width_mm": width_mm,
        "length_mm": length_mm,
        "copper_oz": copper_oz,
        "temp_c": temp_c,
    })


@mcp.tool()
def ipc_2221_current(
    trace_width_mm: float,
    temp_rise_c: float = 10.0,
    copper_oz: float = 1.0,
    external: bool = True,
) -> str:
    """Maximum current capacity per IPC-2221A.

    temp_rise_c: allowable temperature rise above ambient
    external: True for external layer, False for internal
    """
    t_mils = 35 * copper_oz * 0.03937  # mils (1 µm = 0.03937 mils)
    w_mils = trace_width_mm * 39.37

    A = w_mils * t_mils  # cross-section in mils²
    if external:
        I = 0.048 * (temp_rise_c ** 0.44) * (A ** 0.725)
    else:
        I = 0.024 * (temp_rise_c ** 0.44) * (A ** 0.725)

    return json.dumps({
        "max_current_a": round(I, 3),
        "trace_width_mm": trace_width_mm,
        "temp_rise_c": temp_rise_c,
        "copper_oz": copper_oz,
        "layer": "external" if external else "internal",
        "note": f"For {temp_rise_c}°C rise; derate 20% for safety margin",
    })


@mcp.tool()
def via_impedance(
    drill_mm: float = 0.3,
    pad_mm: float = 0.6,
    board_thickness_mm: float = 1.6,
    er: float = 4.4,
    freq_ghz: float = 1.0,
) -> str:
    """Via parasitic L, C, R, and impedance at a given frequency."""
    # Via inductance (Wheeler, simplified)
    h = board_thickness_mm * 1e-3
    d = drill_mm * 1e-3
    L = 2e-7 * h * (math.log(4 * h / d) + 1)  # H

    # Pad capacitance (Wadell)
    d_pad = pad_mm * 1e-3
    C = _EPS0 * er * 1.41 * (d_pad ** 2 - d ** 2) / (board_thickness_mm * 1e-3)

    # DC resistance
    rho = _RHO_CU
    wall_t = 25e-6  # plating: 25 µm minimum
    R = rho * h / (math.pi * d * wall_t)

    omega = 2 * math.pi * freq_ghz * 1e9
    Xl = omega * L
    Xc = 1 / (omega * C) if C > 0 else 1e9
    Z_mag = math.sqrt((R + Xl) ** 2)   # simplified, ignore C parallel path

    return json.dumps({
        "inductance_nH": round(L * 1e9, 4),
        "capacitance_fF": round(C * 1e15, 2),
        "resistance_mohm": round(R * 1000, 3),
        "impedance_ohm_at_freq": round(Z_mag, 2),
        "freq_ghz": freq_ghz,
        "drill_mm": drill_mm,
        "pad_mm": pad_mm,
        "note": "Anti-pad needed in plane layers to reduce capacitance",
    })


@mcp.tool()
def crosstalk_estimate(
    aggressor_length_mm: float,
    separation_mm: float,
    height_mm: float = 0.2,
    rise_time_ns: float = 1.0,
    er: float = 4.4,
) -> str:
    """Near-end and far-end crosstalk estimate (simplified IPC-2141A).

    aggressor_length_mm: coupled length
    separation_mm: center-to-center trace spacing
    height_mm: dielectric height
    rise_time_ns: signal rise time
    """
    s = separation_mm
    h = height_mm
    l = aggressor_length_mm

    # Backward (NEXT) crosstalk coefficient
    Kb = 0.25 * (1 - 1 / (1 + (s / h) ** 2))
    # Forward (FEXT) crosstalk coefficient
    Kf = math.sqrt(er) / (2 * (1 + (s / h) ** 2)) * (l / (rise_time_ns * 150))

    NEXT_dB = 20 * math.log10(max(Kb, 1e-9))
    FEXT_dB = 20 * math.log10(max(abs(Kf), 1e-9))

    recommendation = "OK"
    if Kb > 0.05:
        recommendation = f"Increase spacing; NEXT {Kb*100:.1f}% > 5% limit"

    return json.dumps({
        "NEXT_coefficient": round(Kb, 4),
        "FEXT_coefficient": round(Kf, 4),
        "NEXT_dB": round(NEXT_dB, 1),
        "FEXT_dB": round(FEXT_dB, 1),
        "recommendation": recommendation,
        "note": "3W rule: keep separation ≥ 3× trace width to minimize coupling",
    })


@mcp.tool()
def skin_depth(freq_hz: float = 1e9, material: str = "copper") -> str:
    """Skin depth at a given frequency.

    material: 'copper' | 'gold' | 'silver'
    """
    rho_map = {"copper": 1.724e-8, "gold": 2.44e-8, "silver": 1.59e-8}
    rho = rho_map.get(material.lower(), _RHO_CU)
    mu = _MU0
    delta = math.sqrt(rho / (math.pi * freq_hz * mu))
    return json.dumps({
        "skin_depth_um": round(delta * 1e6, 3),
        "frequency_ghz": freq_hz / 1e9,
        "material": material,
        "note": f"Trace should be ≥ 3× skin depth ({3*delta*1e6:.1f} µm) for low loss",
    })


@mcp.tool()
def power_budget(components_path: str = "/tmp/synth_components_v3.json") -> str:
    """Estimate total board power consumption from component list.

    Uses typical supply current values from the known-pins database categories.
    """
    try:
        comps = json.loads(Path(components_path).read_text())
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

    # Typical power per category (mW)
    POWER_MAP = {
        "mcu": 200, "display": 50, "audio": 80, "audio_codec": 80,
        "battery_charger": 20, "ldo": 10, "usb_esd": 1,
        "spi_flash": 15, "encoder": 1, "crystal": 5,
        "passive": 0, "connector": 0, "default": 10,
    }

    breakdown = []
    total_mw = 0.0
    for comp in comps:
        c = comp.get("component", {})
        cat = c.get("category", "default")
        mpn = c.get("mpn", "?")
        ref = comp.get("_ref", "?")
        pwr = POWER_MAP.get(cat, POWER_MAP["default"])
        breakdown.append({"ref": ref, "mpn": mpn, "category": cat, "typical_mw": pwr})
        total_mw += pwr

    # Battery life estimate (assuming 3.7V 500mAh LiPo)
    capacity_mah = 500
    voltage_v = 3.7
    capacity_mwh = capacity_mah * voltage_v
    battery_h = capacity_mwh / total_mw if total_mw > 0 else 0

    return json.dumps({
        "total_power_mw": round(total_mw, 1),
        "total_power_w": round(total_mw / 1000, 3),
        "estimated_current_ma": round(total_mw / 3.3, 1),
        "battery_life_h_500mah": round(battery_h, 1),
        "breakdown": breakdown,
        "note": "Values are estimates; check datasheets for active/sleep modes",
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
