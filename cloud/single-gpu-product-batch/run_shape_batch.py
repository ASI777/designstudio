#!/usr/bin/env python3
"""Load Hunyuan3D-2.1 once and bootstrap every product group."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", default="0b94677654c57bb9a6b6845cd7b704ccf551d327")
    args = parser.parse_args()
    import torch
    import trimesh
    from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

    if torch.version.hip is None or torch.cuda.device_count() != 1:
        raise RuntimeError("exactly one ROCm GPU is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-2.1", revision=args.revision
    )
    results = []
    for group in manifest["groups"]:
        directory = args.output / group["slug"]
        directory.mkdir(parents=True, exist_ok=True)
        primary = next(
            image for image in group["images"]
            if image["index"] == group["primary_index"]
        )
        source = args.root / primary["path"]
        started = time.time()
        generator = torch.Generator(device="cuda").manual_seed(int(group["seed"]))
        mesh = pipeline(
            image=str(source),
            num_inference_steps=50,
            octree_resolution=384,
            guidance_scale=5.0,
            generator=generator,
        )[0]
        path = directory / "bootstrap.glb"
        mesh.export(path)
        loaded = trimesh.load(path, force="mesh")
        if loaded.is_empty or not len(loaded.faces):
            raise RuntimeError(f"{group['slug']}: empty bootstrap mesh")
        result = {
            "slug": group["slug"], "seed": group["seed"],
            "primary_image": primary["path"],
            "primary_image_sha256": primary["sha256"],
            "bootstrap_glb": path.name, "bootstrap_glb_sha256": sha(path),
            "vertices": int(len(loaded.vertices)), "faces": int(len(loaded.faces)),
            "watertight": bool(loaded.is_watertight),
            "started_at_unix": started, "finished_at_unix": time.time(),
            "authority_status": "visualization_only",
        }
        (directory / "bootstrap-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
    summary = {
        "schema": "design-studio.product-shape-batch-result/1",
        "model_revision": args.revision, "results": results,
    }
    (args.output / "shape-batch-result.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
