#!/usr/bin/env python3
"""Score rendered Omni candidates against approved six-view silhouettes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


VIEWS = ("front", "rear", "left", "right", "top", "bottom")
TARGET_M = np.asarray((0.20000, 0.08934, 0.05735), dtype=np.float64)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.uint8) >= 128


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--approved-masks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scored = []
    for mesh in sorted(args.candidates.glob("candidate-??.glb")):
        index = int(mesh.stem.rsplit("-", 1)[-1])
        rendered = args.candidates / f"candidate-{index:02d}-views"
        diagnostics = json.loads(
            (rendered / "render-diagnostics.json").read_text(encoding="utf-8")
        )
        dimensions_m = np.asarray(
            diagnostics["bounds_m"]["vehicle_length_width_height"],
            dtype=np.float64,
        )
        bbox_error_mm = np.abs(dimensions_m - TARGET_M) * 1000.0
        scores = {}
        for view in VIEWS:
            approved = mask(args.approved_masks / f"{view}.png")
            candidate = mask(rendered / "masks" / f"{view}.png")
            union = np.logical_or(approved, candidate).sum()
            scores[view] = float(np.logical_and(approved, candidate).sum() / union)
        blockers = []
        if min(scores.values()) < 0.95:
            blockers.append("SILHOUETTE_IOU_BELOW_0.95")
        if float(bbox_error_mm.max()) > 0.1:
            blockers.append("BOUNDING_BOX_ERROR_ABOVE_0.1_MM")
        blockers.append("LANDMARK_ERROR_NOT_VERIFIED")
        scored.append({
            "index": index,
            "mesh": mesh.name,
            "mesh_sha256": sha256(mesh),
            "silhouette_iou": {k: round(v, 6) for k, v in scores.items()},
            "minimum_iou": round(min(scores.values()), 6),
            "mean_iou": round(sum(scores.values()) / len(scores), 6),
            "bounding_box_error_mm": [round(float(v), 6) for v in bbox_error_mm],
            "landmark_error_mm": None,
            "release_eligible": not blockers,
            "blocking_findings": blockers,
        })
    if not scored:
        raise RuntimeError("no candidate GLBs found")
    ranking = sorted(
        scored, key=lambda item: (item["minimum_iou"], item["mean_iou"]),
        reverse=True,
    )
    result = {
        "schema": "design-studio.omni-candidate-scoring/1",
        "thresholds": {
            "silhouette_iou_each_view": 0.95,
            "landmark_error_mm_maximum": 0.5,
            "bounding_box_error_mm_maximum": 0.1,
        },
        "candidates": scored,
        "ranking": [item["index"] for item in ranking],
        "best_candidate_index": ranking[0]["index"],
        "gate_passed": any(item["release_eligible"] for item in scored),
        "cad_baseline_allowed": any(item["release_eligible"] for item in scored),
    }
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
