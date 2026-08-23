#!/usr/bin/env python3
"""MI300X correctness gate before any optional acceleration is enabled."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    import torch
    if torch.version.hip is None or not torch.cuda.is_available():
        raise SystemExit("NO-GO: ROCm PyTorch cannot see an accelerator")
    if "MI300" not in torch.cuda.get_device_name(0).upper():
        raise SystemExit(f"NO-GO: expected MI300X, got {torch.cuda.get_device_name(0)}")
    from omni_runner import generate_omni_candidate
    package = json.loads(args.package.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "candidate.glb"
        generate_omni_candidate(package=package, seed=args.seed, output_path=output)
        if output.stat().st_size < 100:
            raise SystemExit("NO-GO: empty generation artifact")
    print(f"GO: ROCm {torch.version.hip}; {torch.cuda.get_device_name(0)}; acceleration=off")


if __name__ == "__main__":
    main()
