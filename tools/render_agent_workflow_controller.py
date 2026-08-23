#!/usr/bin/env python3
"""Render portable SVG review views from the finalized controller project."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "acceptance" / "agent-workflow-controller" / "generated"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def world(footprint: dict, pad: dict) -> tuple[float, float]:
    angle = math.radians(float(footprint.get("rot_deg", 0)))
    x, y = float(pad.get("x_mm", 0)), float(pad.get("y_mm", 0))
    if int(footprint.get("side", 0)) == 1:
        y = -y
    return (float(footprint["x_mm"]) + x * math.cos(angle) - y * math.sin(angle),
            float(footprint["y_mm"]) + x * math.sin(angle) + y * math.cos(angle))


def pcb_svg(board: dict) -> str:
    width, height = float(board["board_width_mm"]), float(board["board_height_mm"])
    layers = {0: "#e74c3c", 1: "#d9b44a", 2: "#58a6ff", 3: "#9b59b6"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-3 -3 {width+6} {height+6}" '
             f'width="{width*8:.0f}" height="{height*8:.0f}">',
             '<rect x="-3" y="-3" width="100%" height="100%" fill="#0d1117"/>',
             '<g stroke-linecap="round" stroke-linejoin="round">']
    outline = " ".join(f"{x},{y}" for x, y in board["board_outline_pts"])
    parts.append(f'<polygon points="{outline}" fill="#14251d" stroke="#f0f6fc" stroke-width="0.35"/>')
    for zone in board.get("copper_zones", []):
        points = " ".join(f"{x},{y}" for x, y in zone["pts"])
        parts.append(f'<polygon points="{points}" fill="{layers.get(int(zone["layer"]), "#777")}" '
                     'fill-opacity="0.10" stroke-dasharray="1,1" stroke-width="0.18"/>')
    for trace in board.get("traces", []):
        color = layers.get(int(trace.get("layer", 0)), "#aaa")
        opacity = .18 if trace.get("pour", False) else .82
        parts.append(f'<line x1="{trace["ax_mm"]}" y1="{trace["ay_mm"]}" '
                     f'x2="{trace["bx_mm"]}" y2="{trace["by_mm"]}" '
                     f'stroke="{color}" stroke-opacity="{opacity}" stroke-width="{trace["w_mm"]}"/>')
    for footprint in board.get("footprints", []):
        x, y = float(footprint["x_mm"]), float(footprint["y_mm"])
        rotation = float(footprint.get("rot_deg", 0))
        body_w = max(float(footprint.get("body_w_mm", 1)), .4)
        body_h = max(float(footprint.get("body_h_mm", 1)), .4)
        parts.append(f'<g transform="translate({x} {y}) rotate({rotation})">')
        parts.append(f'<rect x="{-body_w/2}" y="{-body_h/2}" width="{body_w}" height="{body_h}" '
                     'fill="#30363d" fill-opacity="0.6" stroke="#8b949e" stroke-width="0.16"/>')
        for pad in footprint.get("pads", []):
            px, py = float(pad.get("x_mm", 0)), float(pad.get("y_mm", 0))
            if int(footprint.get("side", 0)) == 1:
                py = -py
            pw, ph = float(pad.get("w_mm", .5)), float(pad.get("h_mm", .5))
            net = int(pad.get("net", -1))
            fill = "#f6c85f" if net >= 0 else "#6e7681"
            parts.append(f'<rect x="{px-pw/2}" y="{py-ph/2}" width="{pw}" height="{ph}" '
                         f'rx="{min(pw, ph)*.15}" fill="{fill}" stroke="#111" stroke-width="0.08"/>')
        parts.append('</g>')
        parts.append(f'<text x="{x}" y="{y}" fill="#f0f6fc" font-size="1.35" '
                     f'text-anchor="middle" dominant-baseline="middle">{esc(footprint["ref"])}</text>')
    for via in board.get("vias", []):
        radius = float(via.get("dia_mm", .6)) / 2
        parts.append(f'<circle cx="{via["x_mm"]}" cy="{via["y_mm"]}" r="{radius}" '
                     'fill="#d29922" stroke="#111" stroke-width="0.1"/>')
        parts.append(f'<circle cx="{via["x_mm"]}" cy="{via["y_mm"]}" '
                     f'r="{float(via.get("drill_mm", .3))/2}" fill="#0d1117"/>')
    parts.extend(['</g>',
        '<g font-family="sans-serif" font-size="1.8" fill="#f0f6fc">',
        '<text x="2" y="-1">DesignStudio — Agent Workflow Controller / finalized four-layer PCB</text>',
        '</g></svg>'])
    return "\n".join(parts) + "\n"


def schematic_svg(board: dict) -> str:
    symbols = board["schematic"]["symbols"]
    net_names = {int(net["id"]): str(net["name"]) for net in board["net_table"]}
    columns = 4
    cell_w, cell_h = 92, 42
    rows = math.ceil(len(symbols) / columns)
    width, height = columns * cell_w + 8, rows * cell_h + 16
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="{width*4}" height="{height*4}">',
             '<rect width="100%" height="100%" fill="#fffdf5"/>',
             '<g font-family="sans-serif">',
             '<text x="5" y="6" font-size="3.5" font-weight="bold">Agent Workflow Controller — pin-to-net schematic review</text>',
             '<text x="5" y="10" font-size="1.8" fill="#355e3b">Every pin stub terminates in its authoritative net label; NC means an intentional no-connect.</text>']
    for symbol_index, symbol in enumerate(symbols):
        column = symbol_index % columns
        row = symbol_index // columns
        x = column * cell_w + cell_w / 2
        y = row * cell_h + 27
        pins = symbol["pins"]
        split = math.ceil(len(pins) / 2)
        left, right = pins[:split], pins[split:]
        body_h = max(12.0, 4.0 + max(len(left), len(right)) * 2.3)
        body_w = 30.0
        parts.append(f'<rect x="{x-body_w/2}" y="{y-body_h/2}" width="{body_w}" height="{body_h}" '
                     'rx="0.8" fill="#f7f2dc" stroke="#3d4b40" stroke-width="0.25"/>')
        parts.append(f'<text x="{x}" y="{y-2}" text-anchor="middle" font-size="2.1" '
                     f'font-weight="bold">{esc(symbol["ref"])}</text>')
        value = symbol.get("value") or symbol["lib"]
        parts.append(f'<text x="{x}" y="{y+1}" text-anchor="middle" font-size="1.15">'
                     f'{esc(value)}</text>')
        parts.append(f'<text x="{x}" y="{y+3.2}" text-anchor="middle" font-size=".9" fill="#6b6b6b">'
                     f'{esc(symbol["lib"])}</text>')
        for side, side_pins in (("left", left), ("right", right)):
            for pin_index, pin in enumerate(side_pins):
                py = y - body_h / 2 + 3.2 + pin_index * 2.3
                if side == "left":
                    x0, x1, anchor = x - body_w / 2, x - body_w / 2 - 3.0, "end"
                    label_x = x1 - .7
                else:
                    x0, x1, anchor = x + body_w / 2, x + body_w / 2 + 3.0, "start"
                    label_x = x1 + .7
                net_id = int(pin.get("net", -1))
                net_label = net_names.get(net_id, "NC")
                color = "#22743b" if net_id >= 0 else "#8b1d1d"
                parts.append(f'<line x1="{x0}" y1="{py}" x2="{x1}" y2="{py}" '
                             f'stroke="{color}" stroke-width=".3"/>')
                parts.append(f'<circle cx="{x1}" cy="{py}" r=".38" fill="{color}"/>')
                parts.append(f'<text x="{label_x}" y="{py+.42}" text-anchor="{anchor}" '
                             f'font-size="1.05" fill="{color}">'
                             f'{esc(pin["num"])} {esc(pin["name"])} → {esc(net_label)}</text>')
    parts.extend(['</g></svg>'])
    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    board = json.loads((output / "agent-workflow-controller.dsproj").read_text())
    (output / "pcb-layout.svg").write_text(pcb_svg(board), encoding="utf-8")
    (output / "schematic.svg").write_text(schematic_svg(board), encoding="utf-8")
    manifest = json.loads((output / "evidence-manifest.json").read_text())
    with (output / "step-models.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Manufacturer", "MPN", "STEP path", "SHA-256", "Schema", "Round-trip"))
        for component in sorted(manifest["components"], key=lambda item: item["mpn"]):
            step = component["step"]
            writer.writerow((component["manufacturer"], component["mpn"], step["path"],
                             step["sha256"], step["schema"], step["roundtrip_valid"]))
    print(json.dumps({"ok": True, "pcb": str(output / "pcb-layout.svg"),
                      "schematic": str(output / "schematic.svg"),
                      "step_models": len(manifest["components"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
