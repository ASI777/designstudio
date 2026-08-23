"""PCB layout renderer — netlist + component list → routed PCB PNG.

Draws a 4-layer board with:
  L1 (top copper)   — red traces + SMD components
  L2 (GND plane)    — solid dark fill
  L3 (power plane)  — yellow power zones
  L4 (bottom copper)— blue traces
  Vias              — gold circles

Output is a high-res PNG showing component placement, routed traces,
silkscreen labels, and board outline.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle
import numpy as np


# ── Layer colors (PCB stackup) ───────────────────────────────────────────────
L1_TRACE  = "#ff4444"    # Top copper
L4_TRACE  = "#4488ff"    # Bottom copper
L2_FILL   = "#1a2a1a"    # GND plane
L3_FILL   = "#2a2a1a"    # Power plane
VIA_COLOR = "#ffd700"    # Via barrel
SILK      = "#f0f0f0"    # Silkscreen
BOARD_BG  = "#1a2a1a"    # PCB substrate (FR-4 green tinted dark)
BOARD_FG  = "#0d1a0d"
EDGE_CUT  = "#ffff00"    # Board outline
PAD_SMD   = "#c8a040"    # Copper pad
MASK_COLOR = "#0a3d0a"   # Solder mask (dark green)


@dataclass
class Footprint:
    ref: str
    mpn: str
    category: str
    x: float
    y: float
    w: float
    h: float
    rotation: float = 0.0
    pins: list[dict] = field(default_factory=list)   # [{name, x_offset, y_offset}]
    layer: str = "top"   # "top" | "bottom"


# ── Component footprint templates ─────────────────────────────────────────────
_FOOTPRINT_SIZES = {
    "mcu":          (8.0, 8.0),
    "display":      (12.0, 6.0),
    "audio":        (4.5, 4.5),
    "audio_codec":  (4.5, 4.5),
    "power_reg":    (2.0, 2.8),
    "ldo":          (2.0, 2.8),
    "battery_charger": (2.0, 2.5),
    "usb_esd":      (1.6, 1.0),
    "connector":    (9.0, 3.5),
    "spi_flash":    (5.0, 4.0),
    "encoder":      (3.0, 3.0),
    "passive":      (0.8, 0.5),
    "crystal":      (2.0, 1.2),
    "diode":        (1.2, 0.8),
    "default":      (3.0, 2.5),
}

_CATEGORY_COLORS = {
    "mcu":           "#1e3a5f",
    "display":       "#1a3a2a",
    "audio":         "#3a1a3a",
    "audio_codec":   "#3a1a3a",
    "power_reg":     "#3a2a1a",
    "ldo":           "#3a2a1a",
    "battery_charger": "#1a3a3a",
    "usb_esd":       "#2a1a1a",
    "connector":     "#2a2a3a",
    "spi_flash":     "#1e2a3a",
    "encoder":       "#2a3a1a",
    "passive":       "#2a2a2a",
    "crystal":       "#3a3a1a",
    "default":       "#252525",
}


def _net_trace_color(net_name: str) -> tuple[str, str]:
    """Returns (layer, color)."""
    n = net_name.upper()
    if "GND" in n:         return "L2", "#333333"
    if "VCC" in n or "VDD" in n or "VBUS" in n or "VBAT" in n:
        return "L3", "#c8a000"
    if "USB" in n:         return "L1", "#ff6600"
    if "I2C" in n:         return "L1", "#44aaff"
    if "I2S" in n:         return "L4", "#aa44ff"
    if "SPI" in n:         return "L4", "#44ffaa"
    if "AUDIO" in n:       return "L1", "#ff4488"
    if "SWD" in n:         return "L4", "#888888"
    return "L1", "#ff4444"


def _grid_layout(footprints: list[Footprint], board_w: float, board_h: float):
    """Arrange footprints in a logical grid, MCU central."""
    # Sort: MCU first, then by size descending
    mcu = [f for f in footprints if f.category == "mcu"]
    large = [f for f in footprints if f.category in ("display", "connector", "spi_flash", "encoder")]
    small = [f for f in footprints if f not in mcu and f not in large]

    ordered = mcu + large + small
    margin = 3.0
    cx, cy = board_w / 2, board_h / 2

    positions = [
        (cx, cy),                              # MCU center
        (margin + 6, board_h - margin - 3),    # display top-left
        (board_w - margin - 5, board_h - margin - 2),  # connector top-right
        (cx + 8, cy + 3),                      # flash right of MCU
        (margin + 2, cy - 2),                  # encoder area
        (margin + 6, cy - 2),
        (cx - 2, margin + 3),                  # passives bottom
        (cx + 4, margin + 3),
        (board_w - margin - 2, cy - 2),        # regulators right
        (board_w - margin - 2, cy + 3),
    ]

    for i, fp in enumerate(ordered):
        if i < len(positions):
            fp.x, fp.y = positions[i]
        else:
            col = (i - len(positions)) % 4
            row = (i - len(positions)) // 4
            fp.x = margin + col * 7
            fp.y = margin + row * 5


def _draw_pad(ax, x, y, w=0.6, h=0.4, angle=0):
    pad = mpatches.FancyBboxPatch(
        (x - w/2, y - h/2), w, h,
        boxstyle="round,pad=0.05",
        linewidth=0.5, edgecolor="#a07020", facecolor=PAD_SMD, zorder=5,
    )
    ax.add_patch(pad)


def _draw_via(ax, x, y, r=0.4):
    outer = Circle((x, y), r, color=VIA_COLOR, zorder=6)
    inner = Circle((x, y), r * 0.5, color="#111", zorder=7)
    ax.add_patch(outer)
    ax.add_patch(inner)


def _route_trace(ax, x1, y1, x2, y2, color, lw=0.8, layer="L1"):
    """Route a 45-degree-bend trace between two points."""
    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) < 0.01 or abs(dy) < 0.01:
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=lw,
                solid_capstyle="round", zorder=4 if layer == "L1" else 3, alpha=0.85)
    else:
        # 45-degree jog
        if abs(dx) < abs(dy):
            mid_x, mid_y = x2, y1 + math.copysign(abs(dx), dy)
        else:
            mid_x, mid_y = x1 + math.copysign(abs(dy), dx), y2
        ax.plot([x1, mid_x, x2], [y1, mid_y, y2], color=color, linewidth=lw,
                solid_capstyle="round", solid_joinstyle="round",
                zorder=4 if layer == "L1" else 3, alpha=0.85)


def render_pcb(
    components: list[dict],
    nets: dict[str, list[dict]],
    output_path: str,
    board_name: str = "Portable Synthesizer",
    board_w_mm: float = 85.0,
    board_h_mm: float = 60.0,
) -> str:
    """Render PCB layout PNG.

    Returns output_path.
    """

    # ── Build footprints ─────────────────────────────────────────────────────
    footprints: dict[str, Footprint] = {}
    ref_counter: dict[str, int] = {}

    for comp in components:
        c    = comp.get("component", {})
        mpn  = c.get("mpn", "?")
        cat  = c.get("category", "other")
        desc = c.get("description", "")

        # Use pre-assigned _ref if available
        ref = comp.get("_ref") or ""
        if not ref:
            if "cap" in desc.lower() or cat == "passive":
                prefix = "C"
            elif cat in ("connector",):
                prefix = "J"
            elif cat in ("crystal",):
                prefix = "Y"
            elif cat in ("diode", "usb_esd"):
                prefix = "D"
            else:
                prefix = "U"
            ref_counter[prefix] = ref_counter.get(prefix, 0) + 1
            ref = f"{prefix}{ref_counter[prefix]}"

        size_key = cat if cat in _FOOTPRINT_SIZES else "default"
        w, h = _FOOTPRINT_SIZES[size_key]

        raw_pins = (comp.get("symbol") or {}).get("pins") or []
        pins = []
        if raw_pins:
            n = len(raw_pins)
            for i, p in enumerate(raw_pins):
                angle = 2 * math.pi * i / n
                pins.append({
                    "name": p.get("name", str(i)),
                    "x_off": (w/2 + 0.3) * math.cos(angle),
                    "y_off": (h/2 + 0.3) * math.sin(angle),
                })

        footprints[ref] = Footprint(
            ref=ref, mpn=mpn, category=cat,
            x=0, y=0, w=w, h=h,
            pins=pins,
        )

    fps = list(footprints.values())
    _grid_layout(fps, board_w_mm, board_h_mm)

    # ── Pin world coordinates ─────────────────────────────────────────────────
    pin_world: dict[tuple, tuple] = {}
    for fp in fps:
        for p in fp.pins:
            pin_world[(fp.ref, p["name"])] = (fp.x + p["x_off"], fp.y + p["y_off"])

    # ── Figure setup ──────────────────────────────────────────────────────────
    scale = 9.0 / board_w_mm   # scale to ~9 inch figure
    fig_w = board_w_mm * scale + 2
    fig_h = board_h_mm * scale + 2

    fig, ax = plt.subplots(figsize=(fig_w * 1.4, fig_h * 1.4), dpi=150)
    ax.set_xlim(-2, board_w_mm + 2)
    ax.set_ylim(-2, board_h_mm + 2)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("#0a0a0a")
    ax.set_facecolor(BOARD_BG)

    # ── Board outline ─────────────────────────────────────────────────────────
    board_rect = mpatches.FancyBboxPatch(
        (0, 0), board_w_mm, board_h_mm,
        boxstyle="round,pad=0.5",
        linewidth=2.5, edgecolor=EDGE_CUT, facecolor=MASK_COLOR, zorder=0,
    )
    ax.add_patch(board_rect)

    # L2 GND plane fill (subtle grid)
    for gx in range(0, int(board_w_mm), 3):
        ax.axvline(gx, color="#112211", linewidth=0.2, alpha=0.3, zorder=1)
    for gy in range(0, int(board_h_mm), 3):
        ax.axhline(gy, color="#112211", linewidth=0.2, alpha=0.3, zorder=1)

    # ── Draw components ───────────────────────────────────────────────────────
    for fp in fps:
        cat_color = _CATEGORY_COLORS.get(fp.category, _CATEGORY_COLORS["default"])

        # Component body
        comp_rect = mpatches.FancyBboxPatch(
            (fp.x - fp.w/2, fp.y - fp.h/2), fp.w, fp.h,
            boxstyle="round,pad=0.15",
            linewidth=1.2, edgecolor="#aaaaaa", facecolor=cat_color, zorder=3,
        )
        ax.add_patch(comp_rect)

        # Pin 1 marker
        ax.plot(fp.x - fp.w/2 + 0.4, fp.y + fp.h/2 - 0.4,
                ".", color=SILK, markersize=3, zorder=5)

        # Silkscreen: ref + short MPN
        ax.text(fp.x, fp.y + 0.1, fp.ref,
                ha="center", va="center", fontsize=max(4.5, min(6.5, fp.w * 0.7)),
                fontweight="bold", color=SILK, fontfamily="monospace", zorder=6)
        mpn_short = fp.mpn[:12] + ("…" if len(fp.mpn) > 12 else "")
        ax.text(fp.x, fp.y - 0.55, mpn_short,
                ha="center", va="center", fontsize=max(3.0, min(4.5, fp.w * 0.5)),
                color="#aaaaaa", fontfamily="monospace", zorder=6)

        # Draw pads
        if fp.pins:
            n = len(fp.pins)
            for i, p in enumerate(fp.pins):
                px = fp.x + p["x_off"]
                py = fp.y + p["y_off"]
                _draw_pad(ax, px, py, w=0.7, h=0.45)
        else:
            # Generic pads along edges
            for side_x in [fp.x - fp.w/2, fp.x + fp.w/2]:
                for py in [fp.y - fp.h/4, fp.y, fp.y + fp.h/4]:
                    _draw_pad(ax, side_x, py)

    # ── Route traces from netlist ─────────────────────────────────────────────
    for net_name, conns in nets.items():
        layer, color = _net_trace_color(net_name)
        lw = 1.5 if "GND" in net_name.upper() or "VCC" in net_name.upper() else 0.9

        for c in conns:
            k1 = (c.get("from_ref"), c.get("from_pin"))
            k2 = (c.get("to_ref"),   c.get("to_pin"))
            p1 = pin_world.get(k1)
            p2 = pin_world.get(k2)

            if p1 and p2 and p1 != p2:
                _route_trace(ax, p1[0], p1[1], p2[0], p2[1],
                             color=color, lw=lw, layer=layer)
                # Add via if crossing layers (simplified: add via at midpoint for cross-layer nets)
                if layer in ("L3", "L4"):
                    mx = (p1[0] + p2[0]) / 2
                    my = (p1[1] + p2[1]) / 2
                    _draw_via(ax, mx, my, r=0.35)

    # ── Net labels at trace midpoints ─────────────────────────────────────────
    labeled: set[str] = set()
    for net_name, conns in nets.items():
        if not conns or net_name in labeled:
            continue
        _, color = _net_trace_color(net_name)
        c = conns[0]
        k1 = (c.get("from_ref"), c.get("from_pin"))
        k2 = (c.get("to_ref"),   c.get("to_pin"))
        p1 = pin_world.get(k1)
        p2 = pin_world.get(k2)
        if p1 and p2:
            mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            ax.text(mx, my + 0.5, net_name,
                    ha="center", va="bottom", fontsize=4,
                    color=color, fontfamily="monospace",
                    bbox=dict(boxstyle="round,pad=0.1", facecolor="#0a0a0a",
                              edgecolor=color, linewidth=0.4, alpha=0.8),
                    zorder=8)
            labeled.add(net_name)

    # ── Layer legend ──────────────────────────────────────────────────────────
    layers = [("L1 Top", L1_TRACE), ("L2 GND", "#555555"),
              ("L3 PWR", "#c8a000"), ("L4 Bot", L4_TRACE), ("Via", VIA_COLOR)]
    lx, ly = 0.5, board_h_mm + 1.0
    for lname, lcolor in layers:
        ax.plot([lx, lx + 1.5], [ly, ly], color=lcolor, linewidth=2.5)
        ax.text(lx + 1.7, ly, lname, fontsize=5.5, color="#cccccc",
                fontfamily="monospace", va="center")
        lx += 5.5

    # ── Title block ───────────────────────────────────────────────────────────
    ax.text(board_w_mm / 2, board_h_mm + 1.6, f"PCB LAYOUT — {board_name}",
            ha="center", va="bottom", fontsize=9, fontweight="bold",
            color="#f0f6fc", fontfamily="monospace")
    ax.text(board_w_mm / 2, -1.2,
            f"{board_w_mm}mm × {board_h_mm}mm  ·  4-layer  ·  {len(fps)} components  ·  {len(nets)} nets",
            ha="center", va="top", fontsize=6.5, color="#8b949e", fontfamily="monospace")

    # ── Mounting holes ────────────────────────────────────────────────────────
    for hx, hy in [(2.5, 2.5), (board_w_mm-2.5, 2.5),
                   (2.5, board_h_mm-2.5), (board_w_mm-2.5, board_h_mm-2.5)]:
        outer = Circle((hx, hy), 1.5, color="#0a0a0a", linewidth=1,
                        edgecolor=EDGE_CUT, zorder=9)
        ax.add_patch(outer)

    plt.tight_layout(pad=0.2)
    fig.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path
