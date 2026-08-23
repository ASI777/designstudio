"""Small, deterministic triangle-mesh utilities for the CAD dataset pipeline."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np


def read_stl(path: Path | str) -> np.ndarray:
    """Return triangles as float64 with shape (N, 3, 3)."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            records = np.frombuffer(
                data, dtype=np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attr", "<u2")]),
                offset=84,
                count=count,
            )
            return records["vertices"].astype(np.float64)

    text = data.decode("utf-8", errors="replace")
    values: list[list[float]] = []
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) == 4 and fields[0].lower() == "vertex":
            values.append([float(value) for value in fields[1:]])
    if not values or len(values) % 3:
        raise ValueError(f"{path}: not a valid binary or ASCII STL")
    return np.asarray(values, dtype=np.float64).reshape((-1, 3, 3))


def write_stl(path: Path | str, triangles: np.ndarray, name: str = "design-studio") -> None:
    triangles = np.asarray(triangles, dtype=np.float32)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise ValueError("triangles must have shape (N, 3, 3)")
    edges_a = triangles[:, 1] - triangles[:, 0]
    edges_b = triangles[:, 2] - triangles[:, 0]
    normals = np.cross(edges_a, edges_b)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 0
    normals[valid] /= lengths[valid, None]
    normals[~valid] = 0

    header = name.encode("ascii", errors="replace")[:80].ljust(80, b"\0")
    record_type = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attr", "<u2")])
    records = np.zeros(len(triangles), dtype=record_type)
    records["normal"] = normals
    records["vertices"] = triangles
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", len(records)))
        stream.write(records.tobytes())


def indexed_mesh(triangles: np.ndarray, tolerance: float = 1e-7) -> tuple[np.ndarray, np.ndarray]:
    flat = np.asarray(triangles, dtype=np.float64).reshape((-1, 3))
    quantized = np.round(flat / tolerance).astype(np.int64)
    _, first, inverse = np.unique(quantized, axis=0, return_index=True, return_inverse=True)
    return flat[first], inverse.reshape((-1, 3))


def write_obj(path: Path | str, triangles: np.ndarray) -> None:
    vertices, faces = indexed_mesh(triangles)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as stream:
        stream.write("# normalized CAD mesh; units are scale-normalized\n")
        for x, y, z in vertices:
            stream.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")
        for a, b, c in faces + 1:
            stream.write(f"f {a} {b} {c}\n")


def bounds(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    flat = np.asarray(triangles).reshape((-1, 3))
    minimum = flat.min(axis=0)
    maximum = flat.max(axis=0)
    return minimum, maximum, maximum - minimum


def normalize_unit_cube(triangles: np.ndarray) -> tuple[np.ndarray, dict[str, list[float] | float]]:
    minimum, maximum, dimensions = bounds(triangles)
    scale = float(dimensions.max())
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("mesh has a zero or invalid bounding box")
    center = (minimum + maximum) / 2.0
    normalized = (np.asarray(triangles, dtype=np.float64) - center) / scale
    return normalized, {
        "center_mm": center.tolist(),
        "normalization_scale_mm": scale,
        "bbox_min_mm": minimum.tolist(),
        "bbox_max_mm": maximum.tolist(),
        "dimensions_mm": dimensions.tolist(),
    }


def watertight_report(triangles: np.ndarray) -> dict[str, int | bool]:
    vertices, faces = indexed_mesh(triangles)
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = int(np.count_nonzero(counts == 1))
    nonmanifold = int(np.count_nonzero(counts > 2))
    return {
        "watertight": boundary == 0 and nonmanifold == 0,
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
    }


def box_triangles(center: tuple[float, float, float], size: tuple[float, float, float]) -> np.ndarray:
    center_array = np.asarray(center, dtype=np.float64)
    half = np.asarray(size, dtype=np.float64) / 2.0
    vertices = np.asarray(
        [
            [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
            [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
        ],
        dtype=np.float64,
    ) * half + center_array
    faces = np.asarray(
        [
            [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
        ],
        dtype=np.int64,
    )
    return vertices[faces]


def sample_surface(triangles: np.ndarray, count: int, seed: int = 0) -> np.ndarray:
    triangles = np.asarray(triangles, dtype=np.float64)
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(cross, axis=1) / 2.0
    total = float(areas.sum())
    if total <= 0:
        raise ValueError("mesh has no positive-area triangles")
    rng = np.random.default_rng(seed)
    indices = rng.choice(len(triangles), size=count, p=areas / total)
    selected = triangles[indices]
    u = rng.random(count)
    v = rng.random(count)
    reflect = u + v > 1
    u[reflect] = 1 - u[reflect]
    v[reflect] = 1 - v[reflect]
    points = selected[:, 0] + u[:, None] * (selected[:, 1] - selected[:, 0]) + v[:, None] * (
        selected[:, 2] - selected[:, 0]
    )
    normals = cross[indices]
    normal_lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(normal_lengths[:, None], 1e-15)
    return np.concatenate((points, normals), axis=1)


def nearest_distances(source: np.ndarray, target: np.ndarray, block_size: int = 512) -> np.ndarray:
    try:
        from scipy.spatial import cKDTree

        return cKDTree(target).query(source, workers=-1)[0]
    except ImportError:
        output = np.empty(len(source), dtype=np.float64)
        for start in range(0, len(source), block_size):
            block = source[start : start + block_size]
            squared = ((block[:, None, :] - target[None, :, :]) ** 2).sum(axis=2)
            output[start : start + len(block)] = np.sqrt(squared.min(axis=1))
        return output


def render_preview(triangles: np.ndarray, path: Path | str, azimuth_deg: float, elevation_deg: float = 25) -> None:
    from PIL import Image, ImageDraw

    normalized, _ = normalize_unit_cube(triangles)
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    rz = np.asarray(
        [[math.cos(azimuth), -math.sin(azimuth), 0], [math.sin(azimuth), math.cos(azimuth), 0], [0, 0, 1]]
    )
    rx = np.asarray(
        [[1, 0, 0], [0, math.cos(elevation), -math.sin(elevation)], [0, math.sin(elevation), math.cos(elevation)]]
    )
    rotated = normalized @ (rz @ rx).T
    width = height = 518
    xy = rotated[..., :2]
    xy[..., 1] *= -1
    pixels = xy * 390 + np.asarray([width / 2, height / 2])
    order = np.argsort(rotated[..., 2].mean(axis=1))
    image = Image.new("RGBA", (width, height), (250, 250, 250, 0))
    draw = ImageDraw.Draw(image)
    light = np.asarray([0.3, -0.4, 0.85])
    light /= np.linalg.norm(light)
    for index in order:
        tri = rotated[index]
        normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        length = np.linalg.norm(normal)
        shade = 0.55 if length == 0 else 0.45 + 0.45 * abs(float(np.dot(normal / length, light)))
        color = tuple(int(channel * shade) for channel in (95, 145, 205)) + (255,)
        polygon = [tuple(point) for point in pixels[index]]
        draw.polygon(polygon, fill=color, outline=(45, 55, 70, 255))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
