"""Pinned Hunyuan3D-Omni adapter loaded only inside the ROCm container."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


from multiview import (CARDINAL_VIEWS, read_silhouette, visual_hull_surface,
                       write_masked_reference)


def _download_image(reference: dict[str, Any], kind: str) -> Path:
    url = reference.get(f"{kind}_url")
    embedded = reference.get(f"{kind}_base64")
    expected_digest = reference.get(f"{kind}_sha256")
    if embedded is not None:
        data = base64.b64decode(embedded, validate=True)
        if len(data) > 16 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"embedded {kind} is not an accepted PNG")
        if expected_digest is None or hashlib.sha256(data).hexdigest() != expected_digest:
            raise ValueError(f"embedded {kind} digest mismatch")
        temporary = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        temporary.write(data)
        temporary.close()
        return Path(temporary.name)
    if not url:
        raise ValueError(f"conditioning reference has no {kind}")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("conditioning images require signed HTTPS URLs")
    request = Request(url, headers={"User-Agent": "DesignStudio-Hunyuan/1"})
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError("conditioning image download failed")
        media_type = response.headers.get_content_type()
        if not media_type.startswith("image/"):
            raise ValueError("conditioning URL did not return an image")
        data = response.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("conditioning image exceeds 16 MiB")
    temporary = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temporary.write(data)
    temporary.close()
    return Path(temporary.name)


def _normalized_points(package: dict[str, Any]):
    import torch
    dimensions_map = package["dimensions_mm"]
    dimensions = [dimensions_map["width"], dimensions_map["depth"], dimensions_map["height"]]
    values = []
    for control in package["point_controls"]:
        point = control["position_mm"]
        values.append([max(-1.0, min(1.0, 2.0 * point[i] / dimensions[i]))
                       for i in range(3)])
    return torch.tensor(values, dtype=torch.float32).unsqueeze(0) if values else None


def _allowed_volume_samples(package: dict[str, Any]):
    """Convert exact keep-outs to a deterministic allowed-volume point control."""
    import torch
    dimensions_map = package["dimensions_mm"]
    dimensions = [dimensions_map["width"], dimensions_map["depth"], dimensions_map["height"]]
    keepouts = package["voxel_keepouts"]
    points = []
    resolution = 16
    for ix in range(resolution):
        for iy in range(resolution):
            for iz in range(resolution):
                normalized = [(index + 0.5) / resolution * 2.0 - 1.0
                              for index in (ix, iy, iz)]
                millimetres = [normalized[i] * dimensions[i] / 2.0 for i in range(3)]
                excluded = any(all(region["minimum_mm"][i] <= millimetres[i]
                                   <= region["maximum_mm"][i] for i in range(3))
                               for region in keepouts)
                # Boundary and material around a keepout guide the surface encoder.
                boundary = ix in (0, resolution - 1) or iy in (0, resolution - 1) \
                    or iz in (0, resolution - 1)
                if boundary and not excluded:
                    points.append(normalized)
    return torch.tensor(points, dtype=torch.float32).unsqueeze(0) if points else None


def load_omni_pipeline():
    """Load the pinned model once for a sequential single-GPU batch."""
    import torch
    from hy3dshape.pipelines import Hunyuan3DOmniSiTFlowMatchingPipeline

    if torch.version.hip is None or not torch.cuda.is_available():
        raise RuntimeError("Hunyuan3D-Omni requires a visible ROCm GPU")
    return Hunyuan3DOmniSiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-Omni",
        revision=os.environ.get(
            "DS_OMNI_MODEL_REVISION",
            "70e803bfb4e127d534049d8ab8c8cb511780d485",
        ),
        fast_decode=False,
    )


def generate_omni_candidate(*, package: dict[str, Any], seed: int,
                            output_path: Path, pipeline=None,
                            mc_level: float = 0.0,
                            guidance_scale: float = 4.5) -> None:
    import numpy as np
    import torch
    import trimesh
    from hy3dshape.postprocessors import FloaterRemover, DegenerateFaceRemover

    if torch.version.hip is None or not torch.cuda.is_available():
        raise RuntimeError("Hunyuan3D-Omni requires a visible ROCm GPU")
    references = {reference["view"]: reference for reference in package["reference_images"]}
    if set(references) != set(CARDINAL_VIEWS):
        raise ValueError("six unique cardinal reference images are required")
    downloads: list[Path] = []
    try:
        masks = {}
        images = {}
        for view in CARDINAL_VIEWS:
            reference = references[view]
            image_path = _download_image(reference, "image")
            downloads.append(image_path)
            mask_path = None
            if reference.get("mask_url") or reference.get("mask_base64"):
                mask_path = _download_image(reference, "mask")
                downloads.append(mask_path)
            images[view] = image_path
            masks[view] = read_silhouette(image_path, mask_path)
        # Thin structures (bicycle tubes, headphone bands, grille ribs) can
        # disappear entirely on the historical 48^3 fusion lattice.  The
        # pinned contract permits up to 128^3, which preserves those features
        # while remaining tiny relative to MI300X HBM.
        fused_surface = visual_hull_surface(masks, resolution=128)
        masked_front_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        masked_front_file.close()
        masked_front = Path(masked_front_file.name)
        downloads.append(masked_front)
        write_masked_reference(images["front"], masks["front"], masked_front)

        if pipeline is None:
            pipeline = load_omni_pipeline()
        dimension_map = package["dimensions_mm"]
        dimensions = np.asarray([dimension_map["width"], dimension_map["depth"],
                                 dimension_map["height"]], dtype=np.float32)
        # The upstream pipeline moves image conditioning to its configured
        # device, but passes caller-supplied geometric controls through
        # unchanged.  Keep the visual hull on the same accelerator and dtype
        # as the condition encoder to avoid a CPU/ROCm cross-device operation.
        voxel = torch.from_numpy(fused_surface).to(
            device="cuda", dtype=torch.float16
        ).unsqueeze(0)
        # Omni can consume only one geometric control.  The six-view visual hull
        # is authoritative; explicit point/keep-out fields remain evidence until
        # a deterministic compositor combines them without weakening silhouettes.
        result = pipeline(
            image=str(masked_front), voxel=voxel,
            num_inference_steps=50, octree_resolution=512, mc_level=mc_level,
            guidance_scale=guidance_scale,
            generator=torch.Generator("cuda").manual_seed(seed),
        )
        mesh = DegenerateFaceRemover()(FloaterRemover()(result["shapes"][0][0]))
        current = np.asarray(mesh.bounds[1] - mesh.bounds[0], dtype=np.float64)
        if np.any(current <= 0):
            raise RuntimeError("generated mesh has an invalid bounding box")
        mesh.apply_translation(-mesh.bounds.mean(axis=0))
        mesh.apply_scale((dimensions / 1000.0) / current)  # GLB uses metres
        mesh.export(output_path)
    finally:
        for path in downloads:
            path.unlink(missing_ok=True)
