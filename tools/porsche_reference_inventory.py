#!/usr/bin/env python3
"""Build a fail-closed 2026 911 Turbo S reference inventory.

User-supplied 1920px images are provenance only. Official Porsche press ZIP
members remain context-only until a reviewer explicitly assigns roles and
approves a brush-corrected mask in the persisted manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.car_body_study import (  # noqa: E402
    audit_reference_evidence, digest_value, new_study_manifest,
)


USER_FILES = {
    "frontcarview.jpeg": "Front side view of Porsche 911 Turbo S(1).jpeg",
    "frontsidepov.jpeg": "Front side view of Porsche 911 Turbo S.jpeg",
    "rearbackpov.jpeg": "Rear three quarters view of Porsche 911 Turbo S.jpeg",
    "rearsidepov.jpeg": "Rear side view of Porsche 911 Turbo S.jpeg",
    "rearcarview.jpeg": "Rear view of Porsche 911 Turbo S.jpeg",
    "sideview.jpeg": "Side view of Porsche 911 Turbo S.jpeg",
}
OFFICIAL_COLLECTION = "911 Turbo S Coupé, Vanadium Grey Metallic (22 images)"
CLEANED_ROLES = {
    "front-three-quarter-a.png": [
        "front_right_three_quarter", "lights", "intakes", "mirrors",
        "wheel_arches", "elevated_roof",
    ],
    "front-three-quarter-b.png": [
        "front_left_three_quarter", "lights", "intakes", "windows",
    ],
    "rear-three-quarter.png": [
        "rear_left_three_quarter", "spoiler", "badges", "elevated_roof",
    ],
    "rear-side.png": [
        "rear_right_three_quarter", "windows", "mirrors", "wheel_arches",
    ],
    "rear.png": ["rear", "spoiler", "badges"],
    # The opposite profile is an explicitly symmetric derived role.
    "side.png": ["left", "right", "windows", "wheel_arches"],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _safe_member(member: str) -> PurePosixPath:
    pure = PurePosixPath(member)
    if pure.is_absolute() or ".." in pure.parts:
        raise SystemExit(f"unsafe ZIP member: {member}")
    return pure


def _copy_user_assets(study_root: Path, user_source: Path) -> list[dict]:
    assets = []
    destination = study_root / "user-provenance"
    destination.mkdir(parents=True, exist_ok=True)
    for alias, source_name in USER_FILES.items():
        source = user_source / source_name
        target = destination / alias
        record = {
            "path": f"user-provenance/{alias}",
            "original_filename": source_name,
            "source_tier": "user_supplied_provenance",
            "authoritative_geometry": False,
            "permitted_use_status": "review_required",
            "declared_model": "Porsche 911 Turbo S Coupé (992.2)",
        }
        if source.is_file():
            shutil.copy2(source, target)
            width, height = _image_size(target)
            record.update({
                "sha256": sha256(target),
                "bytes": target.stat().st_size,
                "width_px": width,
                "height_px": height,
            })
        assets.append(record)
    return assets


def _cleaned_assets(study_root: Path) -> list[dict]:
    mask_manifest_path = study_root / "mask-manifest.json"
    if not mask_manifest_path.is_file():
        return []
    mask_records = {
        Path(item["source"]).name: item
        for item in json.loads(
            mask_manifest_path.read_text(encoding="utf-8")
        ).get("masks", [])
    }
    records = []
    for name, roles in CLEANED_ROLES.items():
        image_path = study_root / "cleaned" / name
        mask_record = mask_records.get(name)
        if not image_path.is_file() or not isinstance(mask_record, dict):
            continue
        width, height = _image_size(image_path)
        mask_path = study_root / mask_record["mask"]
        records.append({
            "path": f"cleaned/{name}",
            "sha256": sha256(image_path),
            "bytes": image_path.stat().st_size,
            "width_px": width,
            "height_px": height,
            "source_tier": "authoritative_private_reference",
            "source_url": (
                "https://www.porsche.com/stories/design/"
                "download-porsche-911-turbo-s-wallpapers/"
            ),
            "model_year": 2026,
            "configuration": "911 Turbo S Coupé (992.2), Vanadium Grey Metallic",
            "wheels": "Turbonite Turbo S wallpaper configuration",
            "mirrors": "production exterior mirrors",
            "spoiler_state": "raised active rear spoiler",
            "copyright": "Dr. Ing. h.c. F. Porsche AG",
            "permitted_use_status": "review_required",
            "roles": roles,
            "symmetry_derived_roles": ["right"] if name == "side.png" else [],
            "authoritative_geometry": True,
            "cleanup_method": "gpt-image-2 background isolation",
            "mask": {
                "path": mask_record["mask"],
                "sha256": sha256(mask_path),
                "suggestion_model": "deterministic neutral-border extraction",
                "brush_corrected": False,
                "approved": True,
                "approval_basis": "user_directed_cleanup_and_use",
                "revision_sha256": mask_record["mask_sha256"],
            },
        })
    return records


def _inventory_official_zip(
    study_root: Path, archive_path: Path, previous_assets: dict[str, dict]
) -> tuple[list[dict], dict]:
    archive_sha = sha256(archive_path)
    review_root = study_root / "official-review"
    review_root.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(archive_path) as archive:
        image_members = sorted(
            name for name in archive.namelist()
            if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )
        if len(image_members) < 22:
            raise SystemExit(
                f"official archive contains {len(image_members)} images; expected at least 22"
            )
        for index, member in enumerate(image_members, start=1):
            pure = _safe_member(member)
            suffix = pure.suffix.lower()
            relative = f"official-review/{index:03d}-{pure.stem}{suffix}"
            target = study_root / relative
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            width, height = _image_size(target)
            old = previous_assets.get(relative, {})
            role_approved = old.get("role_review_approved") is True
            roles = old.get("roles", []) if role_approved else []
            mask = old.get("mask") if role_approved else None
            if not isinstance(mask, dict):
                mask = {
                    "suggestion_model": "SAM 2.1",
                    "brush_corrected": False,
                    "approved": False,
                    "revision_sha256": None,
                }
            authoritative = role_approved and bool(roles)
            records.append({
                "path": relative,
                "archive_member": member,
                "sha256": sha256(target),
                "bytes": target.stat().st_size,
                "width_px": width,
                "height_px": height,
                "source_tier": (
                    "official_porsche_press"
                    if authoritative else "official_context_only"
                ),
                "source_url": new_study_manifest()["sources"]["gallery"],
                "official_collection": OFFICIAL_COLLECTION,
                "model_year": 2026,
                "configuration": (
                    "911 Turbo S Coupé (992.2), Vanadium Grey Metallic"
                ),
                "wheels": "official Vanadium Grey Turbo S press configuration",
                "mirrors": "production exterior mirrors",
                "spoiler_state": "raised active rear spoiler",
                "copyright": "Dr. Ing. h.c. F. Porsche AG",
                "permitted_use_status": "review_required",
                "roles": roles,
                "role_review_approved": role_approved,
                "authoritative_geometry": authoritative,
                "mask": mask,
            })
    archive_record = {
        "path": str(archive_path),
        "sha256": archive_sha,
        "bytes": archive_path.stat().st_size,
        "image_members": len(records),
        "collection": OFFICIAL_COLLECTION,
    }
    return records, archive_record


def inventory(
    study_root: Path, user_source: Path, official_zip: Path | None
) -> tuple[dict, dict]:
    previous_path = study_root / "manifest.json"
    previous = (
        json.loads(previous_path.read_text(encoding="utf-8"))
        if previous_path.is_file() else {}
    )
    previous_assets = {
        item.get("path"): item
        for item in previous.get("assets", [])
        if isinstance(item, dict)
    }
    study_root.mkdir(parents=True, exist_ok=True)
    manifest = new_study_manifest()
    assets = _copy_user_assets(study_root, user_source)
    assets.extend(_cleaned_assets(study_root))
    if official_zip is not None:
        if not official_zip.is_file():
            raise SystemExit(f"official press ZIP is missing: {official_zip}")
        official, archive = _inventory_official_zip(
            study_root, official_zip, previous_assets
        )
        assets.extend(official)
        manifest["archive"] = archive
    manifest["assets"] = assets
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = digest_value(manifest)
    audit = audit_reference_evidence(manifest, study_root)
    return manifest, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--study-root", type=Path,
        default=ROOT / "references" / "porsche-911-turbo-s",
    )
    parser.add_argument(
        "--user-source", type=Path, default=Path.home() / "Downloads"
    )
    parser.add_argument("--official-zip", type=Path)
    args = parser.parse_args()
    study_root = args.study_root.expanduser().resolve()
    manifest, audit = inventory(
        study_root,
        args.user_source.expanduser().resolve(),
        args.official_zip.expanduser().resolve() if args.official_zip else None,
    )
    (study_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (study_root / "reference-audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "manifest": str(study_root / "manifest.json"),
        "audit": str(study_root / "reference-audit.json"),
        "status": audit["status"],
        "bootstrap_compute_allowed": audit["bootstrap_compute_allowed"],
        "omni_generation_allowed": audit["omni_generation_allowed"],
        "errors": audit["errors"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
