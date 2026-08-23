"""Evidence-bound electronics selection and packaging checks.

This module deliberately stops short of treating a manufacturer product page as
a fabrication-ready component.  It validates exact orderable part identities,
the capabilities used by the architecture, and first-order board occupancy.
Symbols, land patterns, 3D models, application circuits, SI/PI and thermal
receipts remain separate artifacts that must be produced from the bound source
documents.
"""
from __future__ import annotations

import math
import re
from typing import Any


COMPONENT_PLAN_SCHEMA = "design-studio.electronics-component-plan/1"
SELECTION_STATUS = "datasheet_evidence_bound_candidate"

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_BINDING_FIELDS = {
    "binding_id", "manufacturer", "mpn", "role", "applies_to_board",
    "quantity_per_board", "package", "body_size_mm", "capabilities",
    "primary_evidence", "selection_status",
}
_EVIDENCE_FIELDS = {"url", "document", "revision", "observed_date", "fact_basis"}
_BOARD_ROLES = {"joint_logic", "joint_power"}
_ROLE_CAPABILITIES = {
    "motor_control_mcu": {
        "fdcan_instances", "package_accessible_fdcan_instances", "max_clock_mhz",
        "motor_control_pwm_timers", "operating_temperature_max_degc",
    },
    "can_fd_transceiver": {
        "can_fd_max_mbps", "bus_fault_abs_v", "io_voltage_min_v",
        "io_voltage_max_v", "operating_temperature_max_degc",
    },
    "three_phase_gate_driver": {
        "supply_abs_max_v", "current_shunt_amplifiers", "integrated_buck",
        "gate_drive_source_peak_a", "operating_temperature_max_degc",
    },
    "power_mosfet": {
        "vds_max_v", "rds_on_max_ohm", "qg_typ_nc", "id_max_a", "rth_jc_degc_per_w",
        "operating_temperature_max_degc",
    },
    "phase_current_shunt": {
        "resistance_ohm", "power_rating_w", "tolerance_percent",
        "operating_temperature_max_degc",
    },
    "logic_ldo": {
        "input_max_v", "output_v", "output_current_a",
        "operating_temperature_max_degc",
    },
}
_REQUIRED_QUANTITIES = {
    "motor_control_mcu": 1,
    "can_fd_transceiver": 2,
    "three_phase_gate_driver": 1,
    "power_mosfet": 6,
    "phase_current_shunt": 3,
    "logic_ldo": 1,
}
_EXPECTED_BOARD = {
    "motor_control_mcu": "joint_logic",
    "can_fd_transceiver": "joint_logic",
    "logic_ldo": "joint_logic",
    "three_phase_gate_driver": "joint_power",
    "power_mosfet": "joint_power",
    "phase_current_shunt": "joint_power",
}


def _finite(value: Any, path: str, issues: list[str], *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(f"{path} must be a finite number")
        return 0.0
    result = float(value)
    if not math.isfinite(result):
        issues.append(f"{path} must be a finite number")
        return 0.0
    if positive and result <= 0.0:
        issues.append(f"{path} must be positive")
    return result


def validate_component_bindings(records: Any, issues: list[str], *,
                                path: str = "electronics.component_bindings") -> list[dict[str, Any]]:
    """Validate exact-part candidate records and append every issue."""
    if not isinstance(records, list) or not records:
        issues.append(f"{path} must be a non-empty array")
        return []
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    mpns: set[str] = set()
    roles: set[str] = set()
    for index, raw in enumerate(records):
        item_path = f"{path}[{index}]"
        if not isinstance(raw, dict) or set(raw) != _BINDING_FIELDS:
            issues.append(f"{item_path} fields differ from the component evidence contract")
            continue
        identifier = raw.get("binding_id")
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            issues.append(f"{item_path}.binding_id must be stable")
        elif identifier in identifiers:
            issues.append(f"{item_path}.binding_id must be unique")
        else:
            identifiers.add(identifier)
        manufacturer = raw.get("manufacturer")
        mpn = raw.get("mpn")
        package = raw.get("package")
        for key, value in (("manufacturer", manufacturer), ("mpn", mpn),
                           ("package", package)):
            if not isinstance(value, str) or not value.strip():
                issues.append(f"{item_path}.{key} is required")
        mpn_key = re.sub(r"[^A-Z0-9]", "", str(mpn).upper())
        if mpn_key in mpns:
            issues.append(f"{item_path}.mpn must be unique in the joint controller plan")
        mpns.add(mpn_key)
        role = raw.get("role")
        if role not in _ROLE_CAPABILITIES:
            issues.append(f"{item_path}.role is unsupported")
        else:
            roles.add(role)
        board = raw.get("applies_to_board")
        if board not in _BOARD_ROLES:
            issues.append(f"{item_path}.applies_to_board is unsupported")
        elif role in _EXPECTED_BOARD and board != _EXPECTED_BOARD[role]:
            issues.append(f"{item_path}.{role} must be assigned to {_EXPECTED_BOARD[role]}")
        quantity = raw.get("quantity_per_board")
        if type(quantity) is not int or quantity <= 0:
            issues.append(f"{item_path}.quantity_per_board must be a positive integer")
        body = raw.get("body_size_mm")
        if not isinstance(body, list) or len(body) != 3:
            issues.append(f"{item_path}.body_size_mm must contain length width and height")
            body = [0.0, 0.0, 0.0]
        body_values = [_finite(value, f"{item_path}.body_size_mm[{body_index}]", issues,
                               positive=True) for body_index, value in enumerate(body)]
        capabilities = raw.get("capabilities")
        expected_capabilities = _ROLE_CAPABILITIES.get(role, set())
        if not isinstance(capabilities, dict) or set(capabilities) != expected_capabilities:
            issues.append(f"{item_path}.capabilities must contain exactly "
                          f"{sorted(expected_capabilities)}")
            capabilities = {}
        normalized_capabilities: dict[str, Any] = {}
        for key in sorted(expected_capabilities):
            value = capabilities.get(key)
            if key == "integrated_buck":
                if type(value) is not bool:
                    issues.append(f"{item_path}.capabilities.{key} must be boolean")
                    value = False
                normalized_capabilities[key] = value
            elif key in {"fdcan_instances", "package_accessible_fdcan_instances",
                         "motor_control_pwm_timers", "current_shunt_amplifiers"}:
                if type(value) is not int or value < 0:
                    issues.append(f"{item_path}.capabilities.{key} must be a non-negative integer")
                    value = 0
                normalized_capabilities[key] = value
            else:
                normalized_capabilities[key] = _finite(
                    value, f"{item_path}.capabilities.{key}", issues, positive=True)
        evidence = raw.get("primary_evidence")
        if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
            issues.append(f"{item_path}.primary_evidence fields are incomplete")
            evidence = {}
        else:
            for key in ("url", "document", "revision", "observed_date"):
                if not isinstance(evidence.get(key), str) or not evidence[key].strip():
                    issues.append(f"{item_path}.primary_evidence.{key} is required")
            if not str(evidence.get("url", "")).startswith("https://"):
                issues.append(f"{item_path}.primary_evidence.url must use https")
            facts = evidence.get("fact_basis")
            if not isinstance(facts, list) or not facts or any(
                    not isinstance(fact, str) or not fact.strip() for fact in facts):
                issues.append(f"{item_path}.primary_evidence.fact_basis must be non-empty")
        if raw.get("selection_status") != SELECTION_STATUS:
            issues.append(f"{item_path}.selection_status must be {SELECTION_STATUS}")
        normalized.append({
            "binding_id": identifier,
            "manufacturer": manufacturer,
            "mpn": mpn,
            "role": role,
            "applies_to_board": board,
            "quantity_per_board": quantity,
            "package": package,
            "body_size_mm": body_values,
            "capabilities": normalized_capabilities,
            "primary_evidence": dict(evidence),
            "selection_status": raw.get("selection_status"),
        })
    missing_roles = set(_ROLE_CAPABILITIES) - roles
    if missing_roles:
        issues.append(f"{path} is missing roles {sorted(missing_roles)}")
    return normalized


def evaluate_joint_controller_selection(
        bindings: list[dict[str, Any]], *, can_fd_channels: int,
        bus_operating_max_v: float, bus_transient_ceiling_v: float,
        semiconductor_voltage_derating_factor: float, maximum_phase_current_a: float,
        control_frequency_hz: float,
        board_width_mm: float, board_height_mm: float,
        package_to_courtyard_area_factor: float, logic_reserved_fraction: float,
        power_reserved_fraction: float, maximum_estimated_utilization: float) -> dict[str, Any]:
    """Return deterministic capability/occupancy receipts or raise ValueError."""
    by_role = {record["role"]: record for record in bindings}
    findings: list[str] = []
    for role, required_quantity in _REQUIRED_QUANTITIES.items():
        record = by_role.get(role)
        if record is None:
            findings.append(f"joint controller has no {role} binding")
        elif record["quantity_per_board"] != required_quantity:
            findings.append(
                f"{role} requires quantity {required_quantity}, got {record['quantity_per_board']}")
    if findings:
        raise ValueError("; ".join(findings))

    mcu = by_role["motor_control_mcu"]
    accessible_can = mcu["capabilities"]["package_accessible_fdcan_instances"]
    if accessible_can < can_fd_channels:
        findings.append(
            f"MCU {mcu['mpn']} exposes {accessible_can} FDCAN instances in its selected package, "
            f"but {can_fd_channels} channels are required")
    can = by_role["can_fd_transceiver"]
    if can["quantity_per_board"] < can_fd_channels:
        findings.append(
            f"CAN-FD transceiver quantity {can['quantity_per_board']} is below required channel "
            f"count {can_fd_channels}")
    driver = by_role["three_phase_gate_driver"]
    if driver["capabilities"]["current_shunt_amplifiers"] < 3:
        findings.append("three-phase gate driver must provide three current-shunt amplifiers")
    if driver["capabilities"]["integrated_buck"] is not True:
        findings.append("selected compact joint architecture requires the gate driver's integrated buck")

    required_semiconductor_voltage = (
        bus_transient_ceiling_v * semiconductor_voltage_derating_factor)
    if bus_transient_ceiling_v < bus_operating_max_v:
        findings.append("bus transient ceiling is below the maximum operating bus voltage")
    if driver["capabilities"]["supply_abs_max_v"] < required_semiconductor_voltage:
        findings.append(
            f"gate-driver voltage rating is below derated transient requirement "
            f"{required_semiconductor_voltage:.3f} V")
    mosfet = by_role["power_mosfet"]
    if mosfet["capabilities"]["vds_max_v"] < required_semiconductor_voltage:
        findings.append(
            f"MOSFET VDS rating is below derated transient requirement "
            f"{required_semiconductor_voltage:.3f} V")
    if mosfet["capabilities"]["id_max_a"] < maximum_phase_current_a:
        findings.append("MOSFET current rating is below the computed maximum phase current")

    shunt = by_role["phase_current_shunt"]
    shunt_power_w = maximum_phase_current_a ** 2 * shunt["capabilities"]["resistance_ohm"]
    shunt_power_margin = shunt["capabilities"]["power_rating_w"] / max(shunt_power_w, 1e-12)
    if shunt_power_margin < 2.0:
        findings.append(
            f"current-shunt power margin {shunt_power_margin:.3f} is below required 2.0")

    mosfet_voltage_margin_v = (
        mosfet["capabilities"]["vds_max_v"] - required_semiconductor_voltage)
    assumed_gate_drive_v = 10.0
    one_mosfet_conduction_loss_w = (
        maximum_phase_current_a ** 2 * mosfet["capabilities"]["rds_on_max_ohm"])
    six_mosfet_gate_drive_power_w = (
        6.0 * mosfet["capabilities"]["qg_typ_nc"] * 1e-9
        * assumed_gate_drive_v * control_frequency_hz)

    board_area = board_width_mm * board_height_mm
    occupancy: dict[str, dict[str, float]] = {}
    reserve_by_board = {
        "joint_logic": logic_reserved_fraction,
        "joint_power": power_reserved_fraction,
    }
    for board in sorted(_BOARD_ROLES):
        package_body_area = sum(
            record["body_size_mm"][0] * record["body_size_mm"][1]
            * record["quantity_per_board"]
            for record in bindings if record["applies_to_board"] == board)
        estimated_courtyard_area = package_body_area * package_to_courtyard_area_factor
        estimated_utilization = estimated_courtyard_area / board_area + reserve_by_board[board]
        occupancy[board] = {
            "board_area_mm2": round(board_area, 4),
            "bound_package_body_area_mm2": round(package_body_area, 4),
            "estimated_courtyard_area_mm2": round(estimated_courtyard_area, 4),
            "reserved_fraction": round(reserve_by_board[board], 6),
            "estimated_utilization": round(estimated_utilization, 6),
            "maximum_estimated_utilization": round(maximum_estimated_utilization, 6),
        }
        if estimated_utilization > maximum_estimated_utilization:
            findings.append(
                f"{board} estimated placement utilization {estimated_utilization:.3f} exceeds "
                f"maximum {maximum_estimated_utilization:.3f}")
    if findings:
        raise ValueError("; ".join(findings))
    return {
        "schema": COMPONENT_PLAN_SCHEMA,
        "status": "capability_and_first_order_packaging_checks_pass",
        "selection_status": SELECTION_STATUS,
        "bindings": bindings,
        "capability_receipts": {
            "required_can_fd_channels": can_fd_channels,
            "mcu_total_fdcan_instances": mcu["capabilities"]["fdcan_instances"],
            "mcu_package_accessible_fdcan_instances": accessible_can,
            "can_fd_transceiver_count": can["quantity_per_board"],
            "gate_driver_current_shunt_amplifiers": driver["capabilities"][
                "current_shunt_amplifiers"],
            "bus_operating_max_v": round(bus_operating_max_v, 6),
            "bus_transient_ceiling_v": round(bus_transient_ceiling_v, 6),
            "required_semiconductor_voltage_rating_v": round(
                required_semiconductor_voltage, 6),
            "gate_driver_abs_max_v": driver["capabilities"]["supply_abs_max_v"],
            "mosfet_vds_max_v": mosfet["capabilities"]["vds_max_v"],
            "mosfet_voltage_margin_v": round(mosfet_voltage_margin_v, 6),
            "mosfet_voltage_margin_ratio": round(
                mosfet["capabilities"]["vds_max_v"]
                / required_semiconductor_voltage, 6),
            "maximum_phase_current_a": round(maximum_phase_current_a, 6),
            "one_mosfet_static_conduction_loss_at_maximum_phase_current_w": round(
                one_mosfet_conduction_loss_w, 6),
            "two_mosfet_static_path_loss_at_maximum_phase_current_w": round(
                one_mosfet_conduction_loss_w * 2.0, 6),
            "assumed_gate_drive_voltage_v": assumed_gate_drive_v,
            "six_mosfet_gate_charge_power_at_control_frequency_w": round(
                six_mosfet_gate_drive_power_w, 6),
            "shunt_dissipation_at_maximum_phase_current_w": round(shunt_power_w, 6),
            "shunt_power_margin": round(shunt_power_margin, 6),
        },
        "placement_estimates": occupancy,
        "scope_limitations": [
            "manufacturer_document_urls_are_bound_but_pdf_content_digests_are_not_yet_ingested",
            "package_body_area_is_not_a_verified_land_pattern_or_courtyard",
            "schematic_symbol_pin_mapping_and_reference_circuit_are_not_yet_materialized",
            "native_placement_routing_si_pi_emc_and_board_thermal_results_are_not_yet_available",
            "functional_safety_architecture_requires_independent_analysis_and_validation",
        ],
    }


__all__ = [
    "COMPONENT_PLAN_SCHEMA",
    "SELECTION_STATUS",
    "evaluate_joint_controller_selection",
    "validate_component_bindings",
]
