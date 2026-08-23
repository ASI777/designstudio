#!/usr/bin/env python3
"""Cache frozen ShapeVAE latents from Hunyuan surface-sample NPZ files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import torch
from hy3dshape.models.autoencoders import ShapeVAE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", default="tencent/Hunyuan3D-2.1")
    parser.add_argument(
        "--revision",
        default=os.environ.get(
            "DS_3D_MODEL_REVISION",
            "0b94677654c57bb9a6b6845cd7b704ccf551d327",
        ),
    )
    parser.add_argument("--points", type=int, default=81920)
    args = parser.parse_args()

    if not torch.cuda.is_available() or torch.version.hip is None:
        raise SystemExit("precompute_latents requires PyTorch ROCm")
    vae = ShapeVAE.from_pretrained(
        args.model, revision=args.revision,
        use_safetensors=False, variant="fp16",
    ).eval()
    rng = np.random.default_rng(0)
    files = sorted((args.dataset / "preprocessed").glob("*/geo_data/*_surface.npz"))
    if not files:
        raise SystemExit("no *_surface.npz files found")

    for index, source in enumerate(files, 1):
        destination = source.with_name(source.stem.replace("_surface", "_latent") + ".pt")
        if destination.exists():
            continue
        with np.load(source) as archive:
            surface = archive["random_surface"].astype(np.float32)
        selection = rng.choice(len(surface), size=args.points, replace=len(surface) < args.points)
        tensor = torch.from_numpy(surface[selection]).unsqueeze(0).to("cuda", dtype=torch.float16)
        with torch.inference_mode():
            latent = vae.encode(tensor)
        torch.save(latent.detach().cpu(), destination)
        print(f"[{index}/{len(files)}] {destination}")


if __name__ == "__main__":
    main()
