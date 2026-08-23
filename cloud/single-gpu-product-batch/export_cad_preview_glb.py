#!/usr/bin/env python3
"""Tessellate fitted editable B-spline control grids for browser review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import BSpline
import trimesh


def basis(values: np.ndarray, knots: list[float], degree: int) -> np.ndarray:
    control_count = len(knots) - degree - 1
    return np.asarray(BSpline.design_matrix(
        values, np.asarray(knots, dtype=float), degree
    ).toarray()).reshape(len(values), control_count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=32)
    args = parser.parse_args()
    fit = json.loads(args.fit.read_text(encoding="utf-8"))
    meshes = []
    values = np.linspace(0, 1, args.resolution)
    for patch in fit["patches"]:
        control = np.asarray(patch["control_grid_mm"], dtype=float) / 1000.0
        bu = basis(values, patch["knots_u"], int(patch["degree_u"]))
        bv = basis(values, patch["knots_v"], int(patch["degree_v"]))
        vertices = np.einsum("ui,vj,ijc->uvc", bu, bv, control).reshape(-1, 3)
        faces = []
        for u in range(args.resolution - 1):
            for v in range(args.resolution - 1):
                a = u * args.resolution + v
                b, c, d = a + 1, a + args.resolution, a + args.resolution + 1
                faces.extend(((a, c, b), (b, c, d)))
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        color = np.array([70, 160, 220, 255], dtype=np.uint8)
        mesh.visual.face_colors = np.tile(color, (len(mesh.faces), 1))
        meshes.append(mesh)
    scene = trimesh.Scene()
    for index, mesh in enumerate(meshes):
        scene.add_geometry(mesh, node_name=f"bspline-patch-{index:03d}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(scene.export(file_type="glb"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
