#!/usr/bin/env python3
"""Build a deterministic GLB from the six approved orthographic silhouettes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OMNI = ROOT / "cloud" / "hunyuan-omni-amd"
sys.path.insert(0, str(OMNI))
from multiview import CARDINAL_VIEWS, VIEW_PROJECTIONS  # noqa: E402

DIMENSIONS_M = np.asarray((0.08934, 0.20000, 0.05735), dtype=np.float64)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def masks(directory: Path) -> dict[str, np.ndarray]:
    result = {}
    for view in CARDINAL_VIEWS:
        path = directory / f"{view}.png"
        with Image.open(path) as image:
            result[view] = np.asarray(image.convert("L"), dtype=np.uint8) >= 128
    return result


def project(mask: np.ndarray, coordinates: np.ndarray, view: str) -> np.ndarray:
    right, up = VIEW_PROJECTIONS[view]
    horizontal = coordinates[:, right[0]] * right[1]
    vertical = coordinates[:, up[0]] * up[1]
    height, width = mask.shape
    px = np.rint((horizontal + 1.0) * 0.5 * (width - 1)).astype(np.int64)
    py = np.rint((1.0 - vertical) * 0.5 * (height - 1)).astype(np.int64)
    return mask[py, px]


def volume_from_masks(approved: dict[str, np.ndarray], resolution: int) -> np.ndarray:
    axis = (np.arange(resolution, dtype=np.float32) + 0.5) / resolution * 2.0 - 1.0
    yy, zz = np.meshgrid(axis, axis, indexing="ij")
    yz = np.column_stack((yy.ravel(), zz.ravel()))
    volume = np.empty((resolution, resolution, resolution), dtype=bool)
    for ix, x in enumerate(axis):
        coordinates = np.column_stack((
            np.full(len(yz), x, dtype=np.float32), yz[:, 0], yz[:, 1],
        ))
        accepted = np.ones(len(coordinates), dtype=bool)
        for view in CARDINAL_VIEWS:
            accepted &= project(approved[view], coordinates, view)
        volume[ix] = accepted.reshape((resolution, resolution))
    if not volume.any():
        raise RuntimeError("approved silhouettes have no common visual hull")
    return volume


def exposed(volume: np.ndarray, axis: int, positive: bool) -> np.ndarray:
    neighbour = np.zeros_like(volume)
    source = [slice(None)] * 3
    target = [slice(None)] * 3
    if positive:
        source[axis] = slice(1, None)
        target[axis] = slice(None, -1)
    else:
        source[axis] = slice(None, -1)
        target[axis] = slice(1, None)
    neighbour[tuple(target)] = volume[tuple(source)]
    return volume & ~neighbour


def surface_mesh(volume: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    # Four integer grid corners for each outward voxel face.
    faces = (
        (0, False, ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
        (0, True,  ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
        (1, False, ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
        (1, True,  ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
        (2, False, ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
        (2, True,  ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    )
    resolution = volume.shape[0]
    stride = resolution + 1
    quad_ids = []
    for axis, positive, offsets in faces:
        cells = np.argwhere(exposed(volume, axis, positive)).astype(np.int64)
        if not len(cells):
            continue
        corners = np.stack([cells + np.asarray(offset) for offset in offsets], axis=1)
        packed = (
            corners[..., 0] * stride * stride
            + corners[..., 1] * stride
            + corners[..., 2]
        )
        quad_ids.append(packed)
    packed_quads = np.concatenate(quad_ids, axis=0)
    unique, inverse = np.unique(packed_quads.reshape(-1), return_inverse=True)
    quads = inverse.reshape((-1, 4)).astype(np.uint32)
    triangles = np.concatenate((quads[:, (0, 1, 2)], quads[:, (0, 2, 3)]), axis=0)

    x = unique // (stride * stride)
    remainder = unique % (stride * stride)
    y = remainder // stride
    z = remainder % stride
    grid = np.column_stack((x, y, z)).astype(np.float64)
    minimum = grid.min(axis=0)
    extent = grid.max(axis=0) - minimum
    if np.any(extent <= 0):
        raise RuntimeError("visual hull has a degenerate axis")
    xyz = ((grid - minimum) / extent - 0.5) * DIMENSIONS_M
    # Match the Omni/trimesh artifact convention. Blender's glTF importer then
    # presents these source X=width/Y=depth/Z=height coordinates as
    # X=width/Y=height/Z=depth for deterministic review rendering.
    return xyz.astype(np.float32), triangles


def write_glb(path: Path, positions: np.ndarray, triangles: np.ndarray) -> None:
    position_bytes = positions.tobytes(order="C")
    index_bytes = triangles.astype(np.uint32).tobytes(order="C")
    binary = position_bytes
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    index_offset = len(binary)
    binary += index_bytes
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    document = {
        "asset": {
            "version": "2.0",
            "generator": "DesignStudio approved six-view visual hull",
        },
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {
                "buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes),
                "target": 34962,
            },
            {
                "buffer": 0, "byteOffset": index_offset,
                "byteLength": len(index_bytes), "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0, "componentType": 5126,
                "count": len(positions), "type": "VEC3",
                "min": positions.min(axis=0).tolist(),
                "max": positions.max(axis=0).tolist(),
            },
            {
                "bufferView": 1, "componentType": 5125,
                "count": int(triangles.size), "type": "SCALAR",
            },
        ],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0}, "indices": 1, "mode": 4,
        }]}],
        "nodes": [{"mesh": 0, "name": "ApprovedVisualHull"}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    total = 12 + 8 + len(encoded) + 8 + len(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(encoded), b"JSON") + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--masks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=256)
    args = parser.parse_args()
    if not 32 <= args.resolution <= 512:
        parser.error("--resolution must be in [32, 512]")
    approved = masks(args.masks)
    volume = volume_from_masks(approved, args.resolution)
    positions, triangles = surface_mesh(volume)
    write_glb(args.output, positions, triangles)
    manifest = {
        "schema": "design-studio.approved-visual-hull/1",
        "source": "six user-approved orthographic silhouette masks",
        "resolution": args.resolution,
        "dimensions_mm": {
            "overall_length": 200.0,
            "overall_width_including_mirrors": 89.34,
            "overall_height": 57.35,
        },
        "vertices": len(positions),
        "triangles": len(triangles),
        "occupied_voxels": int(volume.sum()),
        "glb": args.output.name,
        "glb_sha256": sha256(args.output),
        "mask_sha256": {
            view: sha256(args.masks / f"{view}.png")
            for view in CARDINAL_VIEWS
        },
        "release_eligible": False,
        "blocking_findings": ["SIX_VIEW_SCORE_NOT_YET_MEASURED"],
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
