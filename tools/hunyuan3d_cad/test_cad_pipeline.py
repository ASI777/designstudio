from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from meshlib import (
    bounds,
    box_triangles,
    normalize_unit_cube,
    read_stl,
    render_preview,
    sample_surface,
    watertight_report,
    write_stl,
)


class MeshPipelineTests(unittest.TestCase):
    def test_box_is_watertight_and_normalizes_to_unit_extent(self):
        mesh = box_triangles((2, 3, 4), (10, 5, 2))
        normalized, transform = normalize_unit_cube(mesh)
        _, _, dimensions = bounds(normalized)
        self.assertAlmostEqual(float(dimensions.max()), 1.0)
        self.assertEqual(transform["dimensions_mm"], [10.0, 5.0, 2.0])
        self.assertTrue(watertight_report(mesh)["watertight"])

    def test_binary_stl_round_trip(self):
        mesh = box_triangles((0, 0, 0), (1, 2, 3))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "box.stl"
            write_stl(path, mesh)
            np.testing.assert_allclose(read_stl(path), mesh)

    def test_surface_sampler_is_deterministic_and_on_box_bounds(self):
        mesh = box_triangles((0, 0, 0), (2, 2, 2))
        first = sample_surface(mesh, 1000, seed=7)
        second = sample_surface(mesh, 1000, seed=7)
        np.testing.assert_array_equal(first, second)
        self.assertTrue(np.all(np.max(np.abs(first[:, :3]), axis=1) > 0.999999))
        np.testing.assert_allclose(np.linalg.norm(first[:, 3:], axis=1), 1)

    def test_render_has_training_alpha_mask(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "view.png"
            render_preview(box_triangles((0, 0, 0), (2, 1, 0.5)), path, 30)
            image = Image.open(path)
            self.assertEqual(image.mode, "RGBA")
            alpha = np.asarray(image)[..., 3]
            self.assertEqual(int(alpha.min()), 0)
            self.assertEqual(int(alpha.max()), 255)


if __name__ == "__main__":
    unittest.main()
