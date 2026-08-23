#!/usr/bin/env python3
"""Mandatory ROCm gate: run pretrained shape inference before training."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch
import trimesh
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("/tmp/hunyuan3d-rocm-gate.glb"))
    parser.add_argument("--model", default="tencent/Hunyuan3D-2.1")
    parser.add_argument(
        "--revision",
        default=os.environ.get(
            "DS_3D_MODEL_REVISION",
            "0b94677654c57bb9a6b6845cd7b704ccf551d327",
        ),
    )
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("NO-GO: PyTorch cannot see a ROCm GPU")
    if torch.version.hip is None:
        raise SystemExit("NO-GO: this is not a ROCm PyTorch build")
    device_name = torch.cuda.get_device_name(0)
    print(f"ROCm {torch.version.hip}; device={device_name}")

    pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        args.model, revision=args.revision
    )
    mesh = pipeline(image=str(args.image))[0]
    mesh.export(args.output)
    loaded = trimesh.load(args.output, force="mesh")
    if loaded.is_empty or len(loaded.faces) == 0 or not loaded.is_watertight:
        raise SystemExit("NO-GO: inference output is empty or non-watertight")
    print(f"GO: {args.output} vertices={len(loaded.vertices)} faces={len(loaded.faces)}")


if __name__ == "__main__":
    main()
