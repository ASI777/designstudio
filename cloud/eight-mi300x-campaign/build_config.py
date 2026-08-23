#!/usr/bin/env python3
"""Build the campaign config with current, digest-bound local inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_record(relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": sha(ROOT / relative)}


def job(identifier: str, gpu: int, role: str, kind: str, seed: int,
        inputs: list[str], authority: str, *, compatible: list[int] | None = None,
        attempts: int = 1, depends_on: list[str] | None = None) -> dict:
    value = {
        "id": identifier,
        "preferred_gpu": gpu,
        "compatible_gpus": compatible or [gpu],
        "expert_role": role,
        "model_revision": "mock/local-deterministic-v1",
        "container_revision": "mock/no-container",
        "seed": seed,
        "inputs": [input_record(path) for path in inputs],
        "outputs": ["result.json"],
        "solver_versions": {},
        "authority_status": authority,
        "timeout_seconds": 30,
        "max_attempts": attempts,
        "long_job": False,
        "command": [
            "{python}", "cloud/eight-mi300x-campaign/mock_job.py",
            "--kind", kind, "--seed", str(seed),
            "--output", "{output_dir}/result.json",
        ],
    }
    if depends_on:
        value["depends_on"] = depends_on
    return value


def main() -> None:
    porsche = "references/porsche-911-turbo-s/reference-audit.json"
    component = "acceptance/agent-workflow-controller/generated/step-models.csv"
    component_bindings = "cloud/eight-mi300x-campaign/component-bindings.json"
    enclosure = "cloud/mi300x-designstudio/rib-pocket-experiment-request-v2.json"
    physics = "cloud/eight-mi300x-campaign/physics-inputs.json"
    pcb = "acceptance/agent-workflow-controller/generated/verification-report.json"
    jobs = [
        job("porsche-seed-200", 0, "porsche_omni_candidate", "car", 200,
            [porsche], "visualization_only"),
        job("hero-components", 0, "component_visualization", "components", 400,
            [component, component_bindings], "visualization_only",
            depends_on=["porsche-seed-200"]),
        job("surface-fit", 0, "curvature_bspline_fit", "surface_fit", 500,
            [porsche,
             "references/porsche-911-turbo-s/generation/approved-six-view-visual-hull.glb"],
            "deterministic_mock_evidence", depends_on=["hero-components"]),
        job("enclosure-physics", 0, "topology_structural_thermal", "physics", 600,
            [enclosure, physics], "deterministic_mock_evidence",
            depends_on=["surface-fit"]),
        job("qwen-expert-router", 0, "expert_router", "router", 700,
            [pcb], "proposal_only", depends_on=["enclosure-physics"]),
        job("cpu-pcb-verifier", 0, "pcb_drc_si_pdn_thermal", "pcb", 701,
            [pcb], "deterministic_mock_evidence",
            depends_on=["qwen-expert-router"]),
    ]
    config = {
        "schema": "multi-gpu-campaign/1",
        "campaign_id": "porsche-controller-pcb-components-1x-mi300x",
        "policy": {
            "maximum_runtime_seconds": 17_400,
            "no_start_after_seconds": 14_400,
            "destroy_deadline_seconds": 18_000,
        },
        "lanes": [{"gpu": 0, "rocr_visible_devices": "0"}],
        "jobs": jobs,
    }
    (HERE / "campaign.single-gpu.mock.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
