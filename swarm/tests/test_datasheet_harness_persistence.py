from __future__ import annotations

import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swarm.memory.harness import Deps, Harness, Session


class _KnowledgeGraph:
    def get(self, _mpn):
        return None

    def ingest_json(self, _component):
        return True

    def save(self):
        return None


def _component(mpn="AUTO-IC-1"):
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Fixture", "mpn": mpn},
        "symbol": {"pins": [
            {"number": "1", "name": "IN", "electrical_type": "input"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"},
        ]},
        "footprint": {"name": "SOT_AUTO", "mount": "smd", "pads": [
            {"number": "1", "x_mm": -0.5, "y_mm": 0,
             "width_mm": 0.4, "height_mm": 0.8},
            {"number": "2", "x_mm": 0.5, "y_mm": 0,
             "width_mm": 0.4, "height_mm": 0.8},
        ]},
    }


class DatasheetHarnessPersistenceTests(unittest.TestCase):
    def test_successful_extraction_is_saved_for_immediate_board_application(self):
        session = Session(
            session_id="test", intent_text="build controller", phase="datasheet",
            components=[{"ref": "U1", "mpn": "AUTO-IC-1"}],
        )
        deps = Deps(
            kg=_KnowledgeGraph(), memory=None,
            fetch_datasheet=lambda _part: _component(),
        )
        harness = Harness(deps)
        with patch("swarm.memory.apply_to_board.save_component") as save:
            next_phase = harness._p_datasheet(session, deps)
        self.assertEqual(next_phase, "expand")
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0]["component"]["mpn"], "AUTO-IC-1")


if __name__ == "__main__":
    unittest.main()
