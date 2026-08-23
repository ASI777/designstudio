#!/usr/bin/env python3
"""Create and validate immutable DesignStudio visual-reference image sets.

The importer deliberately separates three things:

* the byte-for-byte source image (audit authority),
* a non-authoritative browser-chrome crop (model input convenience), and
* semantic view labels (human/model assertions with confidence).

No pixel measurement is ever converted to millimetres by this module.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


SCHEMA = "design-studio.reference-image-set/1"
SUPPORTED = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp"}
PREFERRED_VIEWS = ("front", "rear", "top", "left", "right", "oblique",
                   "rib_detail", "control_detail")
VIEW_CLASSES = set(PREFERRED_VIEWS) | {"bottom", "interior", "feature_detail",
                                             "ui_reference", "unknown"}


class ReferenceImageSetError(ValueError):
    """Raised when an image set violates its immutable evidence contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.") or "reference"


def _longest_run(flags: list[bool]) -> tuple[int, int]:
    best = (0, 0)
    start = None
    for index, enabled in enumerate(flags + [False]):
        if enabled and start is None:
            start = index
        elif not enabled and start is not None:
            if index - start > best[1] - best[0]:
                best = (start, index)
            start = None
    return best


def _bridge_short_gaps(flags: list[bool], maximum_gap: int) -> list[bool]:
    """Close small dark feature gaps inside an otherwise continuous canvas."""
    result = list(flags)
    index = 0
    while index < len(result):
        if result[index]:
            index += 1
            continue
        end = index
        while end < len(result) and not result[end]:
            end += 1
        if index > 0 and end < len(result) and end - index <= maximum_gap:
            result[index:end] = [True] * (end - index)
        index = end
    return result


def detect_content_crop(image: Image.Image) -> dict[str, Any]:
    """Find a large neutral/light content canvas and exclude surrounding chrome.

    This is intentionally conservative. If a stable canvas is not found the
    full image is retained and ``detected`` is false; an uncertain crop must
    never silently delete source evidence.
    """
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    width, height = rgb.size
    scale = min(1.0, 640.0 / max(width, 1))
    sample = rgb.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                        Image.Resampling.BILINEAR)
    sw, sh = sample.size
    pixels = sample.load()

    def neutral_light(x: int, y: int) -> bool:
        r, g, b = pixels[x, y]
        return (r + g + b) / 3.0 >= 168 and max(r, g, b) - min(r, g, b) <= 72

    row_flags = []
    for y in range(sh):
        count = sum(1 for x in range(sw) if neutral_light(x, y))
        row_flags.append(count >= max(8, int(sw * 0.43)))
    y0, y1 = _longest_run(_bridge_short_gaps(row_flags, max(2, round(sh * 0.015))))
    if y1 - y0 < sh * 0.22:
        return {"x": 0, "y": 0, "width": width, "height": height,
                "detected": False, "method": "full-image-fallback", "confidence": 0.0}

    col_flags = []
    denom = max(1, y1 - y0)
    for x in range(sw):
        count = sum(1 for y in range(y0, y1) if neutral_light(x, y))
        col_flags.append(count >= max(4, int(denom * 0.35)))
    x0, x1 = _longest_run(_bridge_short_gaps(col_flags, max(2, round(sw * 0.015))))
    if x1 - x0 < sw * 0.30:
        return {"x": 0, "y": 0, "width": width, "height": height,
                "detected": False, "method": "full-image-fallback", "confidence": 0.0}

    margin = max(2, round(min(sw, sh) * 0.005))
    x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
    x1, y1 = min(sw, x1 + margin), min(sh, y1 + margin)
    full = (0, 0, width, height)
    crop = (round(x0 / scale), round(y0 / scale),
            round((x1 - x0) / scale), round((y1 - y0) / scale))
    crop = (max(0, crop[0]), max(0, crop[1]),
            min(width - max(0, crop[0]), max(1, crop[2])),
            min(height - max(0, crop[1]), max(1, crop[3])))
    coverage = crop[2] * crop[3] / max(1.0, width * height)
    if coverage < 0.18:
        crop = full
        return {"x": 0, "y": 0, "width": width, "height": height,
                "detected": False, "method": "full-image-fallback", "confidence": 0.0}
    confidence = max(0.0, min(1.0, (1.0 - abs(coverage - 0.60)) * 0.9))
    return {"x": crop[0], "y": crop[1], "width": crop[2], "height": crop[3],
            "detected": crop != full, "method": "neutral-canvas-run-v1",
            "confidence": round(confidence, 4)}


def perceptual_dhash(image: Image.Image) -> str:
    gray = ImageOps.grayscale(ImageOps.exif_transpose(image)).resize((17, 16), Image.Resampling.LANCZOS)
    values = list(gray.getdata())
    bits = []
    for y in range(16):
        row = values[y * 17:(y + 1) * 17]
        bits.extend(row[x] > row[x + 1] for x in range(16))
    number = 0
    for bit in bits:
        number = (number << 1) | int(bit)
    return f"{number:064x}"


def _hamming(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


def _load_view_map(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReferenceImageSetError("view map must be a JSON object keyed by source basename")
    result = {}
    for key, raw in value.items():
        if isinstance(raw, str):
            raw = {"view": raw, "confidence": 1.0, "source": "user"}
        if not isinstance(raw, dict) or raw.get("view") not in VIEW_CLASSES:
            raise ReferenceImageSetError(f"invalid view-map record for {key!r}")
        confidence = float(raw.get("confidence", 1.0))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ReferenceImageSetError(f"invalid view confidence for {key!r}")
        result[str(key)] = {"view": raw["view"], "confidence": confidence,
                            "source": str(raw.get("source", "user"))}
    return result


def _select_representatives(records: list[dict[str, Any]]) -> list[str]:
    selected: list[str] = []
    used_hashes: list[str] = []
    for view in PREFERRED_VIEWS:
        choices = [record for record in records if record["view_classification"]["view"] == view]
        choices.sort(key=lambda item: (-float(item["view_classification"]["confidence"]),
                                       -item["crop_bbox_px"]["width"] * item["crop_bbox_px"]["height"],
                                       item["asset_id"]))
        if choices:
            selected.append(choices[0]["asset_id"])
            used_hashes.append(choices[0]["perceptual_hash"])
    for record in records:
        if len(selected) >= 8:
            break
        if record["asset_id"] in selected:
            continue
        if all(_hamming(record["perceptual_hash"], prior) > 8 for prior in used_hashes):
            selected.append(record["asset_id"])
            used_hashes.append(record["perceptual_hash"])
    return selected[:8]


def create_reference_image_set(sources: list[Path], output_root: Path,
                               view_map_path: Path | None = None,
                               set_name: str = "reference-images") -> Path:
    if not sources:
        raise ReferenceImageSetError("at least one image is required")
    if len(sources) > 256:
        raise ReferenceImageSetError("an image set may contain at most 256 originals")
    view_map = _load_view_map(view_map_path)
    source_paths = [path.expanduser().resolve() for path in sources]
    for source in source_paths:
        if source.suffix.lower() not in SUPPORTED or not source.is_file():
            raise ReferenceImageSetError(f"unsupported or missing image: {source}")
        if source.stat().st_size <= 0 or source.stat().st_size > 128 * 1024 * 1024:
            raise ReferenceImageSetError(f"image is empty or exceeds 128 MiB: {source}")

    set_digest = hashlib.sha256("\0".join(sha256_file(path) for path in source_paths).encode()).hexdigest()
    set_id = f"{_safe_stem(set_name)}-{set_digest[:12]}"
    root = output_root.expanduser().resolve() / set_id
    originals = root / "originals"
    crops = root / "crops"
    originals.mkdir(parents=True, exist_ok=True)
    crops.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for index, source in enumerate(source_paths, 1):
        extension = source.suffix.lower()
        stored = originals / f"{index:03d}-{_safe_stem(source.stem)}{extension}"
        if not stored.exists():
            shutil.copy2(source, stored)
        digest = sha256_file(source)
        if sha256_file(stored) != digest:
            raise ReferenceImageSetError(f"stored original hash mismatch: {source}")
        try:
            with Image.open(stored) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except (UnidentifiedImageError, OSError) as exc:
            raise ReferenceImageSetError(f"could not decode image {source}: {exc}") from exc
        bbox = detect_content_crop(image)
        box = (bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])
        cropped = image.crop(box)
        crop_path = crops / f"{index:03d}-{_safe_stem(source.stem)}.png"
        cropped.save(crop_path, format="PNG", optimize=True)
        mapped = view_map.get(source.name, {"view": "unknown", "confidence": 0.0,
                                            "source": "pending_visual_evidence"})
        record = {
            "asset_id": f"reference-image-{index:03d}",
            "source_path": str(source),
            "stored_original_path": str(stored.relative_to(root)),
            "cropped_path": str(crop_path.relative_to(root)),
            "source_sha256": digest,
            "crop_sha256": sha256_file(crop_path),
            "media_type": SUPPORTED[extension],
            "pixel_size": {"width": image.width, "height": image.height},
            "crop_bbox_px": bbox,
            "browser_chrome_detected": bool(bbox["detected"]),
            "view_classification": mapped,
            "perceptual_hash": perceptual_dhash(cropped),
            "duplicate_relationships": [],
            "geometry_authority": False,
            "measurement_status": "uncalibrated",
            "input_detail": "low",
        }
        records.append(record)

    for index, record in enumerate(records):
        for other in records[:index]:
            distance = _hamming(record["perceptual_hash"], other["perceptual_hash"])
            if distance <= 4:
                relationship = "near_duplicate"
            elif distance <= 10:
                relationship = "related_or_cropped_view"
            else:
                continue
            relation = {"asset_id": other["asset_id"], "relationship": relationship,
                        "perceptual_distance": distance, "confidence": round(1.0 - distance / 64.0, 4)}
            record["duplicate_relationships"].append(relation)
            other["duplicate_relationships"].append({**relation, "asset_id": record["asset_id"]})

    representatives = _select_representatives(records)
    for record in records:
        if record["asset_id"] in representatives:
            record["input_detail"] = "original"
            record["selection_reason"] = "representative-view-or-distinct-detail"
        else:
            record["selection_reason"] = "low-detail-context"

    manifest = {
        "schema": SCHEMA,
        "set_id": set_id,
        "revision": 1,
        "authority": "inspiration-and-visual-evidence-only",
        "measurement_status": "uncalibrated",
        "millimetre_inference_allowed": False,
        "source_count": len(records),
        "representative_asset_ids": representatives,
        "sources": records,
        "provenance": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "created_by": "DesignStudio reference-image importer",
            "importer_version": "1",
            "sources_preserved": True,
            "crop_policy": "non-authoritative-derived-copy",
        },
    }
    manifest_path = root / "reference-image-set.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_reference_image_set(manifest_path)
    return manifest_path


def validate_reference_image_set(manifest_path: Path) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ReferenceImageSetError(f"schema must be {SCHEMA}")
    if manifest.get("authority") != "inspiration-and-visual-evidence-only":
        raise ReferenceImageSetError("visual references cannot be CAD authority")
    if manifest.get("measurement_status") != "uncalibrated" or manifest.get("millimetre_inference_allowed") is not False:
        raise ReferenceImageSetError("uncalibrated pixels must not authorize millimetre inference")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != manifest.get("source_count"):
        raise ReferenceImageSetError("source_count does not match source records")
    root = path.parent
    ids = {record.get("asset_id") for record in sources}
    if len(ids) != len(sources) or None in ids:
        raise ReferenceImageSetError("asset IDs must be unique")
    for record in sources:
        for key in ("stored_original_path", "cropped_path"):
            relative = Path(str(record.get(key, "")))
            resolved = (root / relative).resolve()
            if relative.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
                raise ReferenceImageSetError(f"{key} escapes or is missing for {record['asset_id']}")
        if sha256_file(root / record["stored_original_path"]) != record.get("source_sha256"):
            raise ReferenceImageSetError(f"stale original hash for {record['asset_id']}")
        if sha256_file(root / record["cropped_path"]) != record.get("crop_sha256"):
            raise ReferenceImageSetError(f"stale crop hash for {record['asset_id']}")
        if record.get("geometry_authority") is not False or record.get("measurement_status") != "uncalibrated":
            raise ReferenceImageSetError(f"invalid image authority for {record['asset_id']}")
        if record.get("input_detail") not in {"low", "original"}:
            raise ReferenceImageSetError(f"unsupported image detail for {record['asset_id']}")
    representatives = manifest.get("representative_asset_ids")
    if not isinstance(representatives, list) or len(representatives) > 8 or not set(representatives) <= ids:
        raise ReferenceImageSetError("representative asset list is invalid")
    if any(next(record for record in sources if record["asset_id"] == asset_id)["input_detail"] != "original"
           for asset_id in representatives):
        raise ReferenceImageSetError("representative images must use original detail")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, default=[])
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--view-map", type=Path)
    parser.add_argument("--set-name", default="reference-images")
    parser.add_argument("--validate", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.validate:
            manifest = validate_reference_image_set(args.validate)
            result = {"ok": True, "manifest_path": str(args.validate.resolve()),
                      "source_count": manifest["source_count"],
                      "representative_count": len(manifest["representative_asset_ids"])}
        else:
            if args.output_root is None:
                raise ReferenceImageSetError("--output-root is required when importing images")
            manifest_path = create_reference_image_set(args.source, args.output_root,
                                                       args.view_map, args.set_name)
            manifest = validate_reference_image_set(manifest_path)
            result = {"ok": True, "manifest_path": str(manifest_path),
                      "source_count": manifest["source_count"],
                      "representative_count": len(manifest["representative_asset_ids"])}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReferenceImageSetError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
