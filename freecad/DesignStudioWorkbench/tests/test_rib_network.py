from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from DesignStudio.manufacturing_rules import default_profile
from DesignStudio.rib_network import (RibNetworkError, build_rib_network,
                                       to_mechanical_program,
                                       validate_rib_network)


def spec():
    return {
        "schema": "design-studio.rib-network/1",
        "design_volume_mm": {"min": [0.0, 0.0, 0.0], "max": [100.0, 60.0, 80.0]},
        "anchors": [
            {"id": "boss-a", "position_mm": [20.0, 30.0, 15.0], "kind": "boss"},
            {"id": "pcb-a", "position_mm": [80.0, 30.0, 20.0], "kind": "pcb"},
        ],
        "obstacles": [{"id": "battery", "bounds": {
            "min": [35.0, 20.0, 0.0], "max": [65.0, 40.0, 50.0]}}],
        "load_cases": [{"anchor_id": "boss-a", "force_n": [0.0, 0.0, -20.0]}],
        "manufacturing": {"wall_mm": 2.0, "clearance_mm": 0.5,
                           "pull_direction": [0.0, 1.0, 0.0]},
        "profile": default_profile(),
        "seed": 11,
    }


def test_network_is_deterministic_and_not_boxes():
    first = build_rib_network(spec())
    second = build_rib_network(spec())
    assert first == second
    assert first["status"] == "pass"
    assert len(first["ribs"]) >= 2
    assert all("path_mm" in rib and len(rib["path_mm"]) == 2 for rib in first["ribs"])
    assert all("draft_deg" in rib and "root_fillet_mm" in rib for rib in first["ribs"])
    validate_rib_network(first, spec()["profile"])
    program = to_mechanical_program(first)
    assert program["schema"] == "design-studio.mechanical-cad-program/2"
    assert all(command["op"] == "feature.rib" for command in program["commands"])


def test_invalid_network_fails_closed():
    value = spec()
    value["anchors"][0]["position_mm"] = [1000.0, 0.0, 0.0]
    try:
        build_rib_network(value)
        raise AssertionError("out-of-volume anchor accepted")
    except RibNetworkError:
        pass


if __name__ == "__main__":
    test_network_is_deterministic_and_not_boxes()
    test_invalid_network_fails_closed()
    print("RIB_NETWORK_OK")
