#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "freecad" / "DesignStudioWorkbench"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKBENCH))

from DesignStudio.live_tools import execute


def run(operation, arguments):
    return json.loads(execute(operation, json.dumps(arguments)))


def main():
    with tempfile.TemporaryDirectory(prefix="designstudio-codesign-live-") as raw:
        workspace = Path(raw)
        contracts = workspace / "contracts"
        contracts.mkdir()
        topology_input = contracts / "topology-input.json"
        topology_input.write_text(json.dumps({
            "product_id": "controller-fixture",
            "components": [
                {"component_id": "logic", "functional_group": "logic", "position_mm": [50, 40, 2]},
                {"component_id": "left", "functional_group": "left-grip", "position_mm": [10, 70, 5]},
                {"component_id": "right", "functional_group": "right-grip", "position_mm": [150, 70, 5]},
            ],
            "nets": [{"net_id": "input", "component_ids": ["logic", "left", "right"],
                      "criticality_weight": 2.0}],
            "constraints": {"distributed_surfaces": True, "moving_crossing": False,
                            "flex_allowed": True, "harness_allowed": True},
        }))
        result = run("create_pcb_topology_study", {
            "workspace_root": str(workspace), "mechanical_path": str(workspace / "product.FCStd"),
            "source_path": str(topology_input), "study_id": "controller-topology",
        })
        assert result["ok"], result
        study_path = Path(result["data"]["study_path"])
        study = json.loads(study_path.read_text())
        assert len(study["candidates"]) == 3 and study["approval_required"]
        assert study["ranking_status"] == "provisional"

        mechanical_input = contracts / "mechanical-input.json"
        mechanical_input.write_text(json.dumps({
            "product_id": "controller-fixture",
            "pcb_islands": [{"island_id": "logic"}, {"island_id": "left"}],
            "loads": {"assembly_load_n": 60, "safety_factor": 2},
            "heat_sources": [{"component_id": "regulator", "power_w": 3.5}],
            "thermal": {"ambient_c": 35, "maximum_component_c": 85},
            "cables": [{"cable_id": "usb-c"}],
        }))
        mechanical = run("derive_mechanical_component_requirements", {
            "workspace_root": str(workspace), "mechanical_path": str(workspace / "product.FCStd"),
            "source_path": str(mechanical_input), "requirements_id": "controller-mechanics",
        })
        assert mechanical["ok"], mechanical
        record = json.loads(Path(mechanical["data"]["requirements_path"]).read_text())
        assert {item["kind"] for item in record["requirements"]} >= {
            "fastener", "carrier", "heatsink", "strain_relief"}
        assert all(status != "pass" for status in record["analysis_summary"].values())

        outside = workspace.parent / "outside-codesign.json"
        outside.write_text("{}")
        escaped = run("create_pcb_topology_study", {
            "workspace_root": str(workspace), "source_path": str(outside)})
        assert not escaped["ok"] and "inside" in escaped["message"]
        outside.unlink()
    print("PHYSICAL_CODESIGN_LIVE_TOOLS_OK")


if __name__ == "__main__":
    main()
