#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.product_propagation import (  # noqa: E402
    AnalysisRule, ProductPropagation, margin_solver, runtime_solver, thermal_solver,
)


CONFIGURATION = "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44"


def test_affected_subgraph_and_uncertainty():
    graph = {
        "schema": "design-studio.product-graph/1", "revision": 4,
        "nodes": [
            {"id": "battery"}, {"id": "shell"}, {"id": "power"},
            {"id": "mass"}, {"id": "thermal"}, {"id": "control"},
            {"id": "clearance"},
        ],
        "edges": [
            {"from": "battery", "to": "power", "kind": "constrains"},
            {"from": "battery", "to": "mass", "kind": "constrains"},
            {"from": "power", "to": "thermal", "kind": "depends_on"},
            {"from": "mass", "to": "control", "kind": "depends_on"},
            {"from": "shell", "to": "mass", "kind": "constrains"},
            {"from": "shell", "to": "clearance", "kind": "constrains"},
            {"from": "clearance", "to": "control", "kind": "depends_on"},
        ],
    }
    rules = [
        AnalysisRule("runtime", "runtime", "electrical", frozenset({"power"}),
                     frozenset({"battery_energy_wh", "average_load_w", "reserve_fraction",
                                "required_runtime_h", "runtime_uncertainty_h"}),
                     "duty_cycle_energy_model", runtime_solver, "PWR-01"),
        AnalysisRule("thermal", "thermal", "thermal", frozenset({"thermal"}),
                     frozenset({"ambient_c", "loss_w", "theta_c_per_w",
                                "maximum_junction_c", "thermal_uncertainty_c"}),
                     "steady_state_lumped", thermal_solver, "THERM-01"),
        AnalysisRule("clearance", "clearance", "mechanical", frozenset({"clearance"}),
                     frozenset({"minimum_clearance_mm", "required_clearance_mm",
                                "clearance_uncertainty_mm"}), "brep_distance",
                     margin_solver(value_key="minimum_clearance_mm",
                                   requirement_key="required_clearance_mm", unit="mm",
                                   uncertainty_key="clearance_uncertainty_mm"), "MECH-01"),
        AnalysisRule("control", "control", "control", frozenset({"control"}),
                     frozenset({"static_balance_deg", "required_balance_deg",
                                "control_uncertainty_deg"}), "rigid_body_balance",
                     margin_solver(value_key="static_balance_deg",
                                   requirement_key="required_balance_deg", unit="deg",
                                   uncertainty_key="control_uncertainty_deg"), "CTRL-01"),
        AnalysisRule("deep_fea", "strength", "mechanical", frozenset({"shell"}),
                     frozenset({"load_case"}), "calculix", None, "MECH-02"),
    ]
    values = {
        "battery_energy_wh": 42.0, "average_load_w": 18.0, "reserve_fraction": 0.15,
        "required_runtime_h": 1.5, "runtime_uncertainty_h": 0.12,
        "ambient_c": 40.0, "loss_w": 1.2, "theta_c_per_w": 34.0,
        "maximum_junction_c": 110.0, "thermal_uncertainty_c": 5.0,
        "minimum_clearance_mm": 2.8, "required_clearance_mm": 2.0,
        "clearance_uncertainty_mm": 0.15, "static_balance_deg": 4.7,
        "required_balance_deg": 3.0, "control_uncertainty_deg": 0.2,
        "load_case": "drop-1m",
    }
    propagation = ProductPropagation(graph, rules)
    battery = propagation.run(configuration_id=CONFIGURATION,
                              changed_nodes={"battery"}, values=values,
                              engine_versions={"electrical": "designcore/1",
                                               "thermal": "designcore/1",
                                               "control": "designcore/1"})
    assert battery["executed_analyses"] == ["runtime", "thermal", "control"]
    assert battery["unaffected_analyses"] == ["clearance", "deep_fea"]
    assert battery["causal_paths"]["control"] == ["battery", "mass", "control"]
    assert battery["status"] == "pass"

    shell = propagation.run(configuration_id=CONFIGURATION,
                            changed_nodes={"shell"}, values=values,
                            engine_versions={"electrical": "designcore/1",
                                             "thermal": "designcore/1",
                                             "control": "designcore/1"})
    assert shell["executed_analyses"] == ["clearance", "deep_fea"]
    assert shell["reused_analyses"] == ["control"]
    fea = next(item for item in shell["evidence"] if item["gate"] == "strength")
    assert fea["status"] == "incomplete" and "unavailable" in fea["uncertainty"]
    assert shell["status"] == "incomplete"
    clearance = next(item for item in shell["evidence"] if item["gate"] == "clearance")
    assert abs(clearance["margin"] - 0.8) < 1e-12 and clearance["status"] == "pass"


if __name__ == "__main__":
    test_affected_subgraph_and_uncertainty()
    print("Product propagation tests passed")
