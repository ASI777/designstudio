#!/usr/bin/env python3
"""Stage the supplied screenshot set as digest-bound multiview product groups."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil

from PIL import Image


GROUPS = [
    ("camera", "compact premium mirrorless camera", range(0, 5), 2, [140, 50, 95]),
    ("speaker", "modular audiophile speaker and control console", range(5, 12), 5, [450, 400, 650]),
    ("hair-device", "handheld heated hair styling appliance", range(12, 18), 12, [80, 80, 260]),
    ("cargo-bike", "three-wheel electric cargo bicycle", range(18, 25), 23, [2100, 850, 1200]),
    ("coffee-appliance", "compact modular coffee dosing and pouring appliance", range(25, 34), 25, [180, 180, 280]),
    ("green-ebike", "step-through shared electric bicycle", range(34, 43), 34, [1800, 650, 1150]),
    ("scanner", "handheld phone-connected optical scanner and dock", range(43, 50), 49, [80, 35, 160]),
    ("headphones", "premium over-ear headphones with sculpted lattice headband", range(50, 55), 50, [190, 85, 210]),
    ("skincare-brush", "foaming handheld facial cleansing brush", range(55, 68), 56, [85, 85, 210]),
    ("silver-dock", "modular silver handheld imaging device and desktop dock", range(68, 79), 71, [220, 90, 70]),
    ("rescue-helmet", "red white and black connected alpine rescue helmet", range(79, 90), 79, [310, 250, 240]),
    ("controller", "modular tactile smart controller and charging base", range(90, 98), 90, [190, 110, 35]),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attachment", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    source_paths = [Path(value) for value in shlex.split(
        args.attachment.read_text(encoding="utf-8")
    )]
    if len(source_paths) != 98 or any(not path.is_file() for path in source_paths):
        raise RuntimeError("the expected set of 98 screenshots is not intact")
    output = args.output_directory.resolve()
    images_dir = output / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for index, source in enumerate(source_paths):
        destination = images_dir / f"image-{index:03d}.png"
        shutil.copy2(source, destination)
        with Image.open(destination) as image:
            width, height = image.size
        staged.append({
            "index": index, "path": f"images/{destination.name}",
            "source_name": source.name, "sha256": sha(destination),
            "width": width, "height": height,
        })
    groups = []
    assigned: set[int] = set()
    for slug, label, indices, primary, dimensions in GROUPS:
        members = list(indices)
        assigned.update(members)
        group = {
            "slug": slug,
            "label": label,
            "indices": members,
            "primary_index": primary,
            "dimensions_mm": {
                "width": dimensions[0], "depth": dimensions[1],
                "height": dimensions[2],
            },
            "seed": 10_000 + members[0],
            "prompt": (
                f"Reconstruct one coherent {label} from every supplied view. "
                "Preserve the repeated silhouette, product architecture, control "
                "locations, seams, openings and assembly relationships. Ignore "
                "browser chrome, captions, hands and room backgrounds. Produce "
                "a watertight exterior visualization mesh; do not invent CAD authority."
            ),
            "images": [staged[index] for index in members],
        }
        group["group_sha256"] = digest(group)
        groups.append(group)
    if assigned != set(range(len(staged))):
        raise RuntimeError("screenshot grouping is incomplete or overlapping")
    manifest = {
        "schema": "design-studio.product-multiview-batch/1",
        "image_count": len(staged), "group_count": len(groups),
        "all_images": staged, "groups": groups,
        "geometry_authority": "generated_visualization_only",
    }
    manifest["manifest_sha256"] = digest(manifest)
    (output / "batch-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "manifest": str(output / "batch-manifest.json"),
        "images": len(staged), "groups": [group["slug"] for group in groups],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
