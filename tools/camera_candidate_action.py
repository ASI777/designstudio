#!/usr/bin/env python3
"""Trusted digest-bound Apply/Modify/Reject/Undo adapter for camera candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "freecad/DesignStudioWorkbench"
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.camera_benchmark import persist_candidate_review_action  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path)
    parser.add_argument("action", choices=("apply", "modify", "reject", "undo"))
    parser.add_argument("--candidate", choices=("precision", "grip", "serviceable"))
    parser.add_argument("--candidate-digest")
    parser.add_argument("--modification-request")
    args = parser.parse_args()
    values = {}
    if args.action != "undo":
        if not args.candidate or not args.candidate_digest:
            parser.error("non-undo actions require --candidate and --candidate-digest")
        values.update(candidate=args.candidate, candidate_digest=args.candidate_digest)
    if args.modification_request is not None:
        values["modification_request"] = args.modification_request
    state = persist_candidate_review_action(args.review.resolve(), args.action, **values)
    print(json.dumps({"status": state["status"], "revision": state["revision"],
                      "canonical_revision": state["canonical_revision"],
                      "state_sha256": state["state_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
