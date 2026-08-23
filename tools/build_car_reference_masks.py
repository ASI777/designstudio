#!/usr/bin/env python3
"""Extract deterministic car masks from cleaned neutral-background references."""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "references" / "porsche-911-turbo-s"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connected_background(rgb: np.ndarray, tolerance: float = 42.0) -> np.ndarray:
    height, width, _ = rgb.shape
    border = np.concatenate((
        rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]
    )).astype(np.float32)
    key = np.median(border, axis=0)
    distance = np.linalg.norm(rgb.astype(np.float32) - key, axis=2)
    candidate = distance <= tolerance
    background = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if candidate[0, x]:
            queue.append((0, x))
        if candidate[height - 1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if candidate[y, 0]:
            queue.append((y, 0))
        if candidate[y, width - 1]:
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if background[y, x]:
            continue
        background[y, x] = True
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (
                0 <= ny < height and 0 <= nx < width
                and candidate[ny, nx] and not background[ny, nx]
            ):
                queue.append((ny, nx))
    return background


def fill_enclosed_holes(foreground: np.ndarray) -> np.ndarray:
    """Fill background islands enclosed by the projected car silhouette."""
    height, width = foreground.shape
    exterior = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if not foreground[0, x]:
            queue.append((0, x))
        if not foreground[height - 1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if not foreground[y, 0]:
            queue.append((y, 0))
        if not foreground[y, width - 1]:
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if exterior[y, x] or foreground[y, x]:
            continue
        exterior[y, x] = True
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and not exterior[ny, nx]:
                queue.append((ny, nx))
    return foreground | (~foreground & ~exterior)


def main() -> int:
    cleaned = STUDY / "cleaned"
    masks = STUDY / "masks"
    rgba = STUDY / "clean"
    masks.mkdir(parents=True, exist_ok=True)
    rgba.mkdir(parents=True, exist_ok=True)
    records = []
    for source in sorted(cleaned.glob("*.png")):
        image = Image.open(source).convert("RGB")
        pixels = np.asarray(image)
        foreground = fill_enclosed_holes(~connected_background(pixels))
        mask = Image.fromarray((foreground * 255).astype(np.uint8), "L")
        # Close tiny antialiasing holes without expanding the silhouette far
        # enough to affect the engineering projection.
        mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
        mask_path = masks / f"{source.stem}-mask.png"
        mask.save(mask_path)
        alpha = np.asarray(mask)
        output = image.convert("RGBA")
        output.putalpha(Image.fromarray(alpha, "L"))
        rgba_path = rgba / f"{source.stem}.png"
        output.save(rgba_path)
        coverage = float(np.count_nonzero(alpha)) / float(alpha.size)
        if not 0.08 <= coverage <= 0.75:
            raise SystemExit(
                f"implausible foreground coverage for {source.name}: {coverage:.4f}"
            )
        records.append({
            "source": f"cleaned/{source.name}",
            "source_sha256": digest(source),
            "mask": f"masks/{mask_path.name}",
            "mask_sha256": digest(mask_path),
            "rgba": f"clean/{rgba_path.name}",
            "rgba_sha256": digest(rgba_path),
            "foreground_coverage": round(coverage, 6),
            "method": "border-connected-neutral-background-v1",
        })
    output = STUDY / "mask-manifest.json"
    output.write_text(
        json.dumps({"schema": "design-studio.car-mask-set/1", "masks": records},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
