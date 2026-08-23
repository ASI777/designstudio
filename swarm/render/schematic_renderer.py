"""Schematic renderer — netlist + component list → professional schematic PNG.

Draws each component as a labeled IC box with pins, color-coded nets as wires,
and net labels. Output is a high-res PNG suitable for review.
"""

import math
import textwrap
from dataclasses import dataclass, field
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import matplotlib.patheffects as pe


# ── Net color palette ────────────────────────────────────────────────────────
_NET_COLORS = {
    "GND":      "#1a1a2e",
    "AGND":     "#16213e",
    "VCC_3V3":  "#e94560",
    "VCC_5V":   "#f5a623",
    "VBUS":     "#f5a623",
    "VBAT":     "#27ae60",
    "I2C_SDA":  "#2980b9",
    "I2C_SCL":  "#1abc9c",
    "I2S_DIN":  "#8e44ad",
    "I2S_BCK":  "#9b59b6",
    "I2S_LRCK": "#6c3483",
    "USB_DP":   "#e67e22",
    "USB_DM":   "#d35400",
    "SPI_":     "#16a085",
    "AUDIO_":   "#c0392b",
    "NRST":     "#7f8c8d",
    "SWD":      "#95a5a6",
}

_DEFAULT_COLORS = [
    "#3498db", "#2ecc71", "#e74c3c", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#16a085", "#8e44ad",
    "#c0392b", "#27ae60", "#2980b9", "#d35400", "#7f8c8d",
]


def _net_color(net_name: str, palette: dict) -> str:
    for prefix, color in _NET_COLORS.items():
        if net_name.upper().startswith(prefix):
            return color
    if net_name not in palette:
        idx = len(palette) % len(_DEFAULT_COLORS)
        palette[net_name] = _DEFAULT_COLORS[idx]
    return palette[net_name]


@dataclass
class ComponentBox:
    ref: str
    mpn: str
    description: str
    pins: list[dict]          # [{"name": str, "type": str, "net": str|None}]
    x: float = 0.0
    y: float = 0.0
    width: float = 3.0
    height: float = 0.0       # computed


def _classify_pin_side(pin_type: str, pin_name: str) -> str:
    """Left = inputs/power. Right = outputs/IO."""
    name = pin_name.upper()
    typ  = pin_type.lower()
    if typ in ("power_in",) or name in ("VCC", "VDD", "VIN", "GND", "AGND", "VBUS", "VBAT"):
        return "left"
    if typ in ("output",) or any(k in name for k in ("OUT", "TX", "MISO", "SDO")):
        return "right"
    return "right"


def render_schematic(
    components: list[dict],
    nets: dict[str, list[dict]],
    output_path: str,
    board_name: str = "Portable Synthesizer",
) -> str:
    """Render schematic PNG.

    components: list of component/2 dicts
    nets: {net_name: [{from_ref, from_pin, to_ref, to_pin}, ...]}
    output_path: where to save the PNG
    Returns output_path.
    """

    # ── Build component boxes ────────────────────────────────────────────────
    boxes: dict[str, ComponentBox] = {}
    net_by_pin: dict[tuple, str] = {}   # (ref, pin_name) → net_name

    for net_name, conns in nets.items():
        for c in conns:
            net_by_pin[(c["from_ref"], c["from_pin"])] = net_name
            net_by_pin[(c["to_ref"],   c["to_pin"])]   = net_name

    ref_counter: dict[str, int] = {}
    for comp in components:
        c    = comp.get("component", {})
        mpn  = c.get("mpn", "?")
        desc = c.get("description", "")
        cat  = c.get("category", "other")
        # Use pre-assigned _ref if available (from connection agent / known-pins flow)
        ref = comp.get("_ref") or ""
        if not ref:
            prefix = {"mcu": "U", "display": "U", "audio": "U", "passive": "C",
                      "connector": "J", "transistor": "Q", "diode": "D"}.get(cat, "U")
            ref_counter[prefix] = ref_counter.get(prefix, 0) + 1
            ref = f"{prefix}{ref_counter[prefix]}"

        raw_pins = (comp.get("symbol") or {}).get("pins") or []
        pins = []
        for p in raw_pins:
            pname = p.get("name", p.get("number", "?"))
            ptype = p.get("type", "io")
            net   = net_by_pin.get((ref, pname)) or net_by_pin.get((ref, p.get("number", "")))
            pins.append({"name": pname, "type": ptype, "net": net,
                         "side": _classify_pin_side(ptype, pname)})

        boxes[ref] = ComponentBox(ref=ref, mpn=mpn, description=desc, pins=pins)

    # ── Layout: grid placement ───────────────────────────────────────────────
    COL_W  = 5.5
    ROW_H  = 0.35
    PIN_H  = 0.28
    PAD    = 0.6

    refs = list(boxes.keys())
    cols = math.ceil(math.sqrt(len(refs)))

    for i, ref in enumerate(refs):
        box = boxes[ref]
        col = i % cols
        row = i // cols
        left  = [p for p in box.pins if p["side"] == "left"]
        right = [p for p in box.pins if p["side"] == "right"]
        n_pins = max(len(left), len(right), 1)
        box.height = max(n_pins * PIN_H + PAD, 1.2)
        box.width  = COL_W
        box.x = col * (COL_W + 1.5)
        box.y = -(row * (box.height + 1.2))

    # Canvas size
    max_x = max(b.x + b.width for b in boxes.values()) + 2
    min_y = min(b.y - b.height for b in boxes.values()) - 1.5
    fig_w = max(max_x, 20)
    fig_h = max(-min_y + 3, 14)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=120)
    ax.set_xlim(-0.5, max_x)
    ax.set_ylim(min_y, 3)
    ax.axis("off")
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    palette: dict[str, str] = {}
    pin_coords: dict[tuple, tuple] = {}   # (ref, pin_name) → (x, y)

    # ── Draw component boxes ─────────────────────────────────────────────────
    for ref, box in boxes.items():
        left  = [p for p in box.pins if p["side"] == "left"]
        right = [p for p in box.pins if p["side"] == "right"]
        n_pins = max(len(left), len(right), 1)

        # Box
        rect = mpatches.FancyBboxPatch(
            (box.x, box.y - box.height), box.width, box.height,
            boxstyle="round,pad=0.05",
            linewidth=1.5, edgecolor="#30363d", facecolor="#161b22",
        )
        ax.add_patch(rect)

        # Header bar
        header = mpatches.FancyBboxPatch(
            (box.x, box.y - 0.45), box.width, 0.45,
            boxstyle="round,pad=0.03",
            linewidth=0, facecolor="#21262d",
        )
        ax.add_patch(header)

        ax.text(box.x + box.width / 2, box.y - 0.13, ref,
                ha="center", va="center", fontsize=9, fontweight="bold",
                color="#58a6ff", fontfamily="monospace")
        mpn_short = box.mpn[:22] + ("…" if len(box.mpn) > 22 else "")
        ax.text(box.x + box.width / 2, box.y - 0.35, mpn_short,
                ha="center", va="center", fontsize=6.5, color="#8b949e",
                fontfamily="monospace")

        # Left pins
        pin_area_top = box.y - 0.5
        pin_step = (box.height - 0.55) / max(len(left), 1)
        for i, p in enumerate(left):
            py = pin_area_top - i * pin_step - pin_step / 2
            px = box.x
            color = _net_color(p["net"], palette) if p["net"] else "#484f58"
            # Stub line
            ax.plot([px - 0.4, px], [py, py], color=color, linewidth=1.2, solid_capstyle="round")
            # Circle at connection point
            ax.plot(px - 0.4, py, "o", color=color, markersize=3)
            ax.text(px + 0.12, py, p["name"],
                    ha="left", va="center", fontsize=6, color="#c9d1d9",
                    fontfamily="monospace")
            pin_coords[(ref, p["name"])] = (px - 0.4, py)

        # Right pins
        pin_step = (box.height - 0.55) / max(len(right), 1)
        for i, p in enumerate(right):
            py = pin_area_top - i * pin_step - pin_step / 2
            px = box.x + box.width
            color = _net_color(p["net"], palette) if p["net"] else "#484f58"
            ax.plot([px, px + 0.4], [py, py], color=color, linewidth=1.2, solid_capstyle="round")
            ax.plot(px + 0.4, py, "o", color=color, markersize=3)
            ax.text(px - 0.12, py, p["name"],
                    ha="right", va="center", fontsize=6, color="#c9d1d9",
                    fontfamily="monospace")
            pin_coords[(ref, p["name"])] = (px + 0.4, py)

    # ── Draw nets (wires between pins) ───────────────────────────────────────
    drawn_nets: set[str] = set()
    for net_name, conns in nets.items():
        color = _net_color(net_name, palette)
        points = []
        for c in conns:
            k1 = (c["from_ref"], c["from_pin"])
            k2 = (c["to_ref"],   c["to_pin"])
            if k1 in pin_coords: points.append(pin_coords[k1])
            if k2 in pin_coords: points.append(pin_coords[k2])

        if not points:
            continue

        # Draw a bus line through all connection points
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2

        for p in points:
            ax.annotate("", xy=(cx, cy), xytext=(p[0], p[1]),
                        arrowprops=dict(
                            arrowstyle="-",
                            color=color,
                            lw=1.0,
                            connectionstyle="arc3,rad=0.1",
                        ))

        # Net label at midpoint
        if net_name not in drawn_nets:
            ax.text(cx, cy, net_name,
                    ha="center", va="bottom", fontsize=5.5,
                    color=color, fontfamily="monospace",
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="#0d1117",
                              edgecolor=color, linewidth=0.5, alpha=0.85))
            drawn_nets.add(net_name)

    # ── Legend ───────────────────────────────────────────────────────────────
    legend_x, legend_y = -0.3, 2.6
    ax.text(legend_x, legend_y, f"SCHEMATIC — {board_name}",
            fontsize=11, fontweight="bold", color="#f0f6fc",
            fontfamily="monospace", va="top")
    ax.text(legend_x, legend_y - 0.35, f"{len(boxes)} components  ·  {len(nets)} nets  ·  4-layer PCB",
            fontsize=7, color="#8b949e", fontfamily="monospace", va="top")

    lx = legend_x
    ly = legend_y - 0.75
    for net_name, color in sorted(palette.items()):
        ax.plot([lx, lx + 0.35], [ly, ly], color=color, linewidth=2)
        ax.text(lx + 0.45, ly, net_name, fontsize=5.5, color="#8b949e",
                fontfamily="monospace", va="center")
        lx += 2.2
        if lx > max_x - 2:
            lx = legend_x
            ly -= 0.28

    plt.tight_layout(pad=0.3)
    fig.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path
