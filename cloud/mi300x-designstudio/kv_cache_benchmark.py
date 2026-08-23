#!/usr/bin/env python3
"""Reproducible vLLM automatic-prefix-cache benchmark.

Run once against an APC-disabled server and once against an APC-enabled server.
The tool uses streaming chat completions so time-to-first-token is measured,
records inter-token latency and throughput, and preserves a filtered snapshot
of vLLM's Prometheus cache metrics. It never sends product or customer data.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import statistics
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "design-studio.kv-benchmark/1"


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def synthetic_prefix(target_tokens: int) -> str:
    # Deliberately non-sensitive and stable. Token counts are approximate; the
    # server-reported prompt token count is authoritative in the result.
    block = "datum constraint patch surface component clearance revision evidence "
    return (block * math.ceil(target_tokens / 8))[: target_tokens * 8]


def read_sse(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_token_at: float | None = None
    token_times: list[float] = []
    usage: dict[str, int] = {}
    output_parts: list[str] = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            if delta.get("content"):
                output_parts.append(str(delta["content"]))
                now = time.perf_counter()
                first_token_at = first_token_at or now
                token_times.append(now)
    finished = time.perf_counter()
    ttft = (first_token_at or finished) - started
    itl = [
        (right - left) * 1000.0
        for left, right in zip(token_times, token_times[1:])
    ]
    completion_tokens = int(usage.get("completion_tokens", len(token_times)))
    generation_seconds = max(finished - (first_token_at or finished), 1e-9)
    return {
        "ttft_ms": ttft * 1000.0,
        "e2e_ms": (finished - started) * 1000.0,
        "itl_ms": statistics.fmean(itl) if itl else 0.0,
        "output_tokens": completion_tokens,
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "output_tokens_per_second": completion_tokens / generation_seconds,
        "output_sha256": hashlib.sha256(
            "".join(output_parts).encode("utf-8")
        ).hexdigest(),
    }


def prometheus_snapshot(base_url: str, timeout: float) -> dict[str, float]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/metrics")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            lines = response.read().decode("utf-8").splitlines()
    except Exception:
        return {}
    keep = ("prefix_cache", "gpu_cache", "cache_hit", "num_requests")
    metrics: dict[str, float] = {}
    for line in lines:
        if line.startswith("#") or not any(term in line for term in keep):
            continue
        name, separator, value = line.rpartition(" ")
        try:
            metrics[name] = float(value) if separator else 0.0
        except ValueError:
            continue
    return metrics


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    output_hashes = sorted({row["output_sha256"] for row in samples})
    return {
        "requests": len(samples),
        "ttft_ms": {
            "median": percentile([row["ttft_ms"] for row in samples], 0.5),
            "p95": percentile([row["ttft_ms"] for row in samples], 0.95),
        },
        "e2e_ms": {
            "median": percentile([row["e2e_ms"] for row in samples], 0.5),
            "p95": percentile([row["e2e_ms"] for row in samples], 0.95),
        },
        "itl_ms_mean": statistics.fmean(row["itl_ms"] for row in samples),
        "output_tokens_per_second_mean": statistics.fmean(
            row["output_tokens_per_second"] for row in samples
        ),
        "prompt_tokens_observed": sorted(
            {row["prompt_tokens"] for row in samples}
        ),
        "output_sha256_values": output_hashes,
        "deterministic_output": len(output_hashes) == 1,
    }


def monitor_gpu(stop: threading.Event, samples: list[float], interval: float) -> None:
    while not stop.is_set():
        try:
            result = subprocess.run(
                ["rocm-smi", "--showuse"],
                capture_output=True,
                text=True,
                timeout=max(2.0, interval * 4.0),
                check=False,
            )
            for line in result.stdout.splitlines():
                marker = "GPU use (%):"
                if marker in line:
                    samples.append(float(line.split(marker, 1)[1].strip()))
                    break
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        stop.wait(interval)


def run(args: argparse.Namespace) -> dict[str, Any]:
    prefix = synthetic_prefix(args.context_tokens)
    prefix_sha256 = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": prefix},
            {
                "role": "user",
                "content": "Return exactly eight words describing a safe CAD change.",
            },
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.variant == "apc_on":
        payload["cache_salt"] = hashlib.sha256(
            f"design-studio-benchmark/1:{prefix_sha256}".encode("utf-8")
        ).hexdigest()
    # Warm-ups are deliberately excluded from measurement. For APC-on the first
    # request populates the prefix cache; for APC-off it removes model/kernel
    # cold-start bias without creating a reusable prefix.
    for _ in range(args.warmups):
        read_sse(
            f"{args.base_url.rstrip('/')}/v1/chat/completions",
            payload,
            args.timeout,
        )
    before = prometheus_snapshot(args.base_url, args.timeout)
    samples: list[dict[str, Any]] = []
    gpu_samples: list[float] = []
    stop_gpu = threading.Event()
    gpu_thread = None
    if args.monitor_gpu:
        gpu_thread = threading.Thread(
            target=monitor_gpu,
            args=(stop_gpu, gpu_samples, args.gpu_sample_interval),
            daemon=True,
        )
        gpu_thread.start()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        futures = [
            executor.submit(
                read_sse,
                f"{args.base_url.rstrip('/')}/v1/chat/completions",
                payload,
                args.timeout,
            )
            for _ in range(args.repeats)
        ]
        for future in futures:
            samples.append(future.result())
    stop_gpu.set()
    if gpu_thread is not None:
        gpu_thread.join(timeout=5.0)
    after = prometheus_snapshot(args.base_url, args.timeout)
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "variant": args.variant,
        "server_apc_expected": args.variant == "apc_on",
        "model": args.model,
        "model_revision": args.model_revision,
        "image_digest": args.image_digest,
        "seed": 0,
        "context_tokens_requested": args.context_tokens,
        "concurrency": args.concurrency,
        "repeats": args.repeats,
        "warmups": args.warmups,
        "prefix_sha256": prefix_sha256,
        "summary": summarize(samples),
        "samples": samples,
        "prometheus_before": before,
        "prometheus_after": after,
        "gpu_utilization": {
            "sample_count": len(gpu_samples),
            "mean_percent": statistics.fmean(gpu_samples) if gpu_samples else None,
            "p95_percent": percentile(gpu_samples, 0.95) if gpu_samples else None,
            "samples_at_or_above_80_percent": (
                sum(value >= 80.0 for value in gpu_samples)
                if gpu_samples else None
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--variant", choices=("apc_off", "apc_on"), required=True)
    parser.add_argument("--context-tokens", type=int, choices=(4096, 16384, 65536), required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4, 8), required=True)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--monitor-gpu", action="store_true")
    parser.add_argument("--gpu-sample-interval", type=float, default=0.25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < args.concurrency:
        parser.error("--repeats must be at least --concurrency")
    if not 0 <= args.warmups <= 8:
        parser.error("--warmups must be between 0 and 8")
    if not 0.1 <= args.gpu_sample_interval <= 10.0:
        parser.error("--gpu-sample-interval must be between 0.1 and 10 seconds")
    return args


def main() -> int:
    args = parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
