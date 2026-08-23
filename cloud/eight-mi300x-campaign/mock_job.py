#!/usr/bin/env python3
"""Small deterministic campaign job used to exercise all orchestration paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fail-once-marker", type=Path)
    args = parser.parse_args()
    if args.fail_once_marker and not args.fail_once_marker.exists():
        args.fail_once_marker.parent.mkdir(parents=True, exist_ok=True)
        args.fail_once_marker.touch()
        return 17
    time.sleep(0.15 + (args.seed % 3) * 0.05)
    material = {
        "schema": "multi-gpu-campaign-mock-result/1",
        "job_id": os.environ["CAMPAIGN_JOB_ID"],
        "gpu_index": int(os.environ["CAMPAIGN_GPU_INDEX"]),
        "rocr_visible_devices": os.environ["ROCR_VISIBLE_DEVICES"],
        "kind": args.kind,
        "seed": args.seed,
        "authority_status": (
            "visualization_only" if args.kind in {"car", "components", "router"}
            else "deterministic_mock_evidence"
        ),
    }
    material["result_sha256"] = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
