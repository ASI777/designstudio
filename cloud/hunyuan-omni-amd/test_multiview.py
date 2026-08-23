#!/usr/bin/env python3
"""CPU tests for directional orientation, mask input and visual-hull fusion."""
from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np
from PIL import Image

from multiview import (CARDINAL_VIEWS, read_silhouette, visual_hull_surface,
                       write_masked_reference)


def ellipse(width: int, height: int, radius_x: float, radius_y: float) -> np.ndarray:
    x = (np.arange(width) + 0.5) / width * 2.0 - 1.0
    y = 1.0 - (np.arange(height) + 0.5) / height * 2.0
    xx, yy = np.meshgrid(x, y)
    return (xx / radius_x) ** 2 + (yy / radius_y) ** 2 <= 1.0


def run() -> None:
    masks = {
        "front": ellipse(96, 64, 0.72, 0.55),
        "rear": ellipse(96, 64, 0.72, 0.55),
        "left": ellipse(96, 64, 0.48, 0.55),
        "right": ellipse(96, 64, 0.48, 0.55),
        # Plan views display longitudinal Y horizontally and transverse X
        # vertically, matching the approved six-view rendering contract.
        "top": ellipse(96, 64, 0.48, 0.72),
        "bottom": ellipse(96, 64, 0.48, 0.72),
    }
    points = visual_hull_surface(masks, resolution=32)
    assert points.ndim == 2 and points.shape[1] == 3 and len(points) > 100
    extent = points.max(axis=0) - points.min(axis=0)
    assert extent[0] > extent[2] > extent[1], extent

    try:
        visual_hull_surface({view: masks[view] for view in CARDINAL_VIEWS[:-1]})
    except ValueError as error:
        assert "missing=['bottom']" in str(error)
    else:
        raise AssertionError("missing view was accepted")

    with tempfile.TemporaryDirectory(prefix="omni-masks-") as directory:
        alpha_path = Path(directory) / "alpha.png"
        rgba = np.full((16, 16, 4), 255, dtype=np.uint8)
        rgba[..., 3] = 0
        rgba[4:12, 3:13, 3] = 255
        Image.fromarray(rgba, "RGBA").save(alpha_path)
        alpha = read_silhouette(alpha_path)
        assert alpha.sum() == 80
        masked_path = Path(directory) / "masked.png"
        write_masked_reference(alpha_path, alpha, masked_path)
        with Image.open(masked_path) as masked:
            assert masked.mode == "RGBA" and np.asarray(masked.getchannel("A")).sum() == 80 * 255

        opaque_path = Path(directory) / "opaque.png"
        Image.new("RGB", (16, 16), "white").save(opaque_path)
        try:
            read_silhouette(opaque_path)
        except ValueError as error:
            assert "transparency" in str(error)
        else:
            raise AssertionError("opaque reference without mask was accepted")


if __name__ == "__main__":
    run()
    print("HUNYUAN_OMNI_MULTIVIEW_OK")
