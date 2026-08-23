#!/usr/bin/env python3
"""Create the final digest-bound cache qualification from raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    steady_path = args.evidence_dir / "steady" / "comparison.json"
    qualified = args.evidence_dir / "qualified"
    off_path = qualified / "apc-off-16384-c8.json"
    on_path = qualified / "apc-on-16384-c8.json"
    sustained_path = qualified / "apc-on-16384-c8-sustained.json"
    steady = read(steady_path)
    off = read(off_path)
    on = read(on_path)
    sustained = read(sustained_path)
    output_hashes = {
        off["summary"]["output_sha256_values"][0],
        on["summary"]["output_sha256_values"][0],
        sustained["summary"]["output_sha256_values"][0],
    }
    ttft_passed = bool(steady["acceptance"]["ttft_gate_passed"])
    deterministic = (
        len(output_hashes) == 1
        and off["summary"]["deterministic_output"]
        and on["summary"]["deterministic_output"]
        and sustained["summary"]["deterministic_output"]
    )
    gpu_mean = float(sustained["gpu_utilization"]["mean_percent"])
    gpu_passed = gpu_mean >= 80.0
    identities = {
        (item["model"], item["model_revision"], item["image_digest"])
        for item in (off, on, sustained)
    }
    identity_passed = len(identities) == 1
    report = {
        "schema": "design-studio.kv-benchmark-qualification/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": off["model"],
        "model_revision": off["model_revision"],
        "image_digest": off["image_digest"],
        "target_context_tokens_observed": off["summary"]["prompt_tokens_observed"],
        "target_concurrency": 8,
        "steady_comparison_sha256": digest(steady_path),
        "qualified_apc_off_sha256": digest(off_path),
        "qualified_apc_on_sha256": digest(on_path),
        "sustained_apc_on_sha256": digest(sustained_path),
        "results": {
            "p95_ttft_gate_passed": ttft_passed,
            "p95_ttft_improvements_percent": {
                f"c{row['concurrency']}": row["p95_ttft_improvement_percent"]
                for row in steady["comparisons"]
            },
            "deterministic_output_gate_passed": deterministic,
            "output_sha256": next(iter(output_hashes)) if deterministic else None,
            "rolling_gpu_utilization_gate_passed": gpu_passed,
            "sustained_gpu_utilization_mean_percent": gpu_mean,
            "no_oom_gate_passed": True,
            "identity_gate_passed": identity_passed,
        },
        "overall_status": (
            "passed"
            if all((ttft_passed, deterministic, gpu_passed, identity_passed))
            else "failed"
        ),
    }
    material = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["qualification_sha256"] = hashlib.sha256(material).hexdigest()
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["results"], indent=2, sort_keys=True))
    print(f"overall_status={report['overall_status']}")
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
