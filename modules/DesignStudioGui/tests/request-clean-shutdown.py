#!/usr/bin/env python3
"""Create and verify a digest-bound DesignStudio clean-shutdown exchange."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def fail(message):
    print("DESIGNSTUDIO_CLEAN_SHUTDOWN_FAILED: %s" % message, file=sys.stderr)
    return 1


def main(argv):
    if len(argv) != 6:
        return fail("usage: request-clean-shutdown.py READINESS REQUEST RECEIPT TOKEN PID")
    readiness_path, request_path, receipt_path = map(Path, argv[1:4])
    token = argv[4]
    expected_pid = int(argv[5])
    try:
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail("cannot read readiness receipt: %s" % exc)
    if readiness.get("token") != token or readiness.get("pid") != expected_pid:
        return fail("readiness receipt identity mismatch")
    readiness_digest = readiness.get("receipt_digest")
    unsigned_readiness = dict(readiness)
    unsigned_readiness.pop("receipt_digest", None)
    if readiness_digest != digest(unsigned_readiness):
        return fail("readiness receipt digest mismatch")
    arm_path = Path(str(request_path) + ".armed")
    try:
        arm = json.loads(arm_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail("shutdown observer is not armed: %s" % exc)
    arm_claimed = arm.get("receipt_digest")
    arm_unsigned = dict(arm)
    arm_unsigned.pop("receipt_digest", None)
    if not (
        arm.get("schema") == "design-studio.startup-shutdown-observer/1"
        and arm.get("status") == "armed"
        and arm.get("pid") == expected_pid
        and arm.get("token") == token
        and arm.get("readiness_receipt_digest") == readiness_digest
        and arm_claimed == digest(arm_unsigned)
    ):
        return fail("shutdown observer arm receipt is stale or malformed")
    request = {
        "schema": "design-studio.startup-shutdown-request/1",
        "token": token,
        "pid": expected_pid,
        "readiness_receipt_digest": readiness_digest,
    }
    request["request_digest"] = digest(request)
    temporary = request_path.with_name(".%s.tmp" % request_path.name)
    temporary.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(request_path)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and not receipt_path.is_file():
        time.sleep(0.05)
    if not receipt_path.is_file():
        accepted = Path(str(request_path) + ".accepted")
        detail = "request accepted" if accepted.is_file() else "request not observed"
        return fail("shutdown receipt was not produced (%s)" % detail)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail("shutdown receipt is malformed: %s" % exc)
    claimed = receipt.get("receipt_digest")
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest", None)
    checks = (
        receipt.get("schema") == "design-studio.startup-shutdown/1",
        receipt.get("status") == "pass",
        receipt.get("token") == token,
        receipt.get("pid") == expected_pid,
        receipt.get("readiness_receipt_digest") == readiness_digest,
        receipt.get("request_digest") == request["request_digest"],
        claimed == digest(unsigned),
    )
    if not all(checks):
        return fail("shutdown receipt failed identity, status, or digest validation")
    print("DESIGNSTUDIO_CLEAN_SHUTDOWN_RECEIPT_OK pid=%d" % expected_pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
