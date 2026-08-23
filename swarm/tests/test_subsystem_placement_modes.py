#!/usr/bin/env python3
"""Regression tests for the draft -> optimized placement lifecycle."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[1] / "agents"
sys.path.insert(0, str(AGENTS))
from subsystem_placer_agent import main  # noqa: E402


def fixture() -> dict:
    return {
        "board_width_mm": 40,
        "board_height_mm": 30,
        "board_outline_pts": [[0, 0], [40, 0], [40, 30], [0, 30]],
        "net_table": [{"id": 1, "name": "USB_DP"}],
        "footprints": [
            {
                "ref": "J1", "x_mm": 1, "y_mm": 1, "rot_deg": 0,
                "body_w_mm": 8, "body_h_mm": 4,
                "courtyard_pts": [[-6, -3], [6, -3], [6, 3], [-6, 3]],
                "pads": [{"name": "1", "x_mm": 3, "y_mm": 0, "w_mm": 1, "h_mm": 1, "net": 1}],
                "placement": {"edge_anchor": "left"},
            },
            {
                "ref": "U1", "x_mm": 35, "y_mm": 25, "rot_deg": 0,
                "body_w_mm": 5, "body_h_mm": 5,
                "pads": [{"name": "1", "x_mm": -3, "y_mm": 0, "w_mm": 1, "h_mm": 1, "net": 1}],
            },
        ],
        "traces": [], "vias": [],
    }


def auto_sized_fixture() -> dict:
    data = fixture()
    data["board_width_mm"] = 10
    data["board_height_mm"] = 8
    data["board_outline_pts"] = [[0, 0], [10, 0], [10, 8], [0, 8]]
    data["mechanical_contract"] = {
        "board": {"outline_pts": [[0, 0], [10, 0], [10, 8], [0, 8]]}
    }
    data["placement_state"] = {
        "mode": "draft", "status": "awaiting_optimization",
        "substrate_sizing": "expanded_to_component_courtyards",
        "mechanical_reconciliation": "pending",
        "mechanical_target_mm": [10, 8],
    }
    return data


class SubsystemPlacementModeTests(unittest.TestCase):
    def run_mode(self, mode: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "placement.dsproj"
            path.write_text(json.dumps(fixture()))
            self.assertEqual(main(str(path), mode), 0)
            return json.loads(path.read_text())

    def test_draft_is_substrate_contained_but_not_routed(self) -> None:
        data = self.run_mode("draft")
        self.assertEqual(data["placement_state"]["mode"], "draft")
        self.assertEqual(data["placement_state"]["status"], "awaiting_optimization")
        self.assertEqual(data["traces"], [])
        self.assertEqual(data["placement_state"]["substrate_containment"], "passed")
        connector = next(fp for fp in data["footprints"] if fp["ref"] == "J1")
        self.assertGreaterEqual(connector["x_mm"] - 6.0, 0.25 - 1e-6)

    def test_optimized_state_legalizes_edge_anchor(self) -> None:
        data = self.run_mode("optimized")
        self.assertEqual(data["placement_state"]["mode"], "optimized")
        self.assertEqual(data["placement_state"]["status"], "legalization_passed")
        connector = next(fp for fp in data["footprints"] if fp["ref"] == "J1")
        self.assertGreaterEqual(connector["x_mm"], 0)
        self.assertLessEqual(connector["x_mm"], data["board_width_mm"])
        self.assertIn(connector["rot_deg"], (0, 90, 180, 270))

    def test_auto_sized_board_grows_instead_of_restoring_mechanical_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auto-sized.dsproj"
            path.write_text(json.dumps(auto_sized_fixture()))
            self.assertEqual(main(str(path), "optimized"), 0)
            data = json.loads(path.read_text())
        self.assertGreater(data["board_width_mm"], 10)
        self.assertGreater(data["board_height_mm"], 8)
        self.assertEqual(data["placement_state"]["substrate_sizing"],
                         "routing_optimized_auto_size")
        self.assertEqual(data["placement_state"]["mechanical_reconciliation"], "pending")
        self.assertEqual(data["board_outline_pts"], [
            [0, 0], [data["board_width_mm"], 0],
            [data["board_width_mm"], data["board_height_mm"]],
            [0, data["board_height_mm"]],
        ])


if __name__ == "__main__":
    unittest.main()
