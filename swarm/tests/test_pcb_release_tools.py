from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_tool(name: str, project: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / name), str(project), *args],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def base_project() -> dict:
    return {
        "copper_layers": 4,
        "footprints": [{
            "ref": "J1", "x_mm": 2.0, "y_mm": 2.0,
            "rot_deg": 0, "side": 0,
            "pads": [
                {"name": "1", "x_mm": 0.0, "y_mm": 0.0,
                 "w_mm": 1.0, "h_mm": 1.0, "net": 0, "th": True},
                {"name": "M1", "x_mm": 0.0, "y_mm": 2.0,
                 "w_mm": 1.0, "h_mm": 1.0, "net": 0, "th": True},
            ],
        }],
        "traces": [{
            "ax_mm": 2.0, "ay_mm": 2.0,
            "bx_mm": 5.0, "by_mm": 2.0,
            "w_mm": 0.2, "layer": 0, "net": 0, "pour": False,
        }, {
            "ax_mm": 5.0, "ay_mm": 2.0,
            "bx_mm": 5.0, "by_mm": 5.0,
            "w_mm": 0.2, "layer": 0, "net": 0, "pour": False,
        }],
        "vias": [],
        "placement_state": {},
        "routing_provenance": [],
    }


def test_controller_led_package_has_approved_four_pad_contract() -> None:
    path = ROOT / "tools" / "build_agent_workflow_controller.py"
    spec = importlib.util.spec_from_file_location("controller_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    led = module.packages()["led"]
    assert led.mpn == "IN-PI20TBT5R5G5B"
    assert [pad["number"] for pad in led.pads] == ["1", "2", "3", "4"]
    assert [(pin[0], pin[1], pin[2]) for pin in led.pins] == [
        ("1", "VDD", "power_in"),
        ("2", "DOUT", "output"),
        ("3", "GND", "power_in"),
        ("4", "DIN", "input"),
    ]


def test_move_preserves_only_attached_route_endpoints(tmp_path: Path) -> None:
    project = tmp_path / "move.dsproj"
    project.write_text(json.dumps(base_project()))
    result = run_tool(
        "move_routed_footprints.py", project, "--move", "J1:3:4")
    data = json.loads(project.read_text())
    assert result["moved_trace_endpoints"] == 1
    assert (data["traces"][0]["ax_mm"], data["traces"][0]["ay_mm"]) == (3.0, 4.0)
    assert (data["traces"][0]["bx_mm"], data["traces"][0]["by_mm"]) == (5.0, 2.0)
    assert data["footprints"][0]["x_mm"] == 3.0
    assert data["footprints"][0]["y_mm"] == 4.0


def test_pad_bridge_carries_branch_current_contract(tmp_path: Path) -> None:
    project = tmp_path / "bridge.dsproj"
    project.write_text(json.dumps(base_project()))
    result = run_tool(
        "add_pad_bridges.py", project,
        "--bridge", "J1:1:M1:3:0.25",
        "--required-current", "0.35")
    data = json.loads(project.read_text())
    assert len(result["added"]) == 1
    bridge = data["traces"][-1]
    assert bridge["layer"] == 3
    assert bridge["required_current_a"] == 0.35
    assert bridge["min_width_override_mm"] == 0.25


def test_corner_optimizer_replaces_true_right_angle(tmp_path: Path) -> None:
    project = tmp_path / "corner.dsproj"
    data = base_project()
    data["footprints"] = []
    project.write_text(json.dumps(data))
    result = run_tool(
        "optimize_route_corners.py", project, "--setback", "0.05")
    optimized = json.loads(project.read_text())
    assert result["optimized_corners"] == 1
    assert len(optimized["traces"]) == 3
    diagonal = optimized["traces"][-1]
    assert diagonal["ax_mm"] != diagonal["bx_mm"]
    assert diagonal["ay_mm"] != diagonal["by_mm"]
    assert optimized["routing_release_operations"][-1][
        "remaining_right_angle_corners"] == 0


def test_route_waypoint_preserves_segment_electrical_metadata(
        tmp_path: Path) -> None:
    project = tmp_path / "waypoint.dsproj"
    data = base_project()
    data["traces"] = [{
        "ax_mm": 1.0, "ay_mm": 1.0, "bx_mm": 4.0, "by_mm": 4.0,
        "w_mm": 0.5, "layer": 0, "net": 3, "pour": False,
        "required_current_a": 0.75, "min_width_override_mm": 0.5,
    }]
    project.write_text(json.dumps(data))
    run_tool(
        "add_route_waypoint.py", project,
        "--segment", "3:0:1:1:4:4:1:2")
    routed = json.loads(project.read_text())
    assert len(routed["traces"]) == 2
    assert all(trace["required_current_a"] == 0.75
               for trace in routed["traces"])
    assert routed["traces"][0]["bx_mm"] == 1.0
    assert routed["traces"][0]["by_mm"] == 2.0
