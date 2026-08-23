#!/usr/bin/env python3
"""Download, verify, then destroy one explicitly identified DigitalOcean Droplet."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--remote-run-directory", required=True)
    parser.add_argument(
        "--remote-tools-directory",
        default="/workspace/cloud/eight-mi300x-campaign",
    )
    parser.add_argument("--local-directory", type=Path, required=True)
    parser.add_argument("--droplet-id", required=True)
    parser.add_argument("--destroy", action="store_true")
    args = parser.parse_args()
    if "/" in args.droplet_id or not args.droplet_id.isdigit():
        parser.error("droplet-id must be an exact numeric DigitalOcean ID")
    local = args.local_directory.resolve()
    local.mkdir(parents=True, exist_ok=True)
    remote = f"{args.user}@{args.host}"
    remote_inventory = f"{args.remote_run_directory}/artifact-inventory.json"
    run([
        "ssh", remote, "python3",
        f"{args.remote_tools_directory}/verify_artifacts.py",
        "--directory", args.remote_run_directory,
        "--write", remote_inventory,
    ])
    run(["rsync", "-a", "--partial", f"{remote}:{args.remote_run_directory}/", f"{local}/"])
    run([
        "python3", str(Path(__file__).resolve().parent / "verify_artifacts.py"),
        "--directory", str(local),
        "--verify", str(local / "artifact-inventory.json"),
    ])
    receipt = {
        "schema": "multi-gpu-campaign-closeout/1",
        "droplet_id": args.droplet_id,
        "host": args.host,
        "artifacts_verified": True,
        "destroy_requested": args.destroy,
        "destroyed": False,
    }
    if not args.destroy:
        print(json.dumps(receipt, indent=2))
        raise RuntimeError(
            "artifacts verified but destruction was not authorized; destroy manually now"
        )
    if not os.environ.get("DIGITALOCEAN_ACCESS_TOKEN"):
        print(json.dumps(receipt, indent=2))
        raise RuntimeError(
            "DIGITALOCEAN_ACCESS_TOKEN unavailable; destroy the droplet manually now"
        )
    run(["doctl", "compute", "droplet", "delete", args.droplet_id, "--force"])
    completed = subprocess.run(
        ["doctl", "compute", "droplet", "list", "--format", "ID", "--no-header"],
        text=True, capture_output=True, check=True,
    )
    if args.droplet_id in completed.stdout.split():
        raise RuntimeError("DigitalOcean still lists the droplet after deletion")
    receipt["destroyed"] = True
    (local / "destruction-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("ARTIFACTS_VERIFIED_AND_DROPLET_DESTROYED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
