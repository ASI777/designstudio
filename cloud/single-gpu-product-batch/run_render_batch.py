#!/usr/bin/env python3
"""Render deterministic six-view inputs from every Shape bootstrap GLB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--renderer", type=Path, required=True)
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--resolution", type=int, default=768)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    for group in manifest["groups"]:
        directory = args.runs / group["slug"]
        mesh = directory / "bootstrap.glb"
        output = directory / "bootstrap-views"
        expected = output / "silhouette-manifest.json"
        if expected.is_file():
            print(f"RENDER_RESUME {group['slug']}", flush=True)
            results.append({"slug": group["slug"], "status": "resumed"})
            continue
        if not mesh.is_file():
            raise FileNotFoundError(mesh)
        subprocess.run(
            [
                sys.executable,
                str(args.renderer),
                "--mesh",
                str(mesh),
                "--output",
                str(output),
                "--resolution",
                str(args.resolution),
                "--blender",
                args.blender,
                "--vehicle-axis-layout",
                "glb-y-up",
            ],
            check=True,
        )
        if not expected.is_file():
            raise RuntimeError(f"{group['slug']}: renderer produced no manifest")
        results.append({"slug": group["slug"], "status": "completed"})
        print(f"RENDER_COMPLETE {group['slug']}", flush=True)
    (args.runs / "render-batch-result.json").write_text(
        json.dumps(
            {
                "schema": "design-studio.product-render-batch-result/1",
                "resolution": args.resolution,
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("RENDER_BATCH_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
