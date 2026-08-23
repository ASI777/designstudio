#!/usr/bin/env python3
"""Fit, tessellate, and exact-host reconstruct every completed Omni mesh."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("RUN " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--fitter", type=Path, required=True)
    parser.add_argument("--preview-exporter", type=Path, required=True)
    parser.add_argument("--freecad-reconstructor", type=Path, required=True)
    parser.add_argument("--freecadcmd", required=True)
    parser.add_argument("--slug")
    parser.add_argument("--preview-resolution", type=int, default=12)
    parser.add_argument("--control-count", type=int, default=10)
    parser.add_argument("--maximum-patches", type=int, default=4096)
    parser.add_argument("--fairness", type=float, default=1e-8)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    groups = manifest["groups"]
    if args.slug:
        groups = [group for group in groups if group["slug"] == args.slug]
        if not groups:
            parser.error(f"unknown product slug: {args.slug}")
    for group in groups:
        directory = args.runs / group["slug"]
        source = directory / "omni.glb"
        if not source.is_file():
            source = directory / "bootstrap.glb"
        if not source.is_file():
            raise FileNotFoundError(directory / "omni.glb")
        fit = directory / "fit-result.json"
        preview = directory / "cad-preview.glb"
        validation = directory / "host-validation.json"
        if not fit.is_file():
            run(
                [
                    sys.executable,
                    str(args.fitter),
                    "--input",
                    str(source),
                    "--output-directory",
                    str(directory),
                    "--control-u",
                    str(args.control_count),
                    "--control-v",
                    str(args.control_count),
                    "--maximum-patches",
                    str(args.maximum_patches),
                    "--fairness",
                    str(args.fairness),
                ]
            )
        if not preview.is_file():
            run(
                [
                    sys.executable,
                    str(args.preview_exporter),
                    "--fit",
                    str(fit),
                    "--output",
                    str(preview),
                    "--resolution",
                    str(args.preview_resolution),
                ]
            )
        if not validation.is_file():
            run(
                [
                    sys.executable,
                    str(args.freecad_reconstructor),
                    "--fit",
                    str(fit),
                    "--output-directory",
                    str(directory),
                    "--freecadcmd",
                    args.freecadcmd,
                ]
            )
        fit_data = json.loads(fit.read_text(encoding="utf-8"))
        validation_data = json.loads(validation.read_text(encoding="utf-8"))
        result = {
            "slug": group["slug"],
            "source_glb": source.name,
            "source_glb_sha256": sha256(source),
            "fit_result_sha256": sha256(fit),
            "cad_preview_sha256": sha256(preview),
            "host_validation_sha256": sha256(validation),
            "deviation_metrics": fit_data["deviation_metrics"],
            "valid_outer_shell": validation_data["valid_outer_shell"],
            "ap242_roundtrip_valid": validation_data["ap242_roundtrip_valid"],
            "continuity_verified": validation_data["continuity_verified"],
            "release_eligible": validation_data["release_eligible"],
        }
        results.append(result)
        print(
            f"CAD_COMPLETE {group['slug']} "
            f"release_eligible={result['release_eligible']}",
            flush=True,
        )
    summary = {
        "schema": "design-studio.product-cad-batch-result/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "authority": (
            "deterministic_candidate; release requires every recorded gate"
        ),
        "results": results,
    }
    path = args.runs / (
        f"cad-result-{args.slug}.json" if args.slug else "cad-batch-result.json"
    )
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"CAD_BATCH_COMPLETE {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
