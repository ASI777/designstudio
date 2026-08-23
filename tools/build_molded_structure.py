#!/usr/bin/env python3
"""Build a product-bound, manufacturable neutral rib network.

This replaces the old camera-specific bounding-box exporter.  It consumes the
verified placement report, creates protected component envelopes, and emits a
revision-friendly rib graph plus a typed Mechanical CAD program.  FreeCAD is
still the authority that reconstructs and validates the resulting B-Reps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


WORKTREE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE / "freecad" / "DesignStudioWorkbench"))

from DesignStudio.manufacturing_rules import default_profile
from DesignStudio.rib_network import build_rib_network, to_mechanical_program


def build_workspace(workspace: Path, *, board: dict | None = None) -> dict:
    workspace = workspace.expanduser().resolve()
    placement_path = workspace / "electronics" / "placement-report.json"
    if not placement_path.is_file():
        raise FileNotFoundError(f"placement report is missing: {placement_path}")
    placements_doc = json.loads(placement_path.read_text(encoding="utf-8"))
    placements = placements_doc.get("placements", [])
    if not placements:
        raise ValueError("placement report contains no component placements")
    board = board or {"width": 64.0, "height": 61.0, "thickness": 1.6,
                      "origin_body": [55.0, 14.0], "y0": 8.0}
    ox, oz = [float(value) for value in board.get("origin_body", [55.0, 14.0])]
    y0 = float(board.get("y0", 8.0))
    board_top = y0 + float(board.get("thickness", 1.6))
    width, height = float(board["width"]), float(board["height"])
    domain = {"min": [ox + 1.5, board_top, oz + 1.5],
              "max": [ox + width - 1.5, board_top + 5.0, oz + height - 1.5]}

    obstacles = []
    anchors = []
    for placement in placements:
        ref = str(placement["ref"])
        cx = ox + float(placement["x_mm"])
        cz = oz + float(placement["y_mm"])
        half_x = float(placement["w_mm"]) / 2.0
        half_z = float(placement["l_mm"]) / 2.0
        top = board_top + float(placement["h_mm"])
        obstacles.append({"id": f"component:{ref}", "bounds": {
            "min": [cx - half_x, board_top, cz - half_z],
            "max": [cx + half_x, top, cz + half_z]}})
        # Use tall/heavy packages and board edges as structural anchors.  This
        # is a load-path proposal, not a substitute for a reviewed load case.
        if float(placement["h_mm"]) >= 3.0 or ref.startswith(("J", "U")):
            anchors.append({"id": f"anchor:{ref}",
                            "position_mm": [cx, min(top + 0.5, domain["max"][1]), cz],
                            "kind": "load" if ref.startswith("U") else "connector"})
    if not anchors:
        anchors.append({"id": "anchor:board-center",
                        "position_mm": [ox + width / 2.0, board_top, oz + height / 2.0],
                        "kind": "pcb"})

    profile = default_profile("injection_molding", "PC-ABS")
    spec = {
        "schema": "design-studio.rib-network/1",
        "design_volume_mm": domain,
        "anchors": anchors,
        "obstacles": obstacles,
        "load_cases": [{"anchor_id": anchor["id"], "force_n": [0.0, 0.0, -15.0]}
                        for anchor in anchors if anchor["kind"] == "load"],
        "manufacturing": {"wall_mm": profile["wall"]["nominal_mm"],
                           "clearance_mm": profile["clearance"]["component_mm"],
                           "pull_direction": [0.0, 1.0, 0.0]},
        "profile": profile,
        "seed": 42,
    }
    result = build_rib_network(spec)
    program = to_mechanical_program(result, profile=profile)
    mechanical = workspace / "mechanical"
    mechanical.mkdir(parents=True, exist_ok=True)
    (mechanical / "manufacturing-profile.json").write_text(
        json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    (mechanical / "rib-network-input.json").write_text(
        json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (mechanical / "rib-network.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (mechanical / "structure-program.json").write_text(
        json.dumps(program, indent=2) + "\n", encoding="utf-8")
    report = {"schema": "design-studio.structure-report/1",
              "source": "placement-report.json",
              "profile_id": profile["profile_id"],
              "rib_count": len(result["ribs"]),
              "status": result["status"],
              "findings": result["findings"],
              "candidate_geometry_authoritative": False}
    (mechanical / "structure-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"spec": spec, "result": result, "program": program, "report": report}


if __name__ == "__main__":
    workspace = Path(os.environ.get("DESIGNSTUDIO_WS", ""))
    if not workspace.is_dir():
        raise SystemExit("DESIGNSTUDIO_WS must identify a workspace")
    output = build_workspace(workspace)
    print(f"MOLDED_STRUCTURE_OK ribs={len(output['result']['ribs'])}")
