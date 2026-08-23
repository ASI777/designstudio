#!/usr/bin/env python3
"""Prepare or submit the gated Turbo S Hunyuan generation stages.

Bootstrap uses the strongest cleaned front three-quarter reference. Omni is
fail-closed until the reference audit records six approved silhouettes and the
corresponding PNG image/mask pairs exist in the study directory.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from uuid import NAMESPACE_URL, uuid5


ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "references" / "porsche-911-turbo-s"
AUDIT = STUDY / "reference-audit.json"
BOOTSTRAP_IMAGE = STUDY / "clean" / "front-three-quarter-a.png"
TARGET_BOUNDS = (200.00, 89.34, 57.35)  # length, overall width, height
VIEWS = ("front", "rear", "left", "right", "top", "bottom")
BOOTSTRAP_SEED = 100
OMNI_SEEDS = (200, 201, 202, 203)


class GateError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GateError(f"missing required file: {path}") from error
    if not isinstance(value, dict):
        raise GateError(f"expected an object in {path}")
    return value


def png_payload(path: Path) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as error:
        raise GateError(f"missing required PNG: {path}") from error
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GateError(f"not a PNG: {path}")
    return base64.b64encode(data).decode("ascii"), hashlib.sha256(data).hexdigest()


def bootstrap_request(audit: dict) -> dict:
    if audit.get("bootstrap_compute_allowed") is not True:
        raise GateError("reference audit does not permit bootstrap compute")
    image_base64, _ = png_payload(BOOTSTRAP_IMAGE)
    return {
        "image_base64": image_base64,
        "target_dimensions_mm": list(TARGET_BOUNDS),
        "seed": BOOTSTRAP_SEED,
    }


def omni_request(audit: dict) -> dict:
    if audit.get("omni_generation_allowed") is not True:
        raise GateError(
            "Omni is locked: approve front/rear/left/right/top/bottom silhouette "
            "revisions and rebuild the reference audit first"
        )
    references = []
    for view in VIEWS:
        image, image_digest = png_payload(STUDY / "silhouettes" / f"{view}.png")
        mask, mask_digest = png_payload(
            STUDY / "silhouettes" / "masks" / f"{view}.png"
        )
        references.append(
            {
                "view": view,
                "image_base64": image,
                "mask_base64": mask,
                "image_sha256": image_digest,
                "mask_sha256": mask_digest,
            }
        )
    return {
        "configuration_id": str(
            uuid5(NAMESPACE_URL, "design-studio:porsche-911-turbo-s-9922")
        ),
        "slot_id": "slot.car-body.omni-candidates",
        "candidate_count": len(OMNI_SEEDS),
        "seeds": list(OMNI_SEEDS),
        "input_package": {
            "prompt": (
                "Private 2026 Porsche 911 Turbo S Coupe exterior-form study. "
                "Follow the approved six-view visual hull; preserve raised rear "
                "spoiler, lamp, intake, greenhouse, mirror and wheel-arch landmarks. "
                "Visible exterior A-surfaces only; underbody remains provisional."
            ),
            # Omni's public contract is Width x Depth x Height. Depth is the
            # vehicle's longitudinal 200 mm axis.
            "dimensions_mm": {
                "width": TARGET_BOUNDS[1],
                "depth": TARGET_BOUNDS[0],
                "height": TARGET_BOUNDS[2],
            },
            "reference_images": references,
            "symmetry": "none",
            "point_controls": [],
            "voxel_keepouts": [],
            "protected_regions": [],
        },
    }


def submit(endpoint: str, payload: dict, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise GateError(f"generation service returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise GateError(f"cannot reach generation service: {error.reason}") from error
    if not isinstance(result, dict):
        raise GateError("generation service returned a non-object response")
    return result


def write_bootstrap_result(result: dict, output: Path) -> None:
    encoded = result.pop("mesh_base64", None)
    if not isinstance(encoded, str):
        raise GateError("bootstrap response has no mesh_base64")
    try:
        mesh = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise GateError("bootstrap response mesh is invalid base64") from error
    if not mesh.startswith(b"glTF"):
        raise GateError("bootstrap response is not a binary glTF")
    output.mkdir(parents=True, exist_ok=True)
    mesh_path = output / "bootstrap-seed-100.glb"
    mesh_path.write_bytes(mesh)
    result["mesh_file"] = mesh_path.name
    result["mesh_sha256"] = hashlib.sha256(mesh).hexdigest()
    (output / "bootstrap-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("bootstrap", "omni"))
    parser.add_argument("--endpoint")
    parser.add_argument("--output", type=Path, default=STUDY / "generation")
    parser.add_argument(
        "--request-out",
        type=Path,
        help="write the fully pinned request JSON instead of submitting it",
    )
    args = parser.parse_args()

    audit = load_json(AUDIT)
    payload = bootstrap_request(audit) if args.stage == "bootstrap" else omni_request(audit)
    if args.request_out:
        args.request_out.parent.mkdir(parents=True, exist_ok=True)
        args.request_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(args.request_out)
        return 0
    if not args.endpoint:
        raise GateError("--endpoint is required unless --request-out is used")

    token = None
    if args.stage == "omni":
        token = os.environ.get("DS_HUNYUAN_API_TOKEN")
        if not token:
            raise GateError("DS_HUNYUAN_API_TOKEN is required for Omni")
    result = submit(args.endpoint, payload, token)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage == "bootstrap":
        write_bootstrap_result(result, args.output)
    else:
        (args.output / "omni-submit-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(f"blocked: {error}", file=sys.stderr)
        raise SystemExit(2)
