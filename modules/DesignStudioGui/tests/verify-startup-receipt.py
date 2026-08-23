#!/usr/bin/env python3
"""Validate a post-activation DesignStudio workspace receipt."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


def fail(message: str) -> int:
    print(f"DESIGNSTUDIO_STARTUP_RECEIPT_FAILED: {message}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) not in {4, 5}:
        return fail("usage: verify-startup-receipt.py RECEIPT TOKEN EXPECTED_PID [MIN_CYCLES]")
    receipt_path = Path(argv[1])
    token = argv[2]
    expected_pid = argv[3]
    minimum_cycles = int(argv[4]) if len(argv) == 5 else 0
    if not receipt_path.is_file():
        return fail(f"receipt is missing: {receipt_path}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"receipt is not valid JSON: {exc}")
    if not isinstance(receipt, dict):
        return fail("receipt is not an object")
    if receipt.get("schema") != "design-studio.startup-readiness/1":
        return fail("unexpected receipt schema")
    if receipt.get("status") != "pass":
        return fail(f"receipt status is {receipt.get('status')!r}")
    if receipt.get("token") != token:
        return fail("receipt token is stale or mismatched")
    pid = receipt.get("pid")
    if type(pid) is not int or pid <= 0:
        return fail("receipt pid is invalid")
    if expected_pid != "-" and pid != int(expected_pid):
        return fail(f"receipt belongs to pid {pid}, expected {expected_pid}")
    if not isinstance(receipt.get("created_utc"), str) or not receipt["created_utc"]:
        return fail("receipt timestamp is missing")
    checks = receipt.get("snapshot", {}).get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        return fail("one or more post-activation workspace checks failed")
    counts = receipt.get("snapshot", {}).get("counts", {})
    required_counts = {
        "workspace_controllers": 1,
        "workspace_documents": 1,
        "product_surfaces": 1,
        "chat_docks": 1,
        "context_toolbars": 1,
    }
    for key, expected in required_counts.items():
        if counts.get(key) != expected:
            return fail(f"{key} count is {counts.get(key)!r}, expected {expected}")
    cycles = receipt.get("lifecycle_cycles")
    if type(cycles) is not int or cycles < minimum_cycles:
        return fail(f"lifecycle cycle count {cycles!r} is below {minimum_cycles}")
    if minimum_cycles:
        history = receipt.get("lifecycle_history")
        if not isinstance(history, list) or len(history) != minimum_cycles * 2:
            return fail("lifecycle history does not contain two transitions per cycle")
        for cycle in range(1, minimum_cycles + 1):
            native, designstudio = history[(cycle - 1) * 2:cycle * 2]
            if native.get("cycle") != cycle or native.get("phase") != "native_activated_document_closed":
                return fail(f"cycle {cycle} lacks a native-workbench close transition")
            if native.get("active_workbench") != "PartDesignWorkbench":
                return fail(f"cycle {cycle} did not activate PartDesignWorkbench")
            if any(native.get("counts", {}).values()):
                return fail(f"cycle {cycle} retained workspace objects after close")
            if designstudio.get("cycle") != cycle or designstudio.get("phase") != "designstudio_activated":
                return fail(f"cycle {cycle} lacks a DesignStudio activation transition")
            if designstudio.get("active_workbench") != "DesignStudioWorkbench":
                return fail(f"cycle {cycle} did not reactivate DesignStudioWorkbench")
            if designstudio.get("counts") != required_counts:
                return fail(f"cycle {cycle} has duplicate or missing workspace objects")
    claimed_digest = receipt.get("receipt_digest")
    if not isinstance(claimed_digest, str):
        return fail("receipt digest is missing")
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest", None)
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if claimed_digest != actual_digest:
        return fail("receipt digest does not match its contents")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return fail(f"receipt process is not live: {exc}")
    print(f"DESIGNSTUDIO_STARTUP_RECEIPT_OK pid={pid} cycles={cycles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
