#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("camera_eval", ROOT / "tools" / "score_camera_model_eval.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class CameraModelEvalTests(unittest.TestCase):
    def test_invalid_contract_never_becomes_canonical(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in MODULE.MODEL_CONFIG:
                (root / f"{name}.json").write_text(json.dumps({
                    "schema": "design-studio.visual-evidence/2",
                    "observations": ["rounded body lens rear display top dial USB rib seam silver finish"],
                    "unknowns": ["scale hidden geometry material mechanism manufacturing release"],
                    "exclusions": ["browser chrome excluded"],
                }), encoding="utf-8")
                (root / f"{name}-events.jsonl").write_text(json.dumps({
                    "type": "turn.completed",
                    "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10},
                }) + "\n", encoding="utf-8")
            report = MODULE.build_report(root, ROOT / "docs" / "schemas" / "visual-evidence-v2.schema.json")
            self.assertEqual(report["decision"]["default"], "sol-high")
            self.assertFalse(report["decision"]["canonical_state_written"])
            for run in report["runs"].values():
                self.assertFalse(run["production_schema_valid"])
                self.assertFalse(run["eligible_for_canonical_state"])
                self.assertEqual(run["grounded_feature_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
