"""Deterministic six-view silhouette fusion for Hunyuan3D-Omni.

The pinned Omni checkpoint accepts one appearance image and one geometric
control.  It does not fuse a list of views.  This module turns six registered
orthographic silhouettes into the native voxel-style surface point control so
every supplied direction affects one generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
from PIL import Image


CARDINAL_VIEWS = ("front", "rear", "left", "right", "top", "bottom")

# Each tuple is (image-right world axis, image-up world axis).  World axes are
# X=width, Y=depth, Z=height.  Signs make opposing views mirror correctly.
VIEW_PROJECTIONS = {
    "front": ((0, 1.0), (2, 1.0)),
    "rear": ((0, -1.0), (2, 1.0)),
    "left": ((1, -1.0), (2, 1.0)),
    "right": ((1, 1.0), (2, 1.0)),
    # The approved orthographic contract displays vehicle length horizontally
    # in plan view. Top looks down with width increasing toward image-down;
    # bottom reverses both the longitudinal and transverse image directions.
    "top": ((1, 1.0), (0, -1.0)),
    "bottom": ((1, -1.0), (0, -1.0)),
}


def read_silhouette(image_path: Path, mask_path: Path | None = None) -> np.ndarray:
    """Read a white-foreground mask or an image's alpha channel."""
    if mask_path is not None:
        with Image.open(mask_path) as image:
            mask = np.asarray(image.convert("L"), dtype=np.uint8) >= 128
    else:
        with Image.open(image_path) as image:
            if "A" not in image.getbands():
                raise ValueError(
                    "each directional image needs transparency or a separate mask_url")
            mask = np.asarray(image.getchannel("A"), dtype=np.uint8) >= 128

    coverage = float(mask.mean())
    if coverage <= 0.001 or coverage >= 0.999:
        raise ValueError("directional silhouette must contain foreground and background")
    return mask


def write_masked_reference(image_path: Path, mask: np.ndarray, output_path: Path) -> None:
    """Write the appearance image with the accepted silhouette as RGBA alpha."""
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
    alpha = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255, mode="L")
    if alpha.size != rgb.size:
        alpha = alpha.resize(rgb.size, resample=Image.Resampling.NEAREST)
    rgb.putalpha(alpha)
    rgb.save(output_path, format="PNG")


def _project(mask: np.ndarray, coordinates: np.ndarray, view: str) -> np.ndarray:
    right, up = VIEW_PROJECTIONS[view]
    horizontal = coordinates[:, right[0]] * right[1]
    vertical = coordinates[:, up[0]] * up[1]
    height, width = mask.shape
    x = np.rint((horizontal + 1.0) * 0.5 * (width - 1)).astype(np.int64)
    y = np.rint((1.0 - vertical) * 0.5 * (height - 1)).astype(np.int64)
    return mask[y, x]


def visual_hull_surface(masks: Mapping[str, np.ndarray], resolution: int = 48) -> np.ndarray:
    """Return normalized surface samples from the intersection of six masks."""
    missing = sorted(set(CARDINAL_VIEWS) - set(masks))
    extra = sorted(set(masks) - set(CARDINAL_VIEWS))
    if missing or extra:
        raise ValueError(f"visual hull requires exactly six cardinal views; missing={missing}, extra={extra}")
    if resolution < 16 or resolution > 128:
        raise ValueError("visual-hull resolution must be in [16, 128]")

    axis = (np.arange(resolution, dtype=np.float32) + 0.5) / resolution * 2.0 - 1.0
    x, y, z = np.meshgrid(axis, axis, axis, indexing="ij")
    coordinates = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
    occupied = np.ones(len(coordinates), dtype=bool)
    for view in CARDINAL_VIEWS:
        occupied &= _project(np.asarray(masks[view], dtype=bool), coordinates, view)
    volume = occupied.reshape((resolution, resolution, resolution))
    if not volume.any():
        raise ValueError("directional silhouettes have no common visual hull")

    interior = volume.copy()
    interior[0, :, :] = interior[-1, :, :] = False
    interior[:, 0, :] = interior[:, -1, :] = False
    interior[:, :, 0] = interior[:, :, -1] = False
    interior[1:-1, 1:-1, 1:-1] &= (
        volume[:-2, 1:-1, 1:-1]
        & volume[2:, 1:-1, 1:-1]
        & volume[1:-1, :-2, 1:-1]
        & volume[1:-1, 2:, 1:-1]
        & volume[1:-1, 1:-1, :-2]
        & volume[1:-1, 1:-1, 2:]
    )
    surface_indices = np.argwhere(volume & ~interior)
    points = (surface_indices.astype(np.float32) + 0.5) / resolution * 2.0 - 1.0
    return points
