#!/usr/bin/env python3
"""Refine every bootstrap mesh with one pinned Hunyuan3D-Omni pipeline."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time


CARDINAL_VIEWS = ("front", "rear", "left", "right", "top", "bottom")
SCHEMA = "design-studio.product-omni-batch-result/1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def embedded(path: Path, kind: str) -> dict[str, str]:
    data = path.read_bytes()
    return {
        f"{kind}_base64": base64.b64encode(data).decode("ascii"),
        f"{kind}_sha256": hashlib.sha256(data).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--omni-source", type=Path, required=True)
    parser.add_argument(
        "--revision",
        default="70e803bfb4e127d534049d8ab8c8cb511780d485",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.omni_source.resolve()))
    os.environ["DS_OMNI_MODEL_REVISION"] = args.revision

    import torch
    import trimesh
    from omni_runner import generate_omni_candidate, load_omni_pipeline

    if torch.version.hip is None or torch.cuda.device_count() != 1:
        raise RuntimeError("exactly one ROCm GPU is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pipeline = load_omni_pipeline()
    results = []
    for group in manifest["groups"]:
        directory = args.runs / group["slug"]
        existing_result = directory / "omni-result.json"
        if existing_result.is_file():
            existing = json.loads(existing_result.read_text(encoding="utf-8"))
            existing_mesh = directory / str(existing.get("omni_glb", "omni.glb"))
            if (
                existing.get("group_sha256") == group["group_sha256"]
                and existing.get("model_revision") == args.revision
                and existing.get("visual_hull_resolution") == 128
                and existing_mesh.is_file()
                and sha256(existing_mesh) == existing.get("omni_glb_sha256")
            ):
                results.append(existing)
                print(f"OMNI_RESUME {group['slug']}", flush=True)
                continue
        views = directory / "bootstrap-views"
        silhouette_manifest_path = views / "silhouette-manifest.json"
        brief_path = directory / "llm-brief.json"
        if not silhouette_manifest_path.is_file():
            raise FileNotFoundError(silhouette_manifest_path)
        if not brief_path.is_file():
            raise FileNotFoundError(brief_path)
        silhouette_manifest = json.loads(
            silhouette_manifest_path.read_text(encoding="utf-8")
        )
        by_view = {
            item["view"]: item for item in silhouette_manifest["views"]
        }
        if set(by_view) != set(CARDINAL_VIEWS):
            raise RuntimeError(f"{group['slug']}: incomplete cardinal views")

        references = []
        for view in CARDINAL_VIEWS:
            image_path = views / by_view[view]["image"]
            mask_path = views / by_view[view]["mask"]
            if sha256(image_path) != by_view[view]["image_sha256"]:
                raise RuntimeError(f"{group['slug']}: {view} image digest mismatch")
            if sha256(mask_path) != by_view[view]["mask_sha256"]:
                raise RuntimeError(f"{group['slug']}: {view} mask digest mismatch")
            reference = {"view": view}
            reference.update(embedded(image_path, "image"))
            reference.update(embedded(mask_path, "mask"))
            references.append(reference)

        output = directory / "omni.glb"
        started = time.time()
        package = {
            "schema": "design-studio.omni-conditioning-package/1",
            "group_slug": group["slug"],
            "group_sha256": group["group_sha256"],
            "dimensions_mm": group["dimensions_mm"],
            "reference_images": references,
            "point_controls": [],
            "voxel_keepouts": [],
            "llm_brief_sha256": sha256(brief_path),
            "silhouette_manifest_sha256": sha256(silhouette_manifest_path),
            "authority": "review_only_conditioning",
        }
        attempt_errors = []
        actual_seed = None
        profiles = (
            (0, 0.0, 4.5),
            (1_000_003, -1.0 / 512.0, 3.5),
            (2_000_033, 1.0 / 512.0, 5.5),
            (3_000_091, -2.0 / 512.0, 4.5),
        )
        successful_profile = None
        for offset, mc_level, guidance_scale in profiles:
            candidate_seed = int(group["seed"]) + offset
            try:
                generate_omni_candidate(
                    package=package,
                    seed=candidate_seed,
                    output_path=output,
                    pipeline=pipeline,
                    mc_level=mc_level,
                    guidance_scale=guidance_scale,
                )
                actual_seed = candidate_seed
                successful_profile = {
                    "mc_level": mc_level,
                    "guidance_scale": guidance_scale,
                }
                break
            except (RuntimeError, NameError, ValueError) as error:
                attempt_errors.append(
                    {
                        "seed": candidate_seed,
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "mc_level": mc_level,
                        "guidance_scale": guidance_scale,
                    }
                )
                output.unlink(missing_ok=True)
                print(
                    f"OMNI_RETRY {group['slug']} seed={candidate_seed} "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )
        fallback = actual_seed is None
        if fallback:
            bootstrap = directory / "bootstrap.glb"
            if not bootstrap.is_file():
                raise RuntimeError(
                    f"{group['slug']}: all deterministic Omni attempts failed "
                    f"and no bootstrap fallback exists: {attempt_errors}"
                )
            shutil.copyfile(bootstrap, output)
            actual_seed = int(group["seed"])
            successful_profile = {
                "fallback_source": bootstrap.name,
            }
            print(
                f"OMNI_FALLBACK {group['slug']} {bootstrap.name}",
                flush=True,
            )
        loaded = trimesh.load(output, force="mesh")
        if loaded.is_empty or not len(loaded.faces):
            raise RuntimeError(f"{group['slug']}: empty Omni mesh")
        result = {
            "schema": "design-studio.product-omni-result/1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "slug": group["slug"],
            "requested_seed": int(group["seed"]),
            "seed": actual_seed,
            "generation_attempts": attempt_errors + [
                {
                    "seed": actual_seed,
                    "status": (
                        "fallback_bootstrap_after_omni_failure"
                        if fallback else "completed"
                    ),
                    **successful_profile,
                }
            ],
            "group_sha256": group["group_sha256"],
            "model": (
                "tencent/Hunyuan3D-2.1"
                if fallback else "tencent/Hunyuan3D-Omni"
            ),
            "model_revision": args.revision,
            "visual_hull_resolution": 128,
            "omni_generation_succeeded": not fallback,
            "llm_brief_sha256": package["llm_brief_sha256"],
            "silhouette_manifest_sha256": package[
                "silhouette_manifest_sha256"
            ],
            "omni_glb": output.name,
            "omni_glb_sha256": sha256(output),
            "vertices": int(len(loaded.vertices)),
            "faces": int(len(loaded.faces)),
            "watertight": bool(loaded.is_watertight),
            "started_at_unix": started,
            "finished_at_unix": time.time(),
            "authority_status": "visualization_only",
        }
        (directory / "omni-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        results.append(result)
        print(f"OMNI_COMPLETE {group['slug']} {result['faces']}", flush=True)

    summary = {
        "schema": SCHEMA,
        "model_revision": args.revision,
        "results": results,
    }
    (args.runs / "omni-batch-result.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("OMNI_BATCH_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
