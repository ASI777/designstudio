#!/usr/bin/env python3
"""Small deterministic runner for the gateway's function-style contract tests."""

from __future__ import annotations

import inspect
import sys
import traceback
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_DIR))

import test_gateway  # noqa: E402


def main() -> int:
    tests = [
        (name, function)
        for name, function in inspect.getmembers(test_gateway, inspect.isfunction)
        if name.startswith("test_")
    ]
    failures = 0
    for name, function in tests:
        try:
            function()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 - each failed contract must be reported.
            failures += 1
            print(f"FAIL {name}", file=sys.stderr)
            traceback.print_exc()
    print(f"Python contracts: {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
