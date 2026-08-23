#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "camera_review", ROOT / "freecad" / "DesignStudioWorkbench" / "DesignStudio" / "camera_review.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class CameraReviewTests(unittest.TestCase):
    def test_normalization_removes_absolute_scale(self):
        a = [{"id": "a", "layer": "visible", "points": [[10, 20], [20, 40]]}]
        b = [{"id": "b", "layer": "visible", "points": [[100, 200], [200, 400]]}]
        self.assertEqual(MODULE.normalize_curves(a)[0]["points"],
                         MODULE.normalize_curves(b)[0]["points"])


if __name__ == "__main__":
    unittest.main()
