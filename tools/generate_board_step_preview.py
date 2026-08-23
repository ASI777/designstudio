#!/usr/bin/env python3
"""Generate a visibly non-production AP242 board assembly for UI inspection.

The production exporter requires an approved fabrication profile. This helper
uses an explicit caller-supplied thickness and writes a sidecar declaring that
assumption, so an engineering preview can never be confused with release CAM.
Component STEP bindings, MPNs, transforms, files, and digests are still checked
by the same board-STEP writer used for production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from swarm.production.cam_export import (
    _board_document,
    _drill_hits,
    _json,
    _net_names,
    _pads,
    _write_board_step,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("board_step_binary", type=Path)
    parser.add_argument("--thickness-mm", type=float, required=True)
    args = parser.parse_args()
    if not args.project.is_file():
        parser.error("project does not exist")
    if not args.board_step_binary.is_file():
        parser.error("board STEP binary does not exist")
    if not 0.2 <= args.thickness_mm <= 10.0:
        parser.error("--thickness-mm must be between 0.2 and 10.0")

    document = _json(args.project)
    board, _ = _board_document(document)
    names = _net_names(board)
    pads = _pads(board, names)
    pth, npth = _drill_hits(board, pads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    job = args.output.with_suffix(".board-preview-job.txt")
    report = _write_board_step(
        job,
        args.output,
        board,
        {"stackup": {"finished_thickness_mm": args.thickness_mm}},
        pads,
        pth,
        npth,
        args.project.resolve().parent,
        args.board_step_binary.resolve(),
    )
    job.unlink(missing_ok=True)
    evidence = {
        "schema": "design-studio.board-step-preview/1",
        "release_status": "engineering_preview_only",
        "project": str(args.project.resolve()),
        "project_sha256": sha256(args.project),
        "step": str(args.output.resolve()),
        "step_sha256": sha256(args.output),
        "assumed_finished_thickness_mm": args.thickness_mm,
        "warning": "Not manufacturing output; regenerate with an approved fabrication profile.",
        "kernel_report": report,
    }
    sidecar = args.output.with_suffix(".preview.json")
    sidecar.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
