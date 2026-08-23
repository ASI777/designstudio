"""Distributed six-axis robot product graph and hierarchical analysis.

All values are calculations, simulations, assumptions, or missing evidence.
Nothing emitted by this module is described as a physical measurement.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .spice_simulation import run_validated_spice


def _canonical(value: Any) -> bytes:
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _uuid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"designstudio:distributed-robot:{name}"))


def _status(items: list[dict]) -> str:
    states = {item.get("status") for item in items}
    return ("fail" if "fail" in states else "incomplete" if "incomplete" in states
            else "pass" if items else "not_applicable")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _check(name: str, status: str, message: str, *, kind: str = "calculated",
           value: float | None = None, unit: str | None = None,
           limit: float | None = None, margin: float | None = None) -> dict:
    return {"name": name, "status": status, "evidence_kind": kind,
            "message": message, "value": value, "unit": unit,
            "limit": limit, "margin": margin}


def _limit_check(name: str, value: float | None, limit: float | None, unit: str,
                 *, higher_is_better: bool = False) -> dict:
    if value is None or limit is None:
        return _check(name, "incomplete", f"{name} lacks a required value or limit",
                      kind="missing_evidence", unit=unit)
    margin = value - limit if higher_is_better else limit - value
    return _check(name, "pass" if margin >= 0 else "fail",
                  f"Calculated {name} has {margin:g} {unit} margin",
                  value=value, unit=unit, limit=limit, margin=margin)


def _evidence_checks(evidence: dict, required: tuple[str, ...]) -> list[dict]:
    checks = []
    for name in required:
        item = evidence.get(name) if isinstance(evidence, dict) else None
        digest_ok = isinstance(item, dict) and all(
            isinstance(item.get(key), str) and len(item[key]) == 64
            for key in ("input_digest", "output_digest"))
        status = item.get("status") if isinstance(item, dict) else None
        if status not in ("pass", "fail") or not digest_ok:
            checks.append(_check(name, "incomplete",
                                 f"{name} lacks digest-bound engineering evidence",
                                 kind="missing_evidence"))
        else:
            checks.append(_check(name, status,
                                 str(item.get("message") or f"{name} evidence is {status}"),
                                 kind=str(item.get("evidence_kind") or "simulated")))
    return checks


def build_distributed_product_graph(axis_requirements: list[dict],
                                    system_architecture: dict) -> dict:
    if len(axis_requirements) != 6:
        raise ValueError("distributed robot requires exactly six axis requirements")
    product, coordinator = _uuid("product"), _uuid("coordinator")
    nodes = [{"id": product, "name": "Six-axis robot electronics",
              "assembly_path": "robot/electronics", "domain": "product",
              "authority": "product_graph", "source_ref": "distributed-robot"},
             {"id": coordinator, "name": "CAN and E-stop coordinator",
              "assembly_path": "robot/electronics/coordinator", "domain": "electronic",
              "authority": "electronics", "source_ref": "board:coordinator",
              "requirements": ["aggregate-power", "CAN", "E-stop", "braking"]}]
    edges = [{"from": product, "to": coordinator, "kind": "contains"}]
    slots, variants = [], []
    for index, requirement in enumerate(axis_requirements, 1):
        axis_id, node_id = f"axis-{index}", _uuid(f"joint-{index}")
        digest = requirement.get("requirements_digest") or _digest(requirement)
        nodes.append({"id": node_id, "name": f"Joint controller {index}",
                      "assembly_path": f"robot/electronics/joint-{index}",
                      "domain": "electronic", "authority": "electronics",
                      "source_ref": f"board:joint-{index}",
                      "requirements": [axis_id, digest]})
        edges += [{"from": product, "to": node_id, "kind": "contains"},
                  {"from": node_id, "to": coordinator, "kind": "depends_on"},
                  {"from": coordinator, "to": node_id, "kind": "connects"}]
        slot = f"slot.joint_{index}"
        slots.append({"id": slot, "name": f"Joint board {index}", "node_id": node_id,
                      "assembly_path": f"robot/electronics/joint-{index}",
                      "allowed_change_class": "C4",
                      "protected_properties": ["supply", "CAN", "E-stop"],
                      "customizable_properties": ["motor", "encoder", "brake", "thermal"],
                      "required_evidence": ["electrical", "thermal", "safety", "routing", "simulation"]})
        variants.append({"id": f"joint_{index}.isolated", "slot_id": slot,
                         "name": f"Axis {index} isolated configuration", "change_class": "C4",
                         "parameters": {"requirements_digest": digest}, "failed_gates": [],
                         "incomplete_gates": ["component_binding", "simulation", "fabrication"]})
    graph = {"schema": "design-studio.product-graph/1", "product_id": product,
             "revision": 1, "nodes": nodes, "edges": edges, "slots": slots,
             "variants": variants, "system_architecture_digest": _digest(system_architecture)}
    graph["graph_digest"] = _digest(graph)
    return graph


def axis_template_groups(axis_requirements: list[dict],
                         artifact_digests: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Group only axes with equal requirement *and* resulting artifact digests."""
    artifact_digests = artifact_digests or {}
    groups: dict[str, list[str]] = {}
    for index, axis in enumerate(axis_requirements, 1):
        axis_id = str(axis.get("axis_id") or f"axis-{index}")
        requirement = str(axis.get("requirements_digest") or _digest(axis))
        artifact = artifact_digests.get(axis_id)
        key = _digest({"requirements": requirement, "artifact": artifact}) if artifact else axis_id
        groups.setdefault(key, []).append(axis_id)
    return groups


def _analyze_axis(axis: dict, supply: dict, *, spice_solver: str | None) -> dict:
    axis_id = str(axis.get("axis_id") or "axis-unknown")
    motor, encoder = axis.get("motor") or {}, axis.get("encoder") or {}
    brake, thermal = axis.get("brake") or {}, axis.get("thermal") or {}
    cable = axis.get("cable") or {}
    continuous = _number(motor.get("continuous_current_a"))
    peak = _number(motor.get("peak_current_a"))
    voltage = _number(motor.get("bus_voltage_v"))
    checks = [
        _limit_check("continuous_stage_current", continuous,
                     _number(motor.get("stage_continuous_rating_a")), "A"),
        _limit_check("peak_stage_current", peak, _number(motor.get("stage_peak_rating_a")), "A"),
        _limit_check("connector_current", peak, _number(cable.get("connector_rating_a")), "A"),
        _limit_check("cable_current", peak, _number(cable.get("current_rating_a")), "A"),
        _limit_check("stage_voltage", _number(supply.get("maximum_v")),
                     _number(motor.get("stage_maximum_v")), "V"),
    ]
    digest_payload = {key: value for key, value in axis.items()
                      if key not in ("requirements_digest", "template_reuse_key")}
    declared_digest = axis.get("requirements_digest")
    checks.append(_check(
        "axis_requirement_digest",
        "pass" if declared_digest == _digest(digest_payload) else "fail",
        "Axis requirement digest is intact" if declared_digest == _digest(digest_payload)
        else "Axis requirement bytes changed after sizing",
        kind="calculated"))
    rds = _number(motor.get("effective_phase_resistance_ohm"))
    switching = _number(motor.get("switching_loss_w"))
    loss = None if continuous is None or rds is None else 3.0 * continuous * continuous * rds + (switching or 0.0)
    ambient = _number(thermal.get("ambient_c"))
    theta = _number(thermal.get("theta_ja_c_per_w"))
    junction = None if None in (loss, ambient, theta) else ambient + loss * theta
    checks.append(_limit_check("junction_temperature", junction,
                               _number(thermal.get("maximum_junction_c")), "degC"))
    encoder_current = _number(encoder.get("current_a"))
    brake_current = _number(brake.get("current_a"))
    checks.append(_check("encoder_loading", "pass" if encoder_current is not None else "incomplete",
                         "Encoder load is specified" if encoder_current is not None
                         else "Encoder current is missing", kind="calculated" if encoder_current is not None
                         else "missing_evidence", value=encoder_current, unit="A"))
    checks.append(_check("brake_loading", "pass" if brake_current is not None else "incomplete",
                         "Brake load is specified" if brake_current is not None else "Brake current is missing",
                         kind="calculated" if brake_current is not None else "missing_evidence",
                         value=brake_current, unit="A"))
    checks.extend(_evidence_checks(axis.get("engineering_evidence") or {}, (
        "erc", "required_externals", "reference_circuit", "stability",
        "safety", "routing", "fabrication")))
    spice = run_validated_spice(axis.get("spice_model"), solver=spice_solver)
    checks.append({"name": "spice", **spice})
    payload = {"axis_id": axis_id,
               "input_digest": axis.get("requirements_digest") or _digest(axis),
               "status": _status(checks), "checks": checks,
               "calculated": {"continuous_bus_current_a": continuous,
                              "peak_bus_current_a": peak, "power_loss_w": loss,
                              "junction_temperature_c": junction,
                              "auxiliary_current_a": None if encoder_current is None or brake_current is None
                                                     else encoder_current + brake_current},
               "simulated": {"spice": spice},
               "assumptions": list(axis.get("assumptions") or []),
               "missing_evidence": [item["name"] for item in checks if item["status"] == "incomplete"]}
    payload["output_digest"] = _digest(payload)
    return payload


def analyze_distributed_robot(application: dict, *, spice_solver: str | None = None) -> dict:
    axes = application.get("axis_requirements") or []
    architecture = application.get("system_architecture") or {}
    if len(axes) != 6 or architecture.get("kind") != "distributed_six_axis_robot":
        raise ValueError("analysis requires the distributed six-axis application contract")
    supply_bundle = architecture.get("supply_bus") or {}
    supply = supply_bundle.get("supply", supply_bundle) if isinstance(supply_bundle, dict) else {}
    if not isinstance(supply, dict):
        supply = {}
    axis_reports = [_analyze_axis(axis, supply, spice_solver=spice_solver) for axis in axes]
    continuous_values = [report["calculated"]["continuous_bus_current_a"] for report in axis_reports]
    peak_values = [report["calculated"]["peak_bus_current_a"] for report in axis_reports]
    aggregate_continuous = sum(continuous_values) if all(value is not None for value in continuous_values) else None
    aggregate_peak = sum(peak_values) if all(value is not None for value in peak_values) else None
    can_contract = architecture.get("can") or {}
    can_evidence = can_contract.get("topology_evidence") or {}
    can = can_evidence.get("can", can_evidence) if isinstance(can_evidence, dict) else {}
    if not isinstance(can, dict):
        can = {}
    can = {**can_contract, **can}
    estop = architecture.get("estop") or {}
    braking = architecture.get("braking") or {}
    coordinator_checks = [
        _limit_check("coordinator_connector_continuous_current", aggregate_continuous,
                     _number(supply.get("coordinator_connector_rating_a")), "A"),
        _limit_check("coordinator_connector_peak_current", aggregate_peak,
                     _number(supply.get("coordinator_peak_rating_a")), "A"),
        _check("can_termination", "pass" if can.get("termination_count") == 2 else
               "incomplete" if can.get("termination_count") is None else "fail",
               "CAN requires exactly two endpoint terminations",
               kind="calculated" if can.get("termination_count") is not None else "missing_evidence",
               value=_number(can.get("termination_count")), unit="terminations", limit=2),
        _check("can_loading", "pass" if can.get("node_count", 7) == 7 else "fail",
               "Coordinator plus six joints gives seven CAN nodes", value=7, unit="nodes", limit=7),
        _check("estop_propagation", "pass" if estop.get("propagation") and estop.get("expectations") else "incomplete",
               "E-stop path reaches all six joint safe-torque-off inputs" if estop.get("expectations")
               else "E-stop expectations and validation evidence are missing",
               kind="calculated" if estop.get("expectations") else "missing_evidence"),
        _check("braking_regeneration", "pass" if braking.get("regeneration_evidence") else "incomplete",
               "Braking/regeneration assumptions are declared" if braking.get("regeneration_evidence")
               else "Braking energy destination and regeneration limits are missing",
               kind="assumption" if braking.get("regeneration_evidence") else "missing_evidence"),
    ]
    architecture_payload = {key: value for key, value in architecture.items()
                            if key != "output_digest"}
    architecture_digest = architecture.get("output_digest")
    coordinator_checks.append(_check(
        "system_architecture_digest",
        "pass" if architecture_digest == _digest(architecture_payload) else "incomplete"
        if architecture_digest is None else "fail",
        "System architecture digest is intact" if architecture_digest == _digest(architecture_payload)
        else "System architecture digest is missing or stale",
        kind="calculated" if architecture_digest is not None else "missing_evidence"))
    coordinator_checks.extend(_evidence_checks(
        architecture.get("engineering_evidence") or {}, (
            "erc", "power_tree", "regulator_loading", "dropout_current_limit",
            "stability", "safety", "routing", "thermal", "fabrication")))
    coordinator = {"board_id": "coordinator", "input_digest": _digest({
        "architecture": architecture.get("input_digest"),
        "axis_outputs": [report["output_digest"] for report in axis_reports]}),
        "status": _status(coordinator_checks), "checks": coordinator_checks,
        "calculated": {"aggregate_continuous_bus_current_a": aggregate_continuous,
                       "aggregate_peak_bus_current_a": aggregate_peak}}
    coordinator["output_digest"] = _digest(coordinator)
    all_checks = [item for report in axis_reports for item in report["checks"]] + coordinator_checks
    report = {"schema": "design-studio.distributed-robot-analysis/1",
              "status": _status(all_checks), "evidence_language": {
                  "calculated": "deterministic calculation", "simulated": "solver output",
                  "assumption": "declared assumption", "missing_evidence": "incomplete gate",
                  "physical_measurement": False},
              "axis_reports": axis_reports, "coordinator_report": coordinator,
              "categories": {name: {"status": _status([
                  item for item in all_checks if item.get("name") == name]),
                  "findings": [item for item in all_checks if item.get("name") == name]}
                  for name in sorted({item.get("name") for item in all_checks})},
              "system_report": {"input_digest": coordinator["input_digest"],
                                "axis_output_digests": [report["output_digest"] for report in axis_reports],
                                "coordinator_output_digest": coordinator["output_digest"],
                                "status": _status(all_checks)},
              "release_blockers": [item["name"] for item in all_checks
                                   if item.get("status") in ("fail", "incomplete")]}
    report["system_report"]["output_digest"] = _digest(report["system_report"])
    report["report_digest"] = _digest(report)
    return report
