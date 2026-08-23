#!/usr/bin/env python3
"""Seal explicitly accepted Turbo S silhouette revision 001."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad" / "DesignStudioWorkbench"))
from DesignStudio.car_body_study import (  # noqa: E402
    VIEWPOINTS,
    audit_reference_evidence,
    digest_value,
)

STUDY = ROOT / "references" / "porsche-911-turbo-s"
REVISION = STUDY / "silhouettes" / "revision-001"


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    revision_path = REVISION / "revision-manifest.json"
    revision = json.loads(revision_path.read_text(encoding="utf-8"))
    records = revision.get("views", [])
    if {item.get("viewpoint") for item in records} != set(VIEWPOINTS):
        raise SystemExit("revision 001 does not contain all six cardinal views")
    approved_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    canonical = STUDY / "silhouettes"
    canonical_masks = canonical / "masks"
    canonical_masks.mkdir(parents=True, exist_ok=True)
    approved = []
    for record in records:
        view = record["viewpoint"]
        source_image = STUDY / record["path"]
        source_mask = STUDY / record["mask_path"]
        if digest_file(source_image) != record["revision_sha256"]:
            raise SystemExit(f"revision image digest mismatch: {view}")
        if digest_file(source_mask) != record["mask_sha256"]:
            raise SystemExit(f"revision mask digest mismatch: {view}")
        destination_image = canonical / f"{view}.png"
        destination_mask = canonical_masks / f"{view}.png"
        shutil.copy2(source_image, destination_image)
        shutil.copy2(source_mask, destination_mask)
        sealed = dict(record)
        sealed.update(
            {
                "path": str(destination_image.relative_to(STUDY)),
                "mask_path": str(destination_mask.relative_to(STUDY)),
                "revision_sha256": digest_file(destination_image),
                "mask_sha256": digest_file(destination_mask),
                "approved": True,
                "brush_corrected": True,
                "status": "approved",
                "approved_by": "user",
                "approved_utc": approved_utc,
                "approval_basis": "explicit approve all six silhouettes",
            }
        )
        approved.append(sealed)

    revision["views"] = approved
    revision["approval"] = {
        "approved": True,
        "approved_by": "user",
        "approved_utc": approved_utc,
        "scope": list(VIEWPOINTS),
        "statement": "approve all six silhouettes",
    }
    revision.pop("revision_set_sha256", None)
    revision["revision_set_sha256"] = digest_value(revision)
    revision_path.write_text(
        json.dumps(revision, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest_path = STUDY / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["silhouette_revisions"] = approved
    manifest["silhouette_approval"] = revision["approval"]
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = digest_value(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    audit = audit_reference_evidence(manifest, STUDY)
    if audit["omni_generation_allowed"] is not True:
        raise SystemExit(f"approval did not unlock Omni: {audit['errors']}")
    (STUDY / "reference-audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("TURBO_S_SIX_SILHOUETTES_APPROVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
