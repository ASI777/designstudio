#!/usr/bin/env python3
"""Digest-bound, resumable eight-lane MI300X campaign scheduler."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from typing import Any


SCHEMA = "multi-gpu-campaign/1"
FINAL_STATES = {"succeeded", "failed", "cancelled", "timed_out"}


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


class CampaignError(RuntimeError):
    pass


class Scheduler:
    def __init__(self, config_path: Path, run_dir: Path, *, mock: bool) -> None:
        self.root = config_path.resolve().parents[2]
        self.config_path = config_path.resolve()
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.run_dir = run_dir.resolve()
        self.mock = mock
        self.cancelled = asyncio.Event()
        self.lock = asyncio.Lock()
        self.started_monotonic = time.monotonic()
        self.state_path = self.run_dir / "state.json"
        self.manifest_path = self.run_dir / "manifest.json"
        self.telemetry_path = self.run_dir / "gpu-telemetry.jsonl"
        self.heartbeat_dir = self.run_dir / "heartbeats"
        self.logs_dir = self.run_dir / "logs"
        self.artifact_dir = self.run_dir / "artifacts"
        self.state: dict[str, Any] = {}
        self.gpu_count = 0
        self._validate_config()
        if not self.mock and any(
            str(job.get("model_revision", "")).startswith("mock/")
            or str(job.get("container_revision", "")).startswith("mock/")
            for job in self.config["jobs"]
        ):
            raise CampaignError(
                "production run refused: mock model/container revision present"
            )

    def _validate_config(self) -> None:
        if self.config.get("schema") != SCHEMA:
            raise CampaignError(f"config schema must be {SCHEMA}")
        lanes = self.config.get("lanes")
        if not isinstance(lanes, list) or not 1 <= len(lanes) <= 8:
            raise CampaignError("between one and eight GPU lanes are required")
        self.gpu_count = len(lanes)
        if [x.get("gpu") for x in lanes] != list(range(self.gpu_count)):
            raise CampaignError("GPU lanes must be ordered and contiguous from index 0")
        if any(x.get("rocr_visible_devices") != str(x["gpu"]) for x in lanes):
            raise CampaignError("each lane must bind ROCR_VISIBLE_DEVICES to its GPU index")
        policy = self.config.get("policy", {})
        if float(policy.get("maximum_runtime_seconds", 0)) > 18_000:
            raise CampaignError("hard runtime cap is 18,000 seconds")
        if float(policy.get("no_start_after_seconds", 0)) > 14_400:
            raise CampaignError("long-job start cutoff cannot exceed 14,400 seconds")
        if float(policy.get("destroy_deadline_seconds", 0)) != 18_000:
            raise CampaignError("destruction deadline must be exactly 18,000 seconds")
        identifiers: set[str] = set()
        for job in self.config.get("jobs", []):
            identifier = job.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise CampaignError("job IDs must be unique non-empty strings")
            identifiers.add(identifier)
            compatible = job.get("compatible_gpus")
            if not compatible or any(
                    type(gpu) is not int or gpu not in range(self.gpu_count)
                                     for gpu in compatible):
                raise CampaignError(f"{identifier}: compatible_gpus are invalid")
            if not isinstance(job.get("command"), list) or not job["command"]:
                raise CampaignError(f"{identifier}: command must be an argv list")
            if float(job.get("timeout_seconds", 0)) <= 0:
                raise CampaignError(f"{identifier}: timeout must be positive")
            for item in job.get("inputs", []):
                path = (self.root / item["path"]).resolve()
                if not path.is_file() or file_digest(path) != item["sha256"]:
                    raise CampaignError(f"{identifier}: stale or missing input {path}")

    def _fresh_state(self) -> dict[str, Any]:
        jobs = {
            job["id"]: {
                "status": "pending", "attempts": 0, "gpu": None,
                "started_at": None, "finished_at": None, "exit_code": None,
                "failure": None, "outputs": [],
            }
            for job in self.config["jobs"]
        }
        return {
            "schema": SCHEMA,
            "campaign_id": self.config["campaign_id"],
            "config_sha256": file_digest(self.config_path),
            "started_at_unix": time.time(),
            "jobs": jobs,
        }

    def prepare(self) -> None:
        for directory in (
            self.run_dir, self.heartbeat_dir, self.logs_dir, self.artifact_dir
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("config_sha256") != file_digest(self.config_path):
                raise CampaignError("resume refused: campaign config digest changed")
            self.state = state
            for identifier, record in self.state["jobs"].items():
                if record["status"] == "running":
                    record.update(status="pending", gpu=None,
                                  failure="recovered_after_controller_exit")
                if record["status"] == "succeeded":
                    valid = all(
                        (self.run_dir / output["path"]).is_file()
                        and file_digest(self.run_dir / output["path"]) == output["sha256"]
                        for output in record["outputs"]
                    )
                    if not valid:
                        record.update(status="pending", gpu=None,
                                      failure="artifact_digest_failed_on_resume")
        else:
            self.state = self._fresh_state()
        atomic_json(self.state_path, self.state)

    async def claim(self, gpu: int) -> dict[str, Any] | None:
        async with self.lock:
            now = time.monotonic() - self.started_monotonic
            no_start_after = float(self.config["policy"]["no_start_after_seconds"])
            candidates = []
            for index, job in enumerate(self.config["jobs"]):
                record = self.state["jobs"][job["id"]]
                dependencies = job.get("depends_on", [])
                failed_dependencies = [
                    dep for dep in dependencies
                    if self.state["jobs"][dep]["status"] in FINAL_STATES
                    and self.state["jobs"][dep]["status"] != "succeeded"
                ]
                if record["status"] == "pending" and failed_dependencies:
                    record.update(
                        status="failed", failure=(
                            "dependency_failed:" + ",".join(failed_dependencies)
                        ), finished_at=time.time(),
                    )
                    continue
                ready = all(
                    self.state["jobs"][dep]["status"] == "succeeded"
                    for dep in dependencies
                )
                if record["status"] == "pending" and ready and gpu in job["compatible_gpus"]:
                    if now >= no_start_after and job.get("long_job", False):
                        continue
                    preferred = 0 if job.get("preferred_gpu") == gpu else 1
                    candidates.append((preferred, index, job))
            if not candidates:
                return None
            job = min(candidates, key=lambda item: (item[0], item[1]))[2]
            record = self.state["jobs"][job["id"]]
            record.update(
                status="running", attempts=record["attempts"] + 1, gpu=gpu,
                started_at=time.time(), finished_at=None, exit_code=None,
                failure=None, outputs=[],
            )
            atomic_json(self.state_path, self.state)
            return job

    async def run_job(self, gpu: int, job: dict[str, Any]) -> None:
        identifier = job["id"]
        output_dir = self.artifact_dir / identifier
        output_dir.mkdir(parents=True, exist_ok=True)
        heartbeat = self.heartbeat_dir / f"gpu-{gpu}.json"
        log_path = self.logs_dir / f"{identifier}.log"
        env = os.environ.copy()
        env.update({
            "ROCR_VISIBLE_DEVICES": str(gpu),
            "HIP_VISIBLE_DEVICES": str(gpu),
            "CUDA_VISIBLE_DEVICES": str(gpu),
            "CAMPAIGN_GPU_INDEX": str(gpu),
            "CAMPAIGN_JOB_ID": identifier,
            "CAMPAIGN_OUTPUT_DIR": str(output_dir),
            "CAMPAIGN_RUN_DIR": str(self.run_dir),
            "CAMPAIGN_MOCK": "1" if self.mock else "0",
        })
        command = [
            token.replace("{python}", os.environ.get("PYTHON", "python3"))
                 .replace("{output_dir}", str(output_dir))
            for token in job["command"]
        ]
        with log_path.open("ab") as log:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=self.root, env=env,
                stdout=log, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

            async def pulse() -> None:
                while process.returncode is None:
                    atomic_json(heartbeat, {
                        "schema": "multi-gpu-campaign-heartbeat/1",
                        "gpu": gpu, "job_id": identifier, "pid": process.pid,
                        "unix_time": time.time(),
                    })
                    await asyncio.sleep(1 if self.mock else 5)

            pulse_task = asyncio.create_task(pulse())
            failure = None
            status = "succeeded"
            try:
                exit_code = await asyncio.wait_for(
                    process.wait(), timeout=float(job["timeout_seconds"])
                )
                if exit_code:
                    status, failure = "failed", f"exit_code_{exit_code}"
            except asyncio.TimeoutError:
                status, failure = "timed_out", "job_timeout"
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                exit_code = process.returncode
            except asyncio.CancelledError:
                status, failure = "cancelled", "campaign_cancelled"
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                exit_code = process.returncode
            finally:
                pulse_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pulse_task
        outputs = []
        if status == "succeeded":
            for expected in job["outputs"]:
                path = output_dir / expected
                if not path.is_file():
                    status, failure = "failed", f"missing_output:{expected}"
                    break
                outputs.append({
                    "path": str(path.relative_to(self.run_dir)),
                    "sha256": file_digest(path), "bytes": path.stat().st_size,
                })
        async with self.lock:
            self.state["jobs"][identifier].update(
                status=status, finished_at=time.time(), exit_code=exit_code,
                failure=failure, outputs=outputs,
            )
            atomic_json(self.state_path, self.state)

    async def lane(self, gpu: int) -> None:
        while not self.cancelled.is_set():
            job = await self.claim(gpu)
            if job is None:
                statuses = {record["status"] for record in self.state["jobs"].values()}
                if "running" not in statuses and "pending" not in statuses:
                    return
                await asyncio.sleep(0.1 if self.mock else 2)
                continue
            await self.run_job(gpu, job)
            record = self.state["jobs"][job["id"]]
            if record["status"] != "succeeded" and \
                    record["attempts"] < int(job.get("max_attempts", 1)):
                record.update(status="pending", gpu=None)
                atomic_json(self.state_path, self.state)

    async def telemetry(self) -> None:
        while not self.cancelled.is_set():
            sample = {
                "schema": "multi-gpu-campaign-telemetry/1",
                "unix_time": time.time(), "mock": self.mock, "gpus": [],
            }
            if self.mock:
                for gpu in range(self.gpu_count):
                    running = any(
                        x["status"] == "running" and x["gpu"] == gpu
                        for x in self.state["jobs"].values()
                    )
                    running_job = next((
                        identifier for identifier, value in self.state["jobs"].items()
                        if value["status"] == "running" and value["gpu"] == gpu
                    ), None)
                    sample["gpus"].append({
                        "index": gpu, "activity_percent": 85 if running else 0,
                        "hbm_used_bytes": 1024 * 1024 * (gpu + 1),
                        "temperature_c": 42, "job_id": running_job,
                    })
            else:
                process = await asyncio.create_subprocess_exec(
                    "rocm-smi", "--showuse", "--showmemuse", "--showtemp", "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                if process.returncode:
                    sample["error"] = stderr.decode(errors="replace")[-1000:]
                else:
                    raw = json.loads(stdout)
                    for gpu in range(self.gpu_count):
                        card = raw.get(f"card{gpu}", {})
                        activity_value = next((
                            value for key, value in card.items()
                            if "use" in key.lower() and "%" in key.lower()
                            and "memory" not in key.lower()
                        ), 0)
                        digits = "".join(
                            character for character in str(activity_value)
                            if character.isdigit() or character == "."
                        )
                        running_job = next((
                            identifier
                            for identifier, value in self.state["jobs"].items()
                            if value["status"] == "running" and value["gpu"] == gpu
                        ), None)
                        sample["gpus"].append({
                            "index": gpu,
                            "activity_percent": float(digits or 0),
                            "job_id": running_job,
                            "raw": card,
                        })
            with self.telemetry_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
            await asyncio.sleep(0.1 if self.mock else 5)

    def finalize_manifest(self) -> dict[str, Any]:
        jobs_by_id = {job["id"]: job for job in self.config["jobs"]}
        activity: dict[str, list[float]] = {}
        if self.telemetry_path.is_file():
            for line in self.telemetry_path.read_text(encoding="utf-8").splitlines():
                sample = json.loads(line)
                for gpu in sample.get("gpus", []):
                    if gpu.get("job_id"):
                        activity.setdefault(gpu["job_id"], []).append(
                            float(gpu["activity_percent"])
                        )
        records = []
        for identifier, state in self.state["jobs"].items():
            job = jobs_by_id[identifier]
            records.append({
                "job_id": identifier,
                "gpu_index": state["gpu"],
                "expert_role": job["expert_role"],
                "model_revision": job["model_revision"],
                "container_revision": job["container_revision"],
                "seed": job.get("seed"),
                "inputs": job.get("inputs", []),
                "outputs": state["outputs"],
                "solver_versions": job.get("solver_versions", {}),
                "utilization_percent": (
                    sum(activity.get(identifier, [])) /
                    len(activity[identifier])
                    if activity.get(identifier) else None
                ),
                "authority_status": job["authority_status"],
                "status": state["status"],
                "attempts": state["attempts"],
                "failure": state["failure"],
            })
        telemetry_sha = file_digest(self.telemetry_path) if self.telemetry_path.is_file() else None
        manifest = {
            "schema": SCHEMA,
            "campaign_id": self.config["campaign_id"],
            "config_sha256": file_digest(self.config_path),
            "mock": self.mock,
            "hardware_gate": "mock" if self.mock else "required_before_scheduler",
            "jobs": records,
            "telemetry": {
                "path": self.telemetry_path.name,
                "sha256": telemetry_sha,
                "sample_interval_seconds": 0.1 if self.mock else 5,
                "target_active_utilization_percent": 80,
            },
            "all_jobs_succeeded": all(x["status"] == "succeeded" for x in records),
            "release_authority": "deterministic_solvers_only",
        }
        material = dict(manifest)
        manifest["manifest_sha256"] = canonical_digest(material)
        atomic_json(self.manifest_path, manifest)
        return manifest

    async def run(self) -> int:
        self.prepare()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.cancelled.set)
        timer = asyncio.create_task(self.telemetry())
        lanes = [
            asyncio.create_task(self.lane(gpu))
            for gpu in range(self.gpu_count)
        ]
        maximum = float(self.config["policy"]["maximum_runtime_seconds"])
        try:
            await asyncio.wait_for(asyncio.gather(*lanes), timeout=maximum)
        except asyncio.TimeoutError:
            self.cancelled.set()
            for task in lanes:
                task.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
        finally:
            self.cancelled.set()
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
        manifest = self.finalize_manifest()
        return 0 if manifest["all_jobs_succeeded"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    scheduler = Scheduler(args.config, args.run_directory, mock=args.mock)
    return asyncio.run(scheduler.run())


if __name__ == "__main__":
    raise SystemExit(main())
