#!/usr/bin/env python3
"""Fail-closed MI300X hardware, isolation, topology and burn-in gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time


def command(argv: list[str]) -> str:
    completed = subprocess.run(argv, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(
            f"{argv[0]} failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-gpus", type=int, default=1)
    parser.add_argument("--minimum-hbm-bytes", type=int, default=180_000_000_000)
    parser.add_argument("--burn-seconds", type=float, default=20)
    args = parser.parse_args()
    if not 1 <= args.expected_gpus <= 8:
        parser.error("expected-gpus must be between one and eight")
    if args.burn_seconds < 5:
        parser.error("burn-in must run for at least five seconds")

    import torch
    if torch.version.hip is None or not torch.cuda.is_available():
        raise RuntimeError("ROCm PyTorch is required")
    if torch.cuda.device_count() != args.expected_gpus:
        raise RuntimeError(
            f"expected {args.expected_gpus} GPUs, found {torch.cuda.device_count()}"
        )
    render_nodes = sorted(Path("/dev/dri").glob("renderD*"))
    if len(render_nodes) < args.expected_gpus:
        raise RuntimeError(
            f"expected at least {args.expected_gpus} render nodes, "
            f"found {len(render_nodes)}"
        )

    rocm_smi_identity = json.loads(command([
        "rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json",
    ]))
    devices = []
    for index in range(args.expected_gpus):
        properties = torch.cuda.get_device_properties(index)
        torch_name = properties.name.strip()
        card = rocm_smi_identity.get(f"card{index}", {})
        rocm_smi_name = str(card.get("Card Series", "")).strip()
        if rocm_smi_name.upper() in {"N/A", "NA", "UNKNOWN"}:
            rocm_smi_name = ""
        name = torch_name or rocm_smi_name
        if torch_name and rocm_smi_name and torch_name != rocm_smi_name:
            raise RuntimeError(
                f"GPU {index} identity disagreement: PyTorch={torch_name!r}, "
                f"rocm-smi={rocm_smi_name!r}"
            )
        if "MI300X" not in name.upper():
            raise RuntimeError(f"GPU {index} is not MI300X: {name}")
        if properties.total_memory < args.minimum_hbm_bytes:
            raise RuntimeError(
                f"GPU {index} HBM {properties.total_memory} is below threshold"
            )
        devices.append({
            "index": index, "name": name, "torch_name": torch_name,
            "rocm_smi_name": rocm_smi_name,
            "total_memory_bytes": properties.total_memory,
        })
    peer_access = [
        [
            True if source == target else bool(
                torch.cuda.can_device_access_peer(source, target)
            )
            for target in range(args.expected_gpus)
        ]
        for source in range(args.expected_gpus)
    ]
    if not all(all(row) for row in peer_access):
        raise RuntimeError("not every MI300X lane has peer access to every other lane")

    start = time.monotonic()
    iterations = 0
    checksums = [0.0] * args.expected_gpus
    while time.monotonic() - start < args.burn_seconds:
        for index in range(args.expected_gpus):
            with torch.cuda.device(index):
                generator = torch.Generator(device=f"cuda:{index}")
                generator.manual_seed(10_000 + index + iterations)
                a = torch.randn((2048, 2048), device=f"cuda:{index}",
                                dtype=torch.float16, generator=generator)
                b = torch.randn((2048, 2048), device=f"cuda:{index}",
                                dtype=torch.float16, generator=generator)
                checksums[index] += float((a @ b).float().mean().cpu())
        iterations += 1
    torch.cuda.synchronize()
    if not all(abs(value) < 1e9 for value in checksums):
        raise RuntimeError("burn-in produced a non-finite checksum")

    report = {
        "schema": "multi-gpu-campaign-hardware-gate/1",
        "status": "pass",
        "expected_gpu_count": args.expected_gpus,
        "torch_hip": torch.version.hip,
        "devices": devices,
        "peer_access": peer_access,
        "render_nodes": [str(path) for path in render_nodes],
        "rocminfo_sha": __import__("hashlib").sha256(
            command(["rocminfo"]).encode()
        ).hexdigest(),
        "topology": command(["rocm-smi", "--showtopo", "--json"]),
        "burn_in": {
            "seconds": time.monotonic() - start,
            "iterations": iterations,
            "checksums": checksums,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("HARDWARE_GATE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
