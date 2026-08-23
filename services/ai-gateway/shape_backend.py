"""Hunyuan3D shape-only backend with a deterministic CPU mock."""

from __future__ import annotations

import base64
import io
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ShapeResult:
    data: bytes
    format: str
    dimensions_mm: tuple[float, float, float]
    coordinate_unit: str


def hunyuan_native_target_m(
    dimensions_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Map public Length/Width/Height to Hunyuan X-width/Y-height/Z-length."""
    length, width, height = dimensions_mm
    return (width / 1000.0, height / 1000.0, length / 1000.0)


def _mock_box_stl(dimensions: tuple[float, float, float]) -> bytes:
    hx, hy, hz = (value / 2 for value in dimensions)
    vertices = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    output = io.BytesIO()
    output.write(b"Design Studio mock dimensioned box".ljust(80, b"\0"))
    output.write(struct.pack("<I", len(faces)))
    for face in faces:
        output.write(struct.pack("<3f", 0, 0, 0))
        for index in face:
            output.write(struct.pack("<3f", *vertices[index]))
        output.write(struct.pack("<H", 0))
    return output.getvalue()


class HunyuanShapeBackend:
    """Lazy-load the shape pipeline only when real inference is requested."""

    def __init__(self, mock: bool):
        self.mock = mock
        self.model_id = os.environ.get("DS_3D_MODEL", "tencent/Hunyuan3D-2.1")
        self.model_revision = os.environ.get(
            "DS_3D_MODEL_REVISION",
            "0b94677654c57bb9a6b6845cd7b704ccf551d327",
        )
        self._pipeline = None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        root = Path(os.environ.get("HUNYUAN3D_ROOT", "/opt/Hunyuan3D-2.1"))
        sys.path.insert(0, str(root / "hy3dshape"))
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

        self._pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            self.model_id, revision=self.model_revision
        )
        adapter_path = os.environ.get("DS_3D_LORA")
        if adapter_path:
            from peft import PeftModel

            adapted = PeftModel.from_pretrained(self._pipeline.model, adapter_path)
            self._pipeline.model = adapted.merge_and_unload()
        return self._pipeline

    def generate(
        self,
        image_base64: str | None,
        dimensions_mm: tuple[float, float, float],
        seed: int,
    ) -> ShapeResult:
        if self.mock:
            return ShapeResult(_mock_box_stl(dimensions_mm), "stl", dimensions_mm, "mm")
        if not image_base64:
            raise ValueError("image_base64 is required outside mock mode")

        image_bytes = base64.b64decode(image_base64, validate=True)
        if len(image_bytes) > 16 * 1024 * 1024:
            raise ValueError("conditioning image exceeds 16 MiB")

        from PIL import Image
        import numpy as np
        import torch

        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        generator = torch.Generator(device="cuda").manual_seed(seed)
        mesh = self._load()(image=image, generator=generator)[0]

        source_dimensions = np.asarray(mesh.bounds[1] - mesh.bounds[0], dtype=np.float64)
        # glTF/GLB coordinates are metres by specification.
        # The public CAD contract is vehicle length, overall width, height.
        # Hunyuan's canonical output axes are X=width, Y=height, Z=length.
        # Scale in native mesh order while retaining the public dimensions in
        # the response and provenance record.
        target_dimensions = np.asarray(
            hunyuan_native_target_m(dimensions_mm), dtype=np.float64
        )
        if np.any(source_dimensions <= 0):
            raise RuntimeError("generated mesh has an invalid bounding box")
        mesh.apply_translation(-mesh.bounds.mean(axis=0))
        mesh.apply_scale(target_dimensions / source_dimensions)

        with tempfile.NamedTemporaryFile(suffix=".glb") as artifact:
            mesh.export(artifact.name)
            artifact.seek(0)
            return ShapeResult(artifact.read(), "glb", dimensions_mm, "m")
