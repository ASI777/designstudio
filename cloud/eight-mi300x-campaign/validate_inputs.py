#!/usr/bin/env python3
"""Validate all campaign input packages and acceptance thresholds locally."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    findings: list[str] = []
    bindings = json.loads((HERE / "component-bindings.json").read_text())
    table_path = ROOT / "acceptance/agent-workflow-controller/generated/step-models.csv"
    with table_path.open(newline="", encoding="utf-8") as stream:
        rows = {(row["Manufacturer"], row["MPN"]): row for row in csv.DictReader(stream)}
    for component in bindings["components"]:
        key = (component["manufacturer"], component["mpn"])
        row = rows.get(key)
        if row is None:
            findings.append(f"missing component binding: {key}")
            continue
        declared_step = Path(row["STEP path"])
        step = declared_step if declared_step.is_file() else (
            ROOT / "acceptance/agent-workflow-controller/generated/components"
            / declared_step.parent.name / declared_step.name
        )
        if not step.is_file() or sha(step) != component["step_sha256"]:
            findings.append(f"stale STEP authority: {key}")
        if row["Schema"] != "AP242" or row["Round-trip"] != "True":
            findings.append(f"STEP authority did not pass AP242 round-trip: {key}")

    porsche = ROOT / "references/porsche-911-turbo-s"
    for view in ("front", "rear", "left", "right", "top", "bottom"):
        if not (porsche / f"silhouettes/masks/{view}.png").is_file():
            findings.append(f"missing Porsche mask: {view}")
    physics = json.loads((HERE / "physics-inputs.json").read_text())
    request = json.loads(
        (ROOT / physics["geometry"]["request"]).read_text(encoding="utf-8")
    )
    anchors = {item["id"] for item in request["spec"]["anchors"]}
    for case in physics["structural_cases"]:
        referenced = {case["application_anchor"], *case["constraints"]}
        if not referenced <= anchors:
            findings.append(
                f"{case['id']} references unknown anchors: {sorted(referenced - anchors)}"
            )
    powers = physics["thermal"]["component_powers_w"]
    if not powers or any(float(value) <= 0 for value in powers.values()):
        findings.append("thermal powers must be explicit and positive")
    pcb = json.loads((ROOT /
        "acceptance/agent-workflow-controller/generated/verification-report.json"
    ).read_text())
    for category in ("connectivity", "drc", "power_integrity", "signal_integrity",
                     "thermal"):
        if pcb["categories"].get(category, {}).get("status") != "pass":
            findings.append(f"PCB baseline category does not pass: {category}")
    report = {
        "schema": "multi-gpu-campaign-input-validation/1",
        "status": "pass" if not findings else "fail",
        "findings": findings,
        "component_count": len(bindings["components"]),
        "porsche_mask_count": 6,
        "structural_case_count": len(physics["structural_cases"]),
        "thermal_power_count": len(powers),
        "pcb_source_sha256": sha(
            ROOT / "acceptance/agent-workflow-controller/generated/verification-report.json"
        ),
    }
    report["input_set_sha256"] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    (HERE / "input-validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
