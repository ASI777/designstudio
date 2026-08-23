#!/usr/bin/env python3
"""Exercise a real FreeCAD worker commit and process-level cancellation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time


def request(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


def control(command: str, worker: str, flag: str, root: Path) -> None:
    environment = os.environ.copy()
    environment["DESIGNSTUDIO_NATIVE_TOOL_CONTROL"] = "1"
    completed = subprocess.run(
        [command, worker, "--pass", flag, str(root)], check=False,
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr[-1200:]


def wait_for_published_journal(root: Path, process: subprocess.Popen[str], timeout: float = 75.0) -> dict:
    journal_path = root / ".designstudio-native-transaction.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"worker exited before publication: {stdout[-500:]} {stderr[-1200:]}")
        if journal_path.is_file():
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                journal = {}
            if journal.get("state") == "published-awaiting-host-ack":
                return journal
        time.sleep(0.05)
    raise AssertionError("worker never reached published-awaiting-host-ack")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecadcmd", required=True, type=Path)
    parser.add_argument("--worker", required=True, type=Path)
    args = parser.parse_args()
    command = str(args.freecadcmd.resolve())
    worker = str(args.worker.resolve())

    with tempfile.TemporaryDirectory(prefix="designstudio-worker-runtime-") as value:
        root = Path(value)
        mechanical = root / "camera.FCStd"
        stage = root / ".designstudio-native-worker-success"
        stage.mkdir()
        request_path = stage / "request.json"
        response_path = stage / "response.json"
        request(request_path, {
            "schema": "design-studio.native-tool-request/1",
            "operation": "create_product_workspace",
            "stage_root": str(stage),
            "arguments": {"mechanical_path": str(mechanical),
                          "name": "WorkerRuntimeCamera", "family": "camera"},
        })
        environment = os.environ.copy()
        environment["DESIGNSTUDIO_NATIVE_TOOL_REQUEST"] = str(request_path)
        environment["DESIGNSTUDIO_NATIVE_TOOL_RESPONSE"] = str(response_path)
        completed = subprocess.run(
            [command, worker], env=environment,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=90)
        assert completed.returncode == 0, completed.stderr[-1200:]
        response = json.loads(response_path.read_text(encoding="utf-8"))
        assert response.get("ok") is True, response
        assert mechanical.is_file() and mechanical.stat().st_size > 0
        transaction = response["data"]["worker_transaction"]
        assert transaction["status"] == "published-awaiting-host-ack"
        assert transaction["host_ack_required"] is True
        assert (root / ".designstudio-native-transaction.json").is_file()
        control(command, worker, "--ack-root", root)
        assert not (root / ".designstudio-native-transaction.json").exists()

    with tempfile.TemporaryDirectory(prefix="designstudio-worker-cancel-") as value:
        root = Path(value)
        mechanical = root / "approved.FCStd"
        approved = b"last-approved-revision"
        mechanical.write_bytes(approved)
        stage = root / ".designstudio-native-worker-cancel"
        stage.mkdir()
        request_path = stage / "request.json"
        response_path = stage / "response.json"
        request(request_path, {
            "schema": "design-studio.native-tool-request/1",
            "operation": "apply_mechanical_stage",
            "stage_root": str(stage),
            "arguments": {"mechanical_path": str(mechanical),
                          "template": "sealed_wall_mount", "width_mm": 145,
                          "depth_mm": 95, "height_mm": 32, "wall_mm": 2},
        })
        environment = os.environ.copy()
        environment["DESIGNSTUDIO_NATIVE_TOOL_TEST_DELAY_MS"] = "5000"
        environment["DESIGNSTUDIO_NATIVE_TOOL_REQUEST"] = str(request_path)
        environment["DESIGNSTUDIO_NATIVE_TOOL_RESPONSE"] = str(response_path)
        process = subprocess.Popen(
            [command, worker],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=environment, start_new_session=True)
        time.sleep(0.8)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        assert mechanical.read_bytes() == approved
        if response_path.exists():
            assert json.loads(response_path.read_text(encoding="utf-8")).get("ok") is not True

    # Reproduce the dangerous post-publication/pre-response Stop window.  A
    # hard kill plus GUI QTemporaryDir cleanup must still leave enough durable
    # data for the host to recover the last approved revision.
    with tempfile.TemporaryDirectory(prefix="designstudio-worker-hard-kill-") as value:
        root = Path(value)
        mechanical = root / "candidate.FCStd"
        stage = root / ".designstudio-native-worker-hard-kill"
        stage.mkdir()
        request_path = stage / "request.json"
        response_path = stage / "response.json"
        request(request_path, {
            "schema": "design-studio.native-tool-request/1",
            "operation": "create_product_workspace",
            "stage_root": str(stage),
            "arguments": {"mechanical_path": str(mechanical),
                          "name": "HardKillCamera", "family": "camera"},
        })
        environment = os.environ.copy()
        environment["DESIGNSTUDIO_NATIVE_TOOL_TEST_POST_COMMIT_DELAY_MS"] = "10000"
        environment["DESIGNSTUDIO_NATIVE_TOOL_REQUEST"] = str(request_path)
        environment["DESIGNSTUDIO_NATIVE_TOOL_RESPONSE"] = str(response_path)
        process = subprocess.Popen(
            [command, worker], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=environment, start_new_session=True)
        journal = wait_for_published_journal(root, process)
        backup_root = root / journal["backup_root"]
        assert mechanical.is_file() and backup_root.is_dir()
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        shutil.rmtree(stage)
        assert backup_root.is_dir(), "recovery bytes were coupled to the GUI temporary stage"
        control(command, worker, "--recover-root", root)
        assert not mechanical.exists(), "unacknowledged published file survived Stop recovery"
        assert not (root / ".designstudio-native-transaction.json").exists()
        assert not backup_root.exists()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
