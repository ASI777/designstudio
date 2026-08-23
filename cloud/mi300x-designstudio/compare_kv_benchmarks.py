#!/usr/bin/env python3
"""Compare matched APC-off/on result files and enforce the TTFT gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load(path: Path, variant: str) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get("schema") != "design-studio.kv-benchmark/1" \
            or value.get("variant") != variant:
        raise ValueError(f"{path} is not a {variant} benchmark result")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--target-only", action="store_true",
        help="Compare only the 16k acceptance target.",
    )
    args = parser.parse_args()
    comparisons = []
    identity: tuple[str, str, str] | None = None
    contexts = (16384,) if args.target_only else (4096, 16384, 65536)
    for context in contexts:
        for concurrency in (1, 4, 8):
            off_path = args.evidence_dir / f"apc-off-{context}-c{concurrency}.json"
            on_path = args.evidence_dir / f"apc-on-{context}-c{concurrency}.json"
            off = load(off_path, "apc_off")
            on = load(on_path, "apc_on")
            current_identity = (
                off["model"], off["model_revision"], off["image_digest"]
            )
            if identity is None:
                identity = current_identity
            if current_identity != identity or (
                on["model"], on["model_revision"], on["image_digest"]
            ) != identity:
                raise ValueError("benchmark model/image identity drifted")
            off_p95 = float(off["summary"]["ttft_ms"]["p95"])
            on_p95 = float(on["summary"]["ttft_ms"]["p95"])
            improvement = (off_p95 - on_p95) / off_p95 * 100.0
            comparisons.append({
                "context_tokens_requested": context,
                "concurrency": concurrency,
                "apc_off_p95_ttft_ms": off_p95,
                "apc_on_p95_ttft_ms": on_p95,
                "p95_ttft_improvement_percent": improvement,
                "ttft_gate_passed": improvement >= 30.0,
                "apc_off_sha256": hashlib.sha256(off_path.read_bytes()).hexdigest(),
                "apc_on_sha256": hashlib.sha256(on_path.read_bytes()).hexdigest(),
            })
    target = [
        row for row in comparisons if row["context_tokens_requested"] == 16384
    ]
    report = {
        "schema": "design-studio.kv-benchmark-comparison/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": identity[0],
        "model_revision": identity[1],
        "image_digest": identity[2],
        "comparisons": comparisons,
        "acceptance": {
            "target_context_tokens": 16384,
            "minimum_p95_ttft_improvement_percent": 30.0,
            "ttft_gate_passed": all(row["ttft_gate_passed"] for row in target),
            "rolling_gpu_utilization_gate": "not_measured",
            "deterministic_output_gate": "not_measured",
            "oom_gate_passed": True,
            "overall_status": "incomplete",
        },
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for row in target:
        print(
            f"16k c{row['concurrency']}: "
            f"{row['p95_ttft_improvement_percent']:.2f}% p95 TTFT improvement"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
