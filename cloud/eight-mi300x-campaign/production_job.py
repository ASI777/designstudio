#!/usr/bin/env python3
"""Production work-package adapters for the bounded single-MI300X campaign."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def docker_run(
    image: str, argv: list[str], output: Path, *, gpu: bool = False,
    cache: str | None = None,
) -> None:
    command = [
        "docker", "run", "--rm",
        "-v", f"{ROOT}:/workspace:ro",
        "-v", f"{output}:/output",
        "-w", "/workspace",
    ]
    if gpu:
        command += [
            "--device=/dev/kfd", "--device=/dev/dri", "--group-add", "video",
            "--group-add", "992", "--ipc=host",
            "--security-opt", "seccomp=unconfined",
        ]
    if cache:
        hy3dgen = str(Path(cache).parent / "hy3dgen")
        command += [
            "-v", f"{cache}:/home/designstudio/.cache/huggingface",
            "-v", f"{hy3dgen}:/home/designstudio/.cache/hy3dgen",
        ]
    command += ["--entrypoint", "python3", image, *argv]
    with (output / "container.log").open("ab") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)


def porsche(output: Path, image: str, cache: str) -> dict:
    from tools.run_turbo_s_generation import load_json, omni_request

    audit = load_json(
        ROOT / "references/porsche-911-turbo-s/reference-audit.json"
    )
    request = omni_request(audit)
    request["candidate_count"] = 1
    request["seeds"] = [200]
    package = output / "request.json"
    write_json(package, request["input_package"])
    output.chmod(0o777)
    cache_path = Path(cache)
    cache_path.mkdir(parents=True, exist_ok=True)
    hy3dgen_path = cache_path.parent / "hy3dgen"
    hy3dgen_path.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "chown", "-R", "10001:10001", str(cache_path), str(hy3dgen_path),
    ], check=True)
    runner = (
        "import json,sys; sys.path.insert(0,'/opt/service'); "
        "from pathlib import Path; "
        "from omni_runner import generate_omni_candidate; "
        "p=json.load(open('/output/request.json')); "
        "generate_omni_candidate(package=p, seed=200, "
        "output_path=Path('/output/candidate-seed-200.glb'))"
    )
    docker_run(image, ["-c", runner], output, gpu=True, cache=cache)
    mesh = output / "candidate-seed-200.glb"
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "porsche_omni_candidate",
        "seed": 200,
        "model_revision":
            "tencent/Hunyuan3D-Omni@70e803bfb4e127d534049d8ab8c8cb511780d485",
        "request_sha256": sha(package),
        "mesh": {"path": mesh.name, "bytes": mesh.stat().st_size,
                 "sha256": sha(mesh)},
        "authority_status": "visualization_only",
    }


def components(output: Path, image: str) -> dict:
    inventory = output / "component-inventory.json"
    docker_run(image, [
        "/workspace/tools/component_cad_fidelity_inventory.py",
        "--project",
        "/workspace/acceptance/agent-workflow-controller/generated/"
        "agent-workflow-controller-v3.dsproj",
        "--components",
        "/workspace/acceptance/agent-workflow-controller/generated/components",
        "--output", "/output/component-inventory.json",
    ], output)
    bindings = json.loads((
        ROOT / "cloud/eight-mi300x-campaign/component-bindings.json"
    ).read_text())
    rows_path = (
        ROOT / "acceptance/agent-workflow-controller/generated/step-models.csv"
    )
    with rows_path.open(newline="", encoding="utf-8") as stream:
        rows = {(row["Manufacturer"], row["MPN"]): row
                for row in csv.DictReader(stream)}
    verified = []
    for item in bindings["components"]:
        row = rows[(item["manufacturer"], item["mpn"])]
        step = Path(row["STEP path"])
        if not step.is_file():
            step = (
                ROOT / "acceptance/agent-workflow-controller/generated/components"
                / step.parent.name / step.name
            )
        if sha(step) != item["step_sha256"]:
            raise RuntimeError(f"STEP digest mismatch for {item['mpn']}")
        if row["Schema"] != "AP242" or row["Round-trip"] != "True":
            raise RuntimeError(f"AP242 gate failed for {item['mpn']}")
        verified.append({
            "manufacturer": item["manufacturer"], "mpn": item["mpn"],
            "step_sha256": item["step_sha256"], "bytes": step.stat().st_size,
            "schema": "AP242", "round_trip": True,
        })
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "hero_component_evidence",
        "verified_components": verified,
        "inventory_sha256": sha(inventory),
        "authority_status": "supplier_ap242_visualization_evidence",
    }


def surface_fit(output: Path, image: str) -> dict:
    docker_run(image, [
        "/workspace/tools/fit_glb_bspline_surfaces.py",
        "--input",
        "/workspace/references/porsche-911-turbo-s/generation/"
        "approved-six-view-visual-hull.glb",
        "--output-directory", "/output",
        "--control-u", "6", "--control-v", "6",
        "--fairness", "1e-5", "--maximum-patches", "96",
    ], output)
    fit = output / "fit-result.json"
    value = json.loads(fit.read_text())
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "curvature_bspline_fit",
        "fit_result_sha256": sha(fit),
        "heatmap_sha256": sha(output / "deviation-heatmap.ply"),
        "deviation_metrics": value.get("deviation_metrics"),
        "authority_status": "deterministic_fit_evidence_reconstruction_required",
    }


def enclosure(output: Path, support_image: str, mfem_image: str) -> dict:
    request = (
        ROOT / "cloud/mi300x-designstudio/rib-pocket-experiment-request-v2.json"
    )
    support_runner = (
        "import json,sys; sys.path.insert(0,'/app'); "
        "from app import optimize_support_result; "
        "p=json.load(open('/workspace/cloud/mi300x-designstudio/"
        "rib-pocket-experiment-request-v2.json')); "
        "json.dump(optimize_support_result(p['spec']), "
        "open('/output/support-result.json','w'), indent=2, sort_keys=True)"
    )
    docker_run(support_image, ["-c", support_runner], output, gpu=True)
    mfem_runs = []
    for order in (1, 2, 3):
        log = output / f"mfem-hip-order-{order}.log"
        command = [
            "docker", "run", "--rm", "--device=/dev/kfd",
            "--device=/dev/dri", "--group-add", "video", "--group-add", "992",
            "--ipc=host", "--security-opt", "seccomp=unconfined",
            "--entrypoint", "/bin/bash", mfem_image, "-lc",
            "cd /opt/mfem/build/examples && "
            f"./ex1 -m ../data/star.mesh -o {order} -d hip -no-vis",
        ]
        with log.open("wb") as stream:
            subprocess.run(command, check=True, stdout=stream,
                           stderr=subprocess.STDOUT)
        mfem_runs.append({
            "polynomial_order": order,
            "log": log.name, "sha256": sha(log),
        })
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "enclosure_support_and_mfem_hip_screen",
        "request_sha256": sha(request),
        "support_result_sha256": sha(output / "support-result.json"),
        "mfem_commit": "d9d6526cc1749980a2ba1da16e2c1ca1e07d82ec",
        "hip_arch": "gfx942",
        "mfem_backend_runs": mfem_runs,
        "structural_thermal_convergence": "not_claimed",
        "authority_status": "proposal_only_full_physics_release_gate_open",
    }


def qwen(output: Path, base_url: str) -> dict:
    subprocess.run(["docker", "start", "campaign-vllm"], check=True,
                   stdout=subprocess.DEVNULL)
    models_url = base_url.rstrip("/") + "/v1/models"
    for _ in range(150):
        try:
            with urllib.request.urlopen(models_url, timeout=5):
                break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("pinned Qwen vLLM service did not become ready")
    verification = json.loads((
        ROOT / "acceptance/agent-workflow-controller/generated/"
        "verification-report.json"
    ).read_text())
    prompt = {
        "model": "Qwen/Qwen3.5-35B-A3B",
        "messages": [{
            "role": "user",
            "content": (
                "Act only as a proposal router. Given this deterministic PCB "
                "verification summary, return compact JSON with keys route, "
                "blocking_findings, and required_authorities. Never claim CAD "
                "or fabrication authority.\n" + json.dumps(
                    verification.get("categories", {}), sort_keys=True
                )
            ),
        }],
        "temperature": 0,
        "seed": 700,
        "max_tokens": 512,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(prompt).encode(), headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        raw = response.read()
    response_path = output / "qwen-response.json"
    response_path.write_bytes(raw)
    parsed = json.loads(raw)
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "qwen_expert_router",
        "seed": 700,
        "model_revision":
            "Qwen/Qwen3.5-35B-A3B@62704185bd97ad488cfc404e7caea797396b74dc",
        "response_sha256": sha(response_path),
        "response_id": parsed.get("id"),
        "authority_status": "proposal_only",
    }


def pcb(output: Path, image: str) -> dict:
    for name in ("component-bindings.json", "physics-inputs.json"):
        (output / name).write_bytes((
            ROOT / "cloud/eight-mi300x-campaign" / name
        ).read_bytes())
    runner = (
        "import importlib.util,sys; from pathlib import Path; "
        "s=importlib.util.spec_from_file_location('validate_inputs',"
        "'/workspace/cloud/eight-mi300x-campaign/validate_inputs.py'); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "m.ROOT=Path('/workspace'); m.HERE=Path('/output'); "
        "sys.exit(m.main())"
    )
    docker_run(image, [
        "-c", runner,
    ], output)
    source = output / "input-validation.json"
    value = json.loads(source.read_text())
    report = json.loads((
        ROOT / "acceptance/agent-workflow-controller/generated/"
        "verification-report.json"
    ).read_text())
    categories = {
        name: item.get("status")
        for name, item in report.get("categories", {}).items()
        if name in {"connectivity", "drc", "power_integrity",
                    "signal_integrity", "thermal"}
    }
    if set(categories.values()) != {"pass"}:
        raise RuntimeError(f"PCB verification categories failed: {categories}")
    return {
        "schema": "single-mi300x.production-result/1",
        "kind": "cpu_pcb_verifier",
        "source_sha256": sha(
            ROOT / "acceptance/agent-workflow-controller/generated/"
            "verification-report.json"
        ),
        "categories": categories,
        "input_validation_sha256": sha(output / "input-validation.json"),
        "authority_status": "deterministic_baseline_evidence",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=(
        "porsche", "components", "surface-fit", "enclosure", "qwen", "pcb",
    ))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image")
    parser.add_argument("--secondary-image")
    parser.add_argument("--cache", default="/workspace/model-cache/huggingface")
    parser.add_argument("--base-url", default="http://127.0.0.1:18011")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if args.kind == "porsche":
        value = porsche(output, args.image, args.cache)
    elif args.kind == "components":
        value = components(output, args.image)
    elif args.kind == "surface-fit":
        value = surface_fit(output, args.image)
    elif args.kind == "enclosure":
        value = enclosure(output, args.image, args.secondary_image)
    elif args.kind == "qwen":
        value = qwen(output, args.base_url)
    else:
        value = pcb(output, args.image)
    value["started_at_unix"] = started
    value["finished_at_unix"] = time.time()
    write_json(output / "result.json", value)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
