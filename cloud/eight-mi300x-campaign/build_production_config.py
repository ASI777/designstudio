#!/usr/bin/env python3
"""Create a non-mock campaign config bound to local production image digests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def image(reference: str) -> str:
    value = subprocess.check_output([
        "docker", "image", "inspect", reference,
        "--format", "{{index .RepoDigests 0}}",
    ], text=True).strip()
    if "@sha256:" not in value:
        raise RuntimeError(f"image is not digest-bound: {reference}: {value}")
    return value


def record(relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": sha(relative)}


def main() -> None:
    omni = image("designstudio/hunyuan-omni:production")
    shape = image("designstudio/hunyuan-shape:production")
    support = image("designstudio/support-optimizer:production")
    mfem = image("designstudio/mfem-hip:production")
    vllm = (
        "vllm/vllm-openai-rocm:v0.23.0@"
        "sha256:3813e31cc3ab56f71ba83a72da77a84f3c75030e54c9c316951a99317b818ce4"
    )

    def job(identifier: str, role: str, model: str, container: str,
            kind: str, inputs: list[str], outputs: list[str], timeout: int,
            authority: str, *, depends: list[str] | None = None,
            secondary: str | None = None, long: bool = False) -> dict:
        command = [
            "{python}", "cloud/eight-mi300x-campaign/production_job.py",
            "--kind", kind, "--output", "{output_dir}", "--image", container,
        ]
        if secondary:
            command += ["--secondary-image", secondary]
        value = {
            "id": identifier, "preferred_gpu": 0, "compatible_gpus": [0],
            "expert_role": role, "model_revision": model,
            "container_revision": container, "seed": {
                "porsche": 200, "components": 400, "surface-fit": 500,
                "enclosure": 600, "qwen": 700, "pcb": 701,
            }[kind],
            "inputs": [
                record("cloud/eight-mi300x-campaign/production_job.py"),
                *[record(path) for path in inputs],
            ],
            "outputs": outputs, "solver_versions": {},
            "authority_status": authority, "timeout_seconds": timeout,
            "max_attempts": 1, "long_job": long, "command": command,
        }
        if depends:
            value["depends_on"] = depends
        return value

    config = {
        "schema": "multi-gpu-campaign/1",
        "campaign_id": "porsche-controller-pcb-components-1x-mi300x-production",
        "policy": {
            "maximum_runtime_seconds": 17_400,
            "no_start_after_seconds": 14_400,
            "destroy_deadline_seconds": 18_000,
        },
        "lanes": [{"gpu": 0, "rocr_visible_devices": "0"}],
        "jobs": [
            job("porsche-seed-200", "porsche_omni_candidate",
                "tencent/Hunyuan3D-Omni@70e803bfb4e127d534049d8ab8c8cb511780d485",
                omni, "porsche", [
                    "references/porsche-911-turbo-s/reference-audit.json",
                ], ["result.json", "request.json", "candidate-seed-200.glb"],
                4_800, "visualization_only", long=True),
            job("hero-components", "component_visualization",
                "supplier-ap242-deterministic", support, "components", [
                    "acceptance/agent-workflow-controller/generated/step-models.csv",
                    "cloud/eight-mi300x-campaign/component-bindings.json",
                ], ["result.json", "component-inventory.json"], 1_200,
                "supplier_ap242_visualization_evidence",
                depends=["porsche-seed-200"]),
            job("surface-fit", "curvature_bspline_fit",
                "deterministic-bspline-fit/v1", shape, "surface-fit", [
                    "references/porsche-911-turbo-s/generation/"
                    "approved-six-view-visual-hull.glb",
                ], ["result.json", "fit-result.json", "deviation-heatmap.ply"],
                3_600, "deterministic_fit_evidence_reconstruction_required",
                depends=["hero-components"], long=True),
            job("enclosure-physics", "topology_structural_thermal",
                "designstudio-support-v1+mfem@d9d6526cc1749980a2ba1da16e2c1ca1e07d82ec",
                support, "enclosure", [
                    "cloud/mi300x-designstudio/rib-pocket-experiment-request-v2.json",
                    "cloud/eight-mi300x-campaign/physics-inputs.json",
                ], ["result.json", "support-result.json",
                    "mfem-hip-order-1.log", "mfem-hip-order-2.log",
                    "mfem-hip-order-3.log"], 3_600,
                "proposal_only_full_physics_release_gate_open",
                depends=["surface-fit"], secondary=mfem, long=True),
            job("qwen-expert-router", "expert_router",
                "Qwen/Qwen3.5-35B-A3B@62704185bd97ad488cfc404e7caea797396b74dc",
                vllm, "qwen", [
                    "acceptance/agent-workflow-controller/generated/"
                    "verification-report.json",
                ], ["result.json", "qwen-response.json"], 1_500,
                "proposal_only", depends=["enclosure-physics"]),
            job("cpu-pcb-verifier", "pcb_drc_si_pdn_thermal",
                "designstudio-pcb-input-validator/v1", support, "pcb", [
                    "acceptance/agent-workflow-controller/generated/"
                    "verification-report.json",
                ], ["result.json", "input-validation.json"], 900,
                "deterministic_baseline_evidence",
                depends=["qwen-expert-router"]),
        ],
    }
    path = HERE / "campaign.single-gpu.production.json"
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    print(path)


if __name__ == "__main__":
    main()
