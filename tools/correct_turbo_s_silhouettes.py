#!/usr/bin/env python3
"""Create reviewable Turbo S silhouette revision 001 from supplied evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))
from DesignStudio.car_body_study import audit_reference_evidence  # noqa: E402

STUDY = ROOT / "references" / "porsche-911-turbo-s"
PROVISIONAL = STUDY / "silhouettes" / "provisional"
OUTPUT = STUDY / "silhouettes" / "revision-001"
VIEWS = ("front", "rear", "left", "right", "top", "bottom")
PROJECTED_ASPECT = {
    "front": 89.34 / 57.35,
    "rear": 89.34 / 57.35,
    "left": 200.0 / 57.35,
    "right": 200.0 / 57.35,
    "top": 200.0 / 89.34,
    "bottom": 200.0 / 89.34,
}
SOURCE = {
    # There is no true front orthographic image. The rear orthographic outer
    # envelope is the best non-perspective body-width/height control, checked
    # against both supplied front three-quarter references.
    "front": STUDY / "masks" / "rear-mask.png",
    "rear": STUDY / "masks" / "rear-mask.png",
    "left": STUDY / "masks" / "side-mask.png",
    "right": STUDY / "masks" / "side-mask.png",
    "top": PROVISIONAL / "masks" / "top.png",
    "bottom": PROVISIONAL / "masks" / "bottom.png",
}
METHOD = {
    "front": "rear orthographic width/height envelope constrained by both front three-quarter views",
    "rear": "direct supplied rear orthographic mask",
    "left": "direct supplied side-view mask",
    "right": "explicit mirror of supplied side-view mask",
    "top": "bootstrap orthographic, scale-corrected; provisional",
    "bottom": "bootstrap orthographic, scale-corrected; provisional",
}


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def binary(path: Path) -> Image.Image:
    image = Image.open(path).convert("L")
    return image.point(lambda value: 255 if value >= 128 else 0)


def fit(source: Image.Image, aspect: float, *, resolution: int = 1024) -> Image.Image:
    bounds = source.getbbox()
    if bounds is None:
        raise RuntimeError("source silhouette is empty")
    crop = source.crop(bounds)
    maximum = int(resolution * 0.90)
    if aspect >= 1:
        width = maximum
        height = round(width / aspect)
    else:
        height = maximum
        width = round(height * aspect)
    resized = crop.resize((width, height), Image.Resampling.LANCZOS)
    resized = resized.point(lambda value: 255 if value >= 128 else 0)
    canvas = Image.new("L", (resolution, resolution), 0)
    canvas.paste(resized, ((resolution - width) // 2, (resolution - height) // 2))
    return canvas


def rgba(mask: Image.Image) -> Image.Image:
    image = Image.new("RGBA", mask.size, (245, 247, 248, 0))
    image.putalpha(mask)
    return image


def outline(mask: Image.Image) -> Image.Image:
    outer = mask.filter(ImageFilter.MaxFilter(7))
    inner = mask.filter(ImageFilter.MinFilter(7))
    return ImageChops.subtract(outer, inner)


def overlay(provisional: Image.Image, corrected: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", corrected.size, "white")
    before = Image.new("RGBA", corrected.size, (224, 48, 76, 0))
    before.putalpha(outline(provisional).point(lambda value: value * 3 // 4))
    after = Image.new("RGBA", corrected.size, (0, 142, 166, 0))
    after.putalpha(outline(corrected))
    canvas.alpha_composite(before)
    canvas.alpha_composite(after)
    return canvas.convert("RGB")


def mask_iou(first: Image.Image, second: Image.Image) -> float:
    intersection = ImageChops.multiply(first, second).histogram()[255]
    union = ImageChops.lighter(first, second).histogram()[255]
    if union == 0:
        raise RuntimeError("cannot score empty silhouettes")
    return intersection / union


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    masks = OUTPUT / "masks"
    overlays = OUTPUT / "overlays"
    masks.mkdir(exist_ok=True)
    overlays.mkdir(exist_ok=True)
    revisions = []
    for view in VIEWS:
        corrected = fit(binary(SOURCE[view]), PROJECTED_ASPECT[view])
        if view == "right":
            corrected = corrected.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        mask_path = masks / f"{view}.png"
        image_path = OUTPUT / f"{view}.png"
        overlay_path = overlays / f"{view}.png"
        corrected.save(mask_path, format="PNG", optimize=True)
        rgba(corrected).save(image_path, format="PNG", optimize=True)
        provisional = binary(PROVISIONAL / "masks" / f"{view}.png")
        overlay(provisional, corrected).save(overlay_path, format="PNG", optimize=True)
        bounds = corrected.getbbox()
        revisions.append(
            {
                "viewpoint": view,
                "revision": 1,
                "path": str(image_path.relative_to(STUDY)),
                "mask_path": str(mask_path.relative_to(STUDY)),
                "overlay_path": str(overlay_path.relative_to(STUDY)),
                "revision_sha256": digest_file(image_path),
                "mask_sha256": digest_file(mask_path),
                "method": METHOD[view],
                "source_path": str(SOURCE[view].relative_to(STUDY)),
                "source_sha256": digest_file(SOURCE[view]),
                "projected_aspect": round(PROJECTED_ASPECT[view], 9),
                "corrected_bounds_px": list(bounds) if bounds else None,
                "bootstrap_to_revision_iou": round(
                    mask_iou(provisional, corrected), 6
                ),
                "brush_corrected": True,
                "approved": False,
                "status": (
                    "provisional_pending_explicit_approval"
                    if view in {"top", "bottom"}
                    else "corrected_pending_explicit_approval"
                ),
            }
        )

    revision_manifest = {
        "schema": "design-studio.silhouette-revision/1",
        "revision": 1,
        "source_mesh_sha256": digest_file(
            STUDY / "generation" / "bootstrap-seed-100.glb"
        ),
        "overlay_legend": {
            "red": "bootstrap revision 000 boundary",
            "cyan": "corrected revision 001 boundary",
        },
        "views": revisions,
        "approval": None,
    }
    revision_manifest["revision_set_sha256"] = canonical_digest(revision_manifest)
    (OUTPUT / "revision-manifest.json").write_text(
        json.dumps(revision_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest_path = STUDY / "manifest.json"
    study_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    study_manifest["silhouette_revisions"] = revisions
    study_manifest.pop("manifest_sha256", None)
    study_manifest["manifest_sha256"] = canonical_digest(study_manifest)
    manifest_path.write_text(
        json.dumps(study_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    audit = audit_reference_evidence(study_manifest, STUDY)
    (STUDY / "reference-audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(OUTPUT / "revision-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
