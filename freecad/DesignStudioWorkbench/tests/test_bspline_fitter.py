from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


MODULE = Path(__file__).resolve().parents[3] / "tools/fit_glb_bspline_surfaces.py"
SPEC = importlib.util.spec_from_file_location("fit_glb_bspline_surfaces", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def test_planar_patch_is_fit_with_near_zero_deviation():
    x, y = np.meshgrid(np.linspace(0, 10, 9), np.linspace(0, 5, 7))
    points = np.column_stack((x.ravel(), y.ravel(), 2 + 0.2*x.ravel()))
    result = module.fit_patch(points, 6, 6, 1e-10)
    assert np.percentile(result["deviations"], 95) < 1e-4


def test_curvature_boundary_splits_orthogonal_faces():
    vertices = np.asarray([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1],
    ], dtype=float)
    faces = np.asarray([[0, 1, 2], [0, 2, 3], [0, 4, 5], [0, 5, 1]])
    segments = module.curvature_segments(vertices, faces, angle_degrees=10)
    assert sorted(len(item) for item in segments) == [2, 2]


def test_g2_projection_matches_boundary_tangent_and_curvature_rows():
    a = np.arange(6 * 6 * 3, dtype=float).reshape(6, 6, 3)
    b = (200 - np.arange(6 * 6 * 3, dtype=float)).reshape(6, 6, 3)
    module.enforce_continuity(a, "u1", b, "u0", reverse_b=True, grade="G2")
    a0, a1, a2 = module.edge_rows(a, "u1")
    b0, b1, b2 = module.edge_rows(b, "u0")
    b0, b1, b2 = b0[::-1], b1[::-1], b2[::-1]
    assert np.allclose(a0, b0)
    assert np.allclose(a1 - a0, -(b1 - b0))
    assert np.allclose(a0 - 2*a1 + a2, b0 - 2*b1 + b2)
