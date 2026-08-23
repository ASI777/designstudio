#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.distributed_robot import (  # noqa: E402
    analyze_distributed_robot, axis_template_groups, build_distributed_product_graph,
)


def axis(index: int, *, current: float | None = None) -> dict:
    current = current if current is not None else index
    item = {
        "axis_id": f"axis-{index}",
        "motor": {"bus_voltage_v": 48, "continuous_current_a": current,
                  "peak_current_a": current * 2, "stage_continuous_rating_a": current + 2,
                  "stage_peak_rating_a": current * 2 + 2, "stage_maximum_v": 60,
                  "effective_phase_resistance_ohm": 0.01, "switching_loss_w": 0.5},
        "encoder": {"type": "absolute", "current_a": 0.05},
        "brake": {"type": "holding", "current_a": 0.2},
        "thermal": {"ambient_c": 40, "theta_ja_c_per_w": 2,
                    "maximum_junction_c": 125},
        "safety": {"stop_category": 1},
        "cable": {"current_rating_a": current * 2 + 1,
                  "connector_rating_a": current * 2 + 1},
        "assumptions": [],
        "spice_model": {"validated": True,
                        "generated_netlist": "V1 in 0 48\nR1 in 0 48\n.op\n.end"},
    }
    item["engineering_evidence"] = {name: {
        "status": "pass", "evidence_kind": "simulated",
        "input_digest": "a" * 64, "output_digest": "b" * 64}
        for name in ("erc", "required_externals", "reference_circuit", "stability",
                     "safety", "routing", "fabrication")}
    import hashlib
    payload = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    item["requirements_digest"] = hashlib.sha256(payload).hexdigest()
    return item


def application(axes: list[dict]) -> dict:
    architecture = {
        "kind": "distributed_six_axis_robot", "input_digest": "a" * 64,
        "supply_bus": {"minimum_v": 42, "maximum_v": 54,
                       "coordinator_connector_rating_a": 30,
                       "coordinator_peak_rating_a": 60},
        "can": {"topology_evidence": {"termination_count": 2, "node_count": 7}},
        "estop": {"propagation": "coordinator_to_all_six_joint_safe_torque_off_inputs",
                  "expectations": {"category": 1}},
        "braking": {"regeneration_evidence": {"mode": "local_dump"}},
        "engineering_evidence": {name: {
            "status": "pass", "evidence_kind": "simulated",
            "input_digest": "c" * 64, "output_digest": "d" * 64}
            for name in ("erc", "power_tree", "regulator_loading", "dropout_current_limit",
                         "stability", "safety", "routing", "thermal", "fabrication")},
    }
    import hashlib
    architecture["output_digest"] = hashlib.sha256(json.dumps(
        architecture, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"axis_requirements": axes, "system_architecture": architecture}


def fake_ngspice(root: Path) -> str:
    path = root / "ngspice"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def main() -> None:
    axes = [axis(index) for index in range(1, 7)]
    graph = build_distributed_product_graph(axes, application(axes)["system_architecture"])
    assert len([node for node in graph["nodes"] if "joint-" in node["assembly_path"]]) == 6
    assert len(graph["slots"]) == 6
    groups = axis_template_groups([axis(1, current=2), axis(2, current=2)],
                                  {"axis-1": "f" * 64, "axis-2": "f" * 64})
    # Axis identity is part of the independently captured row, so these remain
    # distinct until an upstream normalizer supplies the same requirements digest.
    assert len(groups) == 2
    identical = [axis(1, current=2), axis(2, current=2)]
    identical[1]["requirements_digest"] = identical[0]["requirements_digest"]
    groups = axis_template_groups(identical,
                                  {"axis-1": "f" * 64, "axis-2": "f" * 64})
    assert len(groups) == 1

    with tempfile.TemporaryDirectory(prefix="ds-spice-") as raw:
        report = analyze_distributed_robot(application(axes), spice_solver=fake_ngspice(Path(raw)))
    assert report["status"] == "pass"
    assert report["coordinator_report"]["calculated"]["aggregate_continuous_bus_current_a"] == 21
    assert len(report["axis_reports"]) == 6
    assert all(len(item["input_digest"]) == len(item["output_digest"]) == 64
               for item in report["axis_reports"])
    assert report["evidence_language"]["physical_measurement"] is False

    hot = [axis(index) for index in range(1, 7)]
    hot[5]["thermal"]["maximum_junction_c"] = 40
    hot[0]["spice_model"]["validated"] = False
    incomplete = analyze_distributed_robot(application(hot), spice_solver=None)
    assert incomplete["status"] in ("fail", "incomplete")
    assert "junction_temperature" in incomplete["release_blockers"]
    assert "spice" in incomplete["release_blockers"]
    print("Distributed six-axis robot tests passed")


if __name__ == "__main__":
    main()
