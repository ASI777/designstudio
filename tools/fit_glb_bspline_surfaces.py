#!/usr/bin/env python3
"""Deterministic curvature segmentation and tensor-product B-spline fitting.

The worker emits editable control grids and per-vertex deviation colors. Exact
shell sewing and AP242 authority are deliberately deferred to FreeCAD.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.interpolate import BSpline
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[1]
GLB_READER = ROOT / "freecad/DesignStudioWorkbench/DesignStudio/glb_mesh.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_glb(path: Path) -> tuple[np.ndarray, np.ndarray]:
    spec = importlib.util.spec_from_file_location("campaign_glb_mesh", GLB_READER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for primitive in module.read_glb(path):
        offset = len(vertices)
        vertices.extend(primitive["vertices_mm"])
        triangles.extend(
            (a + offset, b + offset, c + offset)
            for a, b, c in primitive["triangles"]
        )
    return np.asarray(vertices, dtype=np.float64), np.asarray(triangles, dtype=np.int64)


def face_geometry(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    points = vertices[faces]
    cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(lengths[:, None], 1e-15)
    return points.mean(axis=1), normals


def curvature_segments(
    vertices: np.ndarray, faces: np.ndarray, *,
    angle_degrees: float = 18.0, maximum_patches: int = 96,
) -> list[np.ndarray]:
    """Split the face graph across high-dihedral edges, then cap patch count."""
    _, normals = face_geometry(vertices, faces)
    parent = np.arange(len(faces), dtype=np.int64)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    edge_owner: dict[tuple[int, int], int] = {}
    cosine = math.cos(math.radians(angle_degrees))
    for face_index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(a, b)), int(max(a, b)))
            neighbor = edge_owner.get(edge)
            if neighbor is None:
                edge_owner[edge] = face_index
            elif float(np.dot(normals[face_index], normals[neighbor])) >= cosine:
                union(face_index, neighbor)
    regions: dict[int, list[int]] = {}
    for index in range(len(faces)):
        regions.setdefault(find(index), []).append(index)
    ordered = sorted(regions.values(), key=lambda values: (-len(values), values[0]))
    if len(ordered) > maximum_patches:
        retained = ordered[:maximum_patches]
        centroids, _ = face_geometry(vertices, faces)
        retained_centers = np.asarray([
            centroids[np.asarray(region)].mean(axis=0) for region in retained
        ])
        for region in ordered[maximum_patches:]:
            target = int(np.argmin(np.linalg.norm(
                retained_centers - centroids[np.asarray(region)].mean(axis=0), axis=1
            )))
            retained[target].extend(region)
        ordered = retained
    return [np.asarray(sorted(region), dtype=np.int64) for region in ordered]


def spatial_normal_charts(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    maximum_patches: int,
    minimum_faces: int = 48,
) -> list[np.ndarray]:
    """Partition a mesh into deterministic local, near-graph-like charts.

    A connected low-dihedral region can wrap around a cylinder or torus and is
    therefore not a single-valued PCA height field.  Recursively splitting a
    normalized position+normal feature space prevents those folded UV charts.
    It also avoids the old behavior of merging distant small regions merely to
    satisfy a patch-count cap.
    """
    centroids, normals = face_geometry(vertices, faces)
    extent = np.maximum(np.ptp(centroids, axis=0), 1e-9)
    position = (centroids - centroids.mean(axis=0)) / extent
    features = np.column_stack((position, normals * 0.65))
    charts = [np.arange(len(faces), dtype=np.int64)]
    while len(charts) < maximum_patches:
        candidates = [
            (len(chart), -index, index)
            for index, chart in enumerate(charts)
            if len(chart) >= minimum_faces * 2
        ]
        if not candidates:
            break
        _, _, index = max(candidates)
        chart = charts.pop(index)
        split = split_chart(features, chart, minimum_faces=minimum_faces)
        if split is None:
            charts.insert(index, chart)
            break
        left, right = split
        charts.extend((left, right))
    return sorted(charts, key=lambda chart: int(chart[0]))


def split_chart(
    features: np.ndarray,
    chart: np.ndarray,
    *,
    minimum_faces: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    local = features[chart]
    centered = local - local.mean(axis=0)
    _, _, axes = np.linalg.svd(centered, full_matrices=False)
    coordinate = centered @ axes[0]
    order = np.argsort(coordinate, kind="mergesort")
    middle = len(order) // 2
    left = np.sort(chart[order[:middle]])
    right = np.sort(chart[order[middle:]])
    if len(left) < minimum_faces or len(right) < minimum_faces:
        return None
    return left, right


def open_knots(control_count: int, degree: int = 3) -> np.ndarray:
    if control_count <= degree:
        raise ValueError("control count must exceed degree")
    interior = np.linspace(0, 1, control_count - degree + 1)[1:-1]
    return np.concatenate((np.zeros(degree + 1), interior, np.ones(degree + 1)))


def basis(values: np.ndarray, control_count: int) -> np.ndarray:
    return np.asarray(BSpline.design_matrix(
        np.clip(values, 0, 1), open_knots(control_count), 3
    ).toarray())


def fit_patch(points: np.ndarray, control_u: int, control_v: int,
              fairness: float) -> dict[str, Any]:
    center = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - center, full_matrices=False)
    uv = (points - center) @ axes[:2].T
    span = np.maximum(np.ptp(uv, axis=0), 1e-9)
    uv = (uv - uv.min(axis=0)) / span
    bu, bv = basis(uv[:, 0], control_u), basis(uv[:, 1], control_v)
    design = np.einsum("ni,nj->nij", bu, bv).reshape(len(points), -1)
    # Tikhonov regularization is the discrete control-net fairness energy.
    identity = np.eye(control_u * control_v)
    augmented = np.vstack((design, math.sqrt(fairness) * identity))
    targets = np.vstack((points, np.tile(center, (len(identity), 1)) *
                         math.sqrt(fairness)))
    control, *_ = np.linalg.lstsq(augmented, targets, rcond=None)
    fitted = design @ control
    deviations = np.linalg.norm(fitted - points, axis=1)
    return {
        "control_grid": control.reshape(control_u, control_v, 3),
        "knots_u": open_knots(control_u),
        "knots_v": open_knots(control_v),
        "parameter_frame": {"center": center, "axes": axes[:2], "span": span},
        "deviations": deviations,
        "_design": design,
    }


def edge_rows(grid: np.ndarray, edge: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return boundary and two inward control rows as mutable views."""
    if edge == "u0":
        return grid[0, :, :], grid[1, :, :], grid[2, :, :]
    if edge == "u1":
        return grid[-1, :, :], grid[-2, :, :], grid[-3, :, :]
    if edge == "v0":
        return grid[:, 0, :], grid[:, 1, :], grid[:, 2, :]
    if edge == "v1":
        return grid[:, -1, :], grid[:, -2, :], grid[:, -3, :]
    raise ValueError(f"unknown edge {edge}")


def enforce_continuity(
    grid_a: np.ndarray, edge_a: str, grid_b: np.ndarray, edge_b: str,
    *, reverse_b: bool = False, grade: str = "G2",
) -> None:
    """Project compatible control rows onto G0, G1 or G2 constraints."""
    if grade not in {"G0", "G1", "G2"}:
        raise ValueError("continuity grade must be G0, G1 or G2")
    a0, a1, a2 = edge_rows(grid_a, edge_a)
    b0, b1, b2 = edge_rows(grid_b, edge_b)
    if reverse_b:
        b0, b1, b2 = b0[::-1], b1[::-1], b2[::-1]
    if a0.shape != b0.shape:
        raise ValueError("edge control counts must match")
    common = (a0.copy() + b0.copy()) * 0.5
    a0[:] = common
    b0[:] = common
    if grade in {"G1", "G2"}:
        derivative = ((a1 - common) - (b1 - common)) * 0.5
        a1[:] = common + derivative
        b1[:] = common - derivative
    if grade == "G2":
        curvature_a = common - 2 * a1 + a2
        curvature_b = common - 2 * b1 + b2
        curvature = (curvature_a + curvature_b) * 0.5
        a2[:] = curvature - common + 2 * a1
        b2[:] = curvature - common + 2 * b1


def nearest_edge_pair(grid_a: np.ndarray, grid_b: np.ndarray) -> tuple[str, str, bool, float]:
    best: tuple[str, str, bool, float] | None = None
    for edge_a in ("u0", "u1", "v0", "v1"):
        a = edge_rows(grid_a, edge_a)[0]
        for edge_b in ("u0", "u1", "v0", "v1"):
            b = edge_rows(grid_b, edge_b)[0]
            if a.shape != b.shape:
                continue
            for reverse in (False, True):
                candidate = b[::-1] if reverse else b
                distance = float(np.mean(np.linalg.norm(a - candidate, axis=1)))
                value = (edge_a, edge_b, reverse, distance)
                if best is None or distance < best[3]:
                    best = value
    if best is None:
        raise ValueError("patch grids expose no compatible edge pair")
    return best


def write_heatmap(path: Path, vertices: np.ndarray, deviations: np.ndarray) -> None:
    scale = max(float(np.percentile(deviations, 95)), 1e-9)
    ratio = np.clip(deviations / scale, 0, 1)
    colors = np.column_stack((
        (255 * ratio).astype(int),
        (255 * (1 - ratio)).astype(int),
        np.full(len(vertices), 32),
    ))
    lines = [
        "ply", "format ascii 1.0", f"element vertex {len(vertices)}",
        "property float x", "property float y", "property float z",
        "property uchar red", "property uchar green", "property uchar blue",
        "end_header",
    ]
    lines.extend(
        f"{point[0]:.9g} {point[1]:.9g} {point[2]:.9g} "
        f"{color[0]} {color[1]} {color[2]}"
        for point, color in zip(vertices, colors)
    )
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def fit_mesh(vertices: np.ndarray, faces: np.ndarray, *, control_u: int = 6,
             control_v: int = 6, fairness: float = 1e-5,
             maximum_patches: int = 96) -> tuple[dict[str, Any], np.ndarray]:
    # Start coarse and spend the patch budget only where the deterministic
    # interpolation error demands it.
    initial_patches = max(96, maximum_patches // 8)
    segments = spatial_normal_charts(
        vertices, faces, maximum_patches=initial_patches
    )
    centroids, normals = face_geometry(vertices, faces)
    extent = np.maximum(np.ptp(centroids, axis=0), 1e-9)
    features = np.column_stack((
        (centroids - centroids.mean(axis=0)) / extent,
        normals * 0.65,
    ))
    vertex_deviation = np.full(len(vertices), np.nan)
    fitted_patches: list[dict[str, Any]] = []
    pending = list(segments)
    while pending:
        segment = pending.pop(0)
        vertex_indices = np.unique(faces[segment].ravel())
        points = vertices[vertex_indices]
        if len(points) < 16:
            continue
        fitted = fit_patch(points, control_u, control_v, fairness)
        if (
            float(fitted["deviations"].max()) > 1.25
            and len(fitted_patches) + len(pending) + 2 <= maximum_patches
        ):
            split = split_chart(features, segment, minimum_faces=16)
            if split is not None:
                pending.extend(split)
                continue
        index = len(fitted_patches)
        fitted_patches.append({
            "id": f"patch-{index:03d}",
            "_vertex_indices": vertex_indices,
            "_fitted": fitted,
            "face_count": int(len(segment)),
            "vertex_count": int(len(vertex_indices)),
            "control_grid_mm": np.round(fitted["control_grid"], 9).tolist(),
            "knots_u": fitted["knots_u"].tolist(),
            "knots_v": fitted["knots_v"].tolist(),
            "degree_u": 3, "degree_v": 3,
        })
    continuity_pairs = []
    for left in range(len(fitted_patches)):
        for right in range(left + 1, len(fitted_patches)):
            common_vertices = np.intersect1d(
                fitted_patches[left]["_vertex_indices"],
                fitted_patches[right]["_vertex_indices"],
                assume_unique=True,
            )
            if len(common_vertices) < 2:
                continue
            grid_a = fitted_patches[left]["_fitted"]["control_grid"]
            grid_b = fitted_patches[right]["_fitted"]["control_grid"]
            edge_a, edge_b, reverse, before = nearest_edge_pair(grid_a, grid_b)
            # PCA parameterizations do not guarantee that every topological
            # neighbor maps to a parametric edge. Never weld unrelated rows.
            if before > 2.0:
                continue
            candidate_a, candidate_b = grid_a.copy(), grid_b.copy()
            enforce_continuity(
                candidate_a, edge_a, candidate_b, edge_b,
                reverse_b=reverse, grade="G2",
            )
            left_fit = fitted_patches[left]["_fitted"]
            right_fit = fitted_patches[right]["_fitted"]
            left_vertices = fitted_patches[left]["_vertex_indices"]
            right_vertices = fitted_patches[right]["_vertex_indices"]
            original_max = max(
                float(np.linalg.norm(
                    left_fit["_design"] @ grid_a.reshape(-1, 3)
                    - vertices[left_vertices],
                    axis=1,
                ).max()),
                float(np.linalg.norm(
                    right_fit["_design"] @ grid_b.reshape(-1, 3)
                    - vertices[right_vertices],
                    axis=1,
                ).max()),
            )
            candidate_max = max(
                float(np.linalg.norm(
                    left_fit["_design"] @ candidate_a.reshape(-1, 3)
                    - vertices[left_vertices],
                    axis=1,
                ).max()),
                float(np.linalg.norm(
                    right_fit["_design"] @ candidate_b.reshape(-1, 3)
                    - vertices[right_vertices],
                    axis=1,
                ).max()),
            )
            accepted = candidate_max <= max(1.5, original_max * 1.02)
            if accepted:
                grid_a[:] = candidate_a
                grid_b[:] = candidate_b
            after = nearest_edge_pair(grid_a, grid_b)[3]
            continuity_pairs.append({
                "patch_a": fitted_patches[left]["id"],
                "edge_a": edge_a,
                "patch_b": fitted_patches[right]["id"],
                "edge_b": edge_b,
                "reversed": reverse,
                "grade": "G2",
                "mean_boundary_distance_before_mm": before,
                "mean_boundary_distance_after_mm": after,
                "accepted": accepted,
                "maximum_deviation_before_mm": original_max,
                "maximum_deviation_after_mm": candidate_max,
            })
    patches = []
    for patch in fitted_patches:
        fitted = patch.pop("_fitted")
        vertex_indices = patch.pop("_vertex_indices")
        fitted_points = fitted["_design"] @ fitted["control_grid"].reshape(-1, 3)
        deviations = np.linalg.norm(fitted_points - vertices[vertex_indices], axis=1)
        existing = vertex_deviation[vertex_indices]
        vertex_deviation[vertex_indices] = np.where(
            np.isnan(existing), deviations, np.minimum(existing, deviations)
        )
        patch["control_grid_mm"] = np.round(fitted["control_grid"], 9).tolist()
        patches.append(patch)
    finite = vertex_deviation[np.isfinite(vertex_deviation)]
    if not len(finite):
        raise RuntimeError("no segment had enough points to fit")
    vertex_deviation[np.isnan(vertex_deviation)] = float(finite.max())
    metrics = {
        "median_mm": float(np.median(vertex_deviation)),
        "p95_mm": float(np.percentile(vertex_deviation, 95)),
        "maximum_mm": float(vertex_deviation.max()),
    }
    return {
        "patches": patches,
        "patch_count": len(patches),
        "continuity": {
            "constraint_method": "shared-boundary control-row projection",
            "requested": ["G0", "G1", "G2"],
            "constrained_pairs": continuity_pairs,
            "verified_by_exact_host": False,
        },
        "deviation_metrics": metrics,
    }, vertex_deviation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--control-u", type=int, default=6)
    parser.add_argument("--control-v", type=int, default=6)
    parser.add_argument("--fairness", type=float, default=1e-5)
    parser.add_argument("--maximum-patches", type=int, default=96)
    args = parser.parse_args()
    vertices, faces = load_glb(args.input)
    result, deviations = fit_mesh(
        vertices, faces, control_u=args.control_u, control_v=args.control_v,
        fairness=args.fairness, maximum_patches=args.maximum_patches,
    )
    output = args.output_directory
    output.mkdir(parents=True, exist_ok=True)
    heatmap = output / "deviation-heatmap.ply"
    write_heatmap(heatmap, vertices, deviations)
    result.update({
        "schema": "design-studio.bspline-fit-candidate/1",
        "source_glb": args.input.name,
        "source_glb_sha256": sha(args.input),
        "source_vertices": len(vertices),
        "source_faces": len(faces),
        "density_ablations": [
            {"kind": "decimated", "target_faces": 100_000},
            {"kind": "decimated", "target_faces": 300_000},
            {"kind": "native", "target_faces": len(faces)},
            {"kind": "subdivided", "target_faces": 1_500_000},
        ],
        "deviation_heatmap": heatmap.name,
        "deviation_heatmap_sha256": sha(heatmap),
        "geometry_authority": "deterministic_least_squares_candidate",
        "valid_outer_shell": False,
        "ap242_roundtrip_valid": False,
        "release_eligible": False,
    })
    material = dict(result)
    result["result_sha256"] = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    (output / "fit-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["deviation_metrics"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
