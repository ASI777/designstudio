from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from campaign import Scheduler, file_digest


HERE = Path(__file__).resolve().parent


def config(marker: Path | None = None) -> dict:
    jobs = []
    for gpu in range(8):
        command = [
            sys.executable, str(HERE / "mock_job.py"), "--kind", "test",
            "--seed", str(gpu), "--output", "{output_dir}/result.json",
        ]
        if marker is not None and gpu == 0:
            command += ["--fail-once-marker", str(marker)]
        jobs.append({
            "id": f"job-{gpu}", "preferred_gpu": gpu,
            "compatible_gpus": [gpu], "expert_role": "test",
            "model_revision": "test", "container_revision": "test",
            "inputs": [], "outputs": ["result.json"], "solver_versions": {},
            "authority_status": "test", "timeout_seconds": 5,
            "max_attempts": 2, "long_job": False, "command": command,
        })
    return {
        "schema": "multi-gpu-campaign/1", "campaign_id": "test",
        "policy": {"maximum_runtime_seconds": 10,
                   "no_start_after_seconds": 8,
                   "destroy_deadline_seconds": 18000},
        "lanes": [{"gpu": gpu, "rocr_visible_devices": str(gpu)}
                  for gpu in range(8)],
        "jobs": jobs,
    }


def test_eight_workers_retry_and_isolate_devices(tmp_path):
    config_path = tmp_path / "one/two/campaign.json"
    config_path.parent.mkdir(parents=True)
    marker = tmp_path / "failed-once"
    config_path.write_text(json.dumps(config(marker)))
    run_dir = tmp_path / "run"
    assert asyncio.run(Scheduler(config_path, run_dir, mock=True).run()) == 0
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["all_jobs_succeeded"]
    assert len(manifest["jobs"]) == 8
    assert next(x for x in manifest["jobs"] if x["job_id"] == "job-0")["attempts"] == 2
    for gpu in range(8):
        result = json.loads(
            (run_dir / f"artifacts/job-{gpu}/result.json").read_text()
        )
        assert result["rocr_visible_devices"] == str(gpu)


def test_resume_requeues_a_tampered_artifact(tmp_path):
    config_path = tmp_path / "one/two/campaign.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config()))
    run_dir = tmp_path / "run"
    assert asyncio.run(Scheduler(config_path, run_dir, mock=True).run()) == 0
    artifact = run_dir / "artifacts/job-3/result.json"
    artifact.write_text("tampered")
    resumed = Scheduler(config_path, run_dir, mock=True)
    resumed.prepare()
    assert resumed.state["jobs"]["job-3"]["status"] == "pending"
    assert resumed.state["jobs"]["job-3"]["failure"] == "artifact_digest_failed_on_resume"


def test_file_digest_changes_with_content(tmp_path):
    path = tmp_path / "value"
    path.write_text("a")
    before = file_digest(path)
    path.write_text("b")
    assert file_digest(path) != before


def test_single_gpu_runs_every_job_on_lane_zero(tmp_path):
    value = config()
    value["campaign_id"] = "single-gpu-test"
    value["lanes"] = [{"gpu": 0, "rocr_visible_devices": "0"}]
    value["jobs"] = value["jobs"][:3]
    previous = None
    for job in value["jobs"]:
        job["preferred_gpu"] = 0
        job["compatible_gpus"] = [0]
        if previous is not None:
            job["depends_on"] = [previous]
        previous = job["id"]
    config_path = tmp_path / "one/two/campaign.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(value))
    run_dir = tmp_path / "run"
    assert asyncio.run(Scheduler(config_path, run_dir, mock=True).run()) == 0
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["all_jobs_succeeded"]
    assert [job["gpu_index"] for job in manifest["jobs"]] == [0, 0, 0]
