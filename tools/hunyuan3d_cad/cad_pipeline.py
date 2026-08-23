#!/usr/bin/env python3
"""Prepare CAD data, synthesize package meshes, recover scale, and evaluate output."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .meshlib import (
        bounds,
        box_triangles,
        nearest_distances,
        normalize_unit_cube,
        read_stl,
        render_preview,
        sample_surface,
        watertight_report,
        write_obj,
        write_stl,
    )
except ImportError:
    from meshlib import (  # type: ignore
        bounds,
        box_triangles,
        nearest_distances,
        normalize_unit_cube,
        read_stl,
        render_preview,
        sample_surface,
        watertight_report,
        write_obj,
        write_stl,
    )

CAD_SUFFIXES = {".step", ".stp", ".stl"}


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or "part"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / Path(name).name
    counter = 2
    while candidate.exists():
        candidate = directory / f"{Path(name).stem}-{counter}{Path(name).suffix.lower()}"
        counter += 1
    return candidate


def collect_download_cad(downloads: Path, raw_root: Path) -> list[dict[str, Any]]:
    """Copy only CAD payloads from the known seed sources; never mutate Downloads."""
    raw_root.mkdir(parents=True, exist_ok=True)
    groups: list[dict[str, Any]] = []

    archives = sorted(downloads.glob("ul_*.zip")) + sorted(downloads.glob("EVAL-ADXL*-STEP.zip"))
    for archive in archives:
        archive_files: list[Path] = []
        archive_root = raw_root / slug(archive.stem)
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                suffix = Path(info.filename).suffix.lower()
                if info.is_dir() or suffix not in CAD_SUFFIXES:
                    continue
                destination = unique_destination(archive_root, Path(info.filename).name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                archive_files.append(destination)
        stems: dict[str, list[Path]] = {}
        for path in archive_files:
            stems.setdefault(slug(path.stem), []).append(path)
        for stem, paths in sorted(stems.items()):
            groups.append(
                {
                    "id": f"{slug(archive.stem)}-{stem}",
                    "origin": str(archive),
                    "files": paths,
                }
            )

    esp3d = downloads / "esp32-with-usbc-main" / "3D"
    if esp3d.is_dir():
        for source in sorted(path for path in esp3d.iterdir() if path.suffix.lower() in CAD_SUFFIXES):
            destination_root = raw_root / "esp32-with-usbc"
            destination_root.mkdir(parents=True, exist_ok=True)
            destination = unique_destination(destination_root, source.name)
            shutil.copyfile(source, destination)
            groups.append({"id": f"esp32-{slug(source.stem)}", "origin": str(source), "files": [destination]})

    for source in sorted(downloads.glob("*.stl")):
        destination_root = raw_root / "direct"
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(destination_root, source.name)
        shutil.copyfile(source, destination)
        groups.append({"id": f"direct-{slug(source.stem)}", "origin": str(source), "files": [destination]})

    seen: dict[str, int] = {}
    for group in groups:
        base = group["id"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            group["id"] = f"{base}-{seen[base]}"
    return groups


def collect_cad_tree(source_root: Path, raw_root: Path) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for source in sorted(path for path in source_root.rglob("*") if path.suffix.lower() in CAD_SUFFIXES):
        relative_stem = source.relative_to(source_root).with_suffix("")
        key = relative_stem.as_posix().lower()
        group = grouped.setdefault(
            key,
            {
                "id": slug(relative_stem.as_posix()),
                "origin": str(source_root),
                "files": [],
            },
        )
        destination_root = raw_root / relative_stem.parent
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / source.name
        shutil.copyfile(source, destination)
        group["files"].append(destination)
    return list(grouped.values())


def convert_step(step_path: Path, stl_path: Path, converter: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(converter), str(step_path), str(stl_path), "0.02"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def command_inventory(args: argparse.Namespace) -> int:
    downloads = args.downloads.resolve()
    output = args.output.resolve()
    source_root = args.source_tree.resolve() if args.source_tree else downloads
    groups = (
        collect_cad_tree(source_root, output / "raw")
        if args.source_tree
        else collect_download_cad(downloads, output / "raw")
    )
    normalized_root = output / "preprocessed"
    entries: list[dict[str, Any]] = []

    for group in groups:
        entry: dict[str, Any] = {
            "id": group["id"],
            "split": args.split,
            "origin": group["origin"],
            "license": args.license,
            "source_files": [],
            "key_dimensions": {"pin_pitch_mm": None, "annotation_status": "not_annotated"},
        }
        for path in group["files"]:
            entry["source_files"].append(
                {
                    "path": str(path.relative_to(output)),
                    "format": path.suffix.lower().lstrip("."),
                    "sha256": sha256(path),
                }
            )

        steps = [path for path in group["files"] if path.suffix.lower() in {".step", ".stp"}]
        stls = [path for path in group["files"] if path.suffix.lower() == ".stl"]
        working_stl = output / "tessellated" / f"{group['id']}.stl"
        try:
            if steps and args.step_converter:
                working_stl.parent.mkdir(parents=True, exist_ok=True)
                converter_metadata = convert_step(steps[0], working_stl, args.step_converter.resolve())
                entry["source_units"] = "mm"
                entry["unit_evidence"] = "STEP units interpreted by Open CASCADE"
                entry["step_bbox"] = converter_metadata
            elif stls:
                working_stl.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(stls[0], working_stl)
                entry["source_units"] = "mm"
                entry["unit_evidence"] = "vendor STL convention; verify against datasheet before benchmark sign-off"
            else:
                raise RuntimeError("STEP converter is required and no STL fallback exists")

            triangles = read_stl(working_stl)
            normalized, transform = normalize_unit_cube(triangles)
            part_root = normalized_root / group["id"]
            geo_root = part_root / "geo_data"
            render_root = part_root / "render_cond"
            normalized_stl = geo_root / f"{group['id']}_normalized.stl"
            normalized_obj = geo_root / f"{group['id']}_watertight.obj"
            write_stl(normalized_stl, normalized, group["id"])
            write_obj(normalized_obj, normalized)
            samples = sample_surface(normalized, args.surface_samples, seed=0).astype(np.float16)
            np.savez(geo_root / f"{group['id']}_surface.npz", random_surface=samples, sharp_surface=samples[:0])
            for view, azimuth in enumerate((0, 90, 180, 270)):
                render_preview(normalized, render_root / f"{view:03d}.png", azimuth)
            entry.update(
                {
                    "status": "ready",
                    "reference_mesh_mm": str(working_stl.relative_to(output)),
                    "mesh": str(normalized_stl.relative_to(output)),
                    "hunyuan_mesh": str(normalized_obj.relative_to(output)),
                    "conditioning_images": [
                        str((render_root / f"{view:03d}.png").relative_to(output)) for view in range(4)
                    ],
                    "scale": transform,
                    "topology": watertight_report(normalized),
                }
            )
        except Exception as error:
            entry.update({"status": "failed", "error": str(error)})
        entries.append(entry)

    ready = sum(entry["status"] == "ready" for entry in entries)
    manifest = {
        "schema": "design-studio.hunyuan3d-dataset/1",
        "purpose": (
            "held-out evaluation only; do not train on these parts"
            if args.split == "eval"
            else "shape fine-tuning"
        ),
        "source_root": str(source_root),
        "license": args.license,
        "part_count": len(entries),
        "ready_count": ready,
        "parts": entries,
    }
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "eval_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}: {ready}/{len(entries)} parts ready")
    return 0 if ready == len(entries) else 1


def package_mesh(family: str, rng: random.Random) -> tuple[np.ndarray, dict[str, Any]]:
    triangles: list[np.ndarray] = []
    if family == "soic":
        pins_per_side = rng.choice((4, 7, 8, 10, 12, 14))
        pitch = rng.choice((0.5, 0.635, 0.8, 1.0, 1.27))
        body_l = (pins_per_side - 1) * pitch + rng.uniform(1.2, 2.2)
        body_w = rng.uniform(3.5, 8.0)
        body_h = rng.uniform(1.0, 2.4)
        overall_w = body_w + rng.uniform(1.5, 4.0)
        lead_w = min(pitch * 0.55, 0.65)
        lead_l = (overall_w - body_w) / 2 + 0.15
        lead_h = rng.uniform(0.08, 0.25)
        triangles.append(box_triangles((0, 0, body_h / 2), (body_l, body_w, body_h)))
        for side in (-1, 1):
            y = side * (body_w / 2 + lead_l / 2 - 0.08)
            for index in range(pins_per_side):
                x = (index - (pins_per_side - 1) / 2) * pitch
                triangles.append(box_triangles((x, y, lead_h / 2), (lead_w, lead_l, lead_h)))
        metadata = {
            "family": family,
            "pin_count": pins_per_side * 2,
            "pin_pitch_mm": pitch,
            "body_dimensions_mm": [body_l, body_w, body_h],
        }
    elif family == "qfn":
        pins_per_side = rng.choice((4, 5, 6, 8, 10, 12))
        pitch = rng.choice((0.4, 0.5, 0.65, 0.8))
        body_l = body_w = (pins_per_side - 1) * pitch + rng.uniform(1.0, 1.8)
        body_h = rng.uniform(0.55, 1.1)
        pad_l = rng.uniform(0.35, 0.8)
        pad_w = min(pitch * 0.55, 0.38)
        pad_h = 0.06
        triangles.append(box_triangles((0, 0, body_h / 2 + pad_h), (body_l, body_w, body_h)))
        for side in (-1, 1):
            for index in range(pins_per_side):
                offset = (index - (pins_per_side - 1) / 2) * pitch
                triangles.append(box_triangles((offset, side * (body_w - pad_l) / 2, pad_h / 2), (pad_w, pad_l, pad_h)))
                triangles.append(box_triangles((side * (body_l - pad_l) / 2, offset, pad_h / 2), (pad_l, pad_w, pad_h)))
        metadata = {
            "family": family,
            "pin_count": pins_per_side * 4,
            "pin_pitch_mm": pitch,
            "body_dimensions_mm": [body_l, body_w, body_h],
        }
    else:
        imperial = rng.choice(("0201", "0402", "0603", "0805", "1206"))
        nominal = {
            "0201": (0.6, 0.3),
            "0402": (1.0, 0.5),
            "0603": (1.6, 0.8),
            "0805": (2.0, 1.25),
            "1206": (3.2, 1.6),
        }[imperial]
        length = nominal[0] * rng.uniform(0.92, 1.08)
        width = nominal[1] * rng.uniform(0.92, 1.08)
        height = width * rng.uniform(0.45, 0.85)
        terminal = length * rng.uniform(0.16, 0.25)
        triangles.append(box_triangles((0, 0, height / 2), (length - 2 * terminal, width, height)))
        for side in (-1, 1):
            triangles.append(
                box_triangles((side * (length - terminal) / 2, 0, height / 2), (terminal, width, height * 0.92))
            )
        metadata = {
            "family": "chip_smd",
            "variant": imperial,
            "pin_count": 2,
            "pin_pitch_mm": length - terminal,
            "body_dimensions_mm": [length, width, height],
        }
    merged = np.concatenate(triangles)
    _, _, dimensions = bounds(merged)
    metadata["overall_dimensions_mm"] = dimensions.tolist()
    return merged, metadata


def command_generate(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    rng = random.Random(args.seed)
    families = ("soic", "qfn", "chip_smd")
    entries: list[dict[str, Any]] = []
    for index in range(args.count):
        uid = f"ipc-{index:06d}"
        triangles_mm, metadata = package_mesh(families[index % len(families)], rng)
        normalized, transform = normalize_unit_cube(triangles_mm)
        part_root = output / "preprocessed" / uid
        geo_root = part_root / "geo_data"
        render_root = part_root / "render_cond"
        write_obj(geo_root / f"{uid}_watertight.obj", normalized)
        write_stl(geo_root / f"{uid}_normalized.stl", normalized, uid)
        samples = sample_surface(normalized, args.surface_samples, seed=args.seed + index).astype(np.float16)
        np.savez(geo_root / f"{uid}_surface.npz", random_surface=samples, sharp_surface=samples[:0])
        for view, azimuth in enumerate((0, 90, 180, 270)):
            render_preview(normalized, render_root / f"{view:03d}.png", azimuth)
        entry = {
            "id": uid,
            "split": "train" if index % 20 else "validation",
            **metadata,
            "normalization": transform,
            "topology": watertight_report(normalized),
            "preprocessed_dir": str(part_root.relative_to(output)),
        }
        (part_root / "dimensions.json").write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
        entries.append(entry)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "procedural_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "design-studio.hunyuan3d-dataset/1",
                "generator": "IPC-7351-inspired randomized package primitives",
                "seed": args.seed,
                "part_count": len(entries),
                "parts": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {manifest_path}: {len(entries)} procedural parts")
    return 0


def command_scale(args: argparse.Namespace) -> int:
    triangles = read_stl(args.input)
    minimum, maximum, current = bounds(triangles)
    target = np.asarray(args.dimensions_mm, dtype=np.float64)
    if np.any(target <= 0) or np.any(current <= 0):
        raise ValueError("source and target dimensions must be positive")
    center = (minimum + maximum) / 2
    scaled = (triangles - center) * (target / current)
    write_stl(args.output, scaled, "dimensionally-scaled")
    report = {
        "input": str(args.input),
        "output": str(args.output),
        "source_dimensions": current.tolist(),
        "target_dimensions_mm": target.tolist(),
        "axis_scale": (target / current).tolist(),
        "translation": "bbox center moved to origin",
    }
    metadata_path = Path(args.output).with_suffix(".scale.json")
    metadata_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def mesh_metrics(reference: np.ndarray, prediction: np.ndarray, samples: int, threshold: float) -> dict[str, Any]:
    reference_points = sample_surface(reference, samples, seed=41)[:, :3]
    prediction_points = sample_surface(prediction, samples, seed=43)[:, :3]
    reference_to_prediction = nearest_distances(reference_points, prediction_points)
    prediction_to_reference = nearest_distances(prediction_points, reference_points)
    ref_min, ref_max, ref_dims = bounds(reference)
    pred_min, pred_max, pred_dims = bounds(prediction)
    del ref_min, ref_max, pred_min, pred_max
    precision = float(np.mean(prediction_to_reference <= threshold))
    recall = float(np.mean(reference_to_prediction <= threshold))
    return {
        "chamfer_l1_mm": float(reference_to_prediction.mean() + prediction_to_reference.mean()),
        "f_score": 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall),
        "f_score_threshold_mm": threshold,
        "precision": precision,
        "recall": recall,
        "reference_dimensions_mm": ref_dims.tolist(),
        "prediction_dimensions_mm": pred_dims.tolist(),
        "bbox_error_percent": (np.abs(pred_dims - ref_dims) / np.maximum(ref_dims, 1e-12) * 100).tolist(),
        "bbox_max_error_percent": float(np.max(np.abs(pred_dims - ref_dims) / np.maximum(ref_dims, 1e-12) * 100)),
    }


def command_evaluate(args: argparse.Namespace) -> int:
    result = mesh_metrics(read_stl(args.reference), read_stl(args.prediction), args.samples, args.threshold_mm)
    result.update({"reference": str(args.reference), "prediction": str(args.prediction)})
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def command_evaluate_manifest(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_root = manifest_path.parent
    rows: list[dict[str, Any]] = []
    for part in manifest["parts"]:
        if part.get("status") != "ready":
            continue
        reference_path = dataset_root / part.get("reference_mesh_mm", f"tessellated/{part['id']}.stl")
        reference = read_stl(reference_path)
        _, _, reference_dimensions = bounds(reference)
        for variant in args.variants:
            prediction_path = args.predictions_root / variant / f"{part['id']}.stl"
            row: dict[str, Any] = {"id": part["id"], "variant": variant}
            if not prediction_path.exists():
                row["status"] = "missing"
                rows.append(row)
                continue
            prediction = read_stl(prediction_path)
            if args.rescale_normalized:
                pred_min, pred_max, pred_dimensions = bounds(prediction)
                prediction = (prediction - (pred_min + pred_max) / 2) * (reference_dimensions / pred_dimensions)
            row.update(mesh_metrics(reference, prediction, args.samples, args.threshold_mm))
            row["status"] = "evaluated"
            pitch = part.get("key_dimensions", {}).get("pin_pitch_mm")
            sidecar = prediction_path.with_suffix(".json")
            if pitch is not None and sidecar.exists():
                prediction_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                predicted_pitch = prediction_metadata.get("pin_pitch_mm")
                if predicted_pitch is not None:
                    row["pin_pitch_error_percent"] = abs(float(predicted_pitch) - pitch) / pitch * 100
            rows.append(row)

    aggregate: dict[str, Any] = {}
    for variant in args.variants:
        evaluated = [row for row in rows if row["variant"] == variant and row["status"] == "evaluated"]
        aggregate[variant] = {
            "evaluated": len(evaluated),
            "missing": sum(row["variant"] == variant and row["status"] == "missing" for row in rows),
            "mean_chamfer_l1_mm": (
                float(np.mean([row["chamfer_l1_mm"] for row in evaluated])) if evaluated else None
            ),
            "mean_bbox_max_error_percent": (
                float(np.mean([row["bbox_max_error_percent"] for row in evaluated])) if evaluated else None
            ),
            "bbox_under_3_percent_rate": (
                float(np.mean([row["bbox_max_error_percent"] < 3 for row in evaluated])) if evaluated else None
            ),
        }
    report = {
        "schema": "design-studio.hunyuan3d-evaluation/1",
        "manifest": str(manifest_path),
        "variants": list(args.variants),
        "aggregate": aggregate,
        "parts": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, indent=2))
    return 0


def command_alpha_renders(args: argparse.Namespace) -> int:
    from PIL import Image

    paths = sorted(args.dataset.glob("preprocessed/*/render_cond/*.png"))
    for path in paths:
        rgb = np.asarray(Image.open(path).convert("RGB"))
        alpha = np.where(np.all(rgb >= args.background_threshold, axis=2), 0, 255).astype(np.uint8)
        rgba = np.concatenate((rgb, alpha[..., None]), axis=2)
        Image.fromarray(rgba, mode="RGBA").save(path)
    print(f"converted {len(paths)} conditioning renders to RGBA")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    inventory = commands.add_parser("inventory", help="extract and normalize held-out Downloads CAD")
    inventory.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    inventory.add_argument("--source-tree", type=Path, help="ingest all STEP/STP/STL files under this tree")
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument("--step-converter", type=Path)
    inventory.add_argument("--surface-samples", type=int, default=8192)
    inventory.add_argument("--split", choices=("train", "validation", "eval"), default="eval")
    inventory.add_argument("--license", default="user-provided")
    inventory.set_defaults(func=command_inventory)

    generate = commands.add_parser("generate-ipc", help="generate exact-dimension procedural packages")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--count", type=int, default=3000)
    generate.add_argument("--seed", type=int, default=20260709)
    generate.add_argument("--surface-samples", type=int, default=8192)
    generate.set_defaults(func=command_generate)

    scale = commands.add_parser("scale", help="anisotropically recover a generated mesh's absolute scale")
    scale.add_argument("input", type=Path)
    scale.add_argument("output", type=Path)
    scale.add_argument("--dimensions-mm", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    scale.set_defaults(func=command_scale)

    evaluate = commands.add_parser("evaluate", help="compute surface and bounding-box metrics")
    evaluate.add_argument("reference", type=Path)
    evaluate.add_argument("prediction", type=Path)
    evaluate.add_argument("--samples", type=int, default=20000)
    evaluate.add_argument("--threshold-mm", type=float, default=0.1)
    evaluate.add_argument("--output", type=Path)
    evaluate.set_defaults(func=command_evaluate)

    batch_eval = commands.add_parser("evaluate-manifest", help="compare baseline and fine-tuned predictions")
    batch_eval.add_argument("--manifest", type=Path, required=True)
    batch_eval.add_argument("--predictions-root", type=Path, required=True)
    batch_eval.add_argument("--variants", nargs="+", default=("pretrained", "finetuned"))
    batch_eval.add_argument("--rescale-normalized", action="store_true")
    batch_eval.add_argument("--samples", type=int, default=20000)
    batch_eval.add_argument("--threshold-mm", type=float, default=0.1)
    batch_eval.add_argument("--output", type=Path, required=True)
    batch_eval.set_defaults(func=command_evaluate_manifest)

    alpha = commands.add_parser("alpha-renders", help="add transparent backgrounds to existing RGB renders")
    alpha.add_argument("--dataset", type=Path, required=True)
    alpha.add_argument("--background-threshold", type=int, default=248)
    alpha.set_defaults(func=command_alpha_renders)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
