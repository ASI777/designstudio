"""Typed coupled thermal-network contract for distributed robot joints."""
from __future__ import annotations

from typing import Any


class ThermalNetworkError(ValueError):
    """Raised when an available thermal screen violates a hard limit."""


def _upper(quantity: dict[str, Any]) -> float:
    return float(quantity["value"] + quantity["tolerance"]["plus"])


def _lower(quantity: dict[str, Any]) -> float:
    return float(quantity["value"] - quantity["tolerance"]["minus"])


def build_robot_thermal_network_contract(
    spec: dict[str, Any],
    joint_reports: list[dict[str, Any]],
    *,
    requirements_sha256: str,
) -> dict[str, Any]:
    """Evaluate known power-stage paths and expose missing coupled inputs.

    A missing motor loss map or thermal resistance is never replaced by an AI
    estimate.  The resulting network remains solvable only for the uncoupled
    power-stage path until exact motor, reducer and interface data are bound.
    """

    ambient = _upper(spec["thermal"]["maximum_ambient"])
    junction_limit = _lower(spec["thermal"]["maximum_driver_junction"])
    winding_limit = _lower(spec["thermal"]["maximum_motor_winding"])
    joints: list[dict[str, Any]] = []
    for report in joint_reports:
        loss = report["continuous_power_stage_loss_budget_w"]
        path_rth = report["selected_thermal_resistance_degc_per_w"]
        junction_temperature = ambient + loss * path_rth
        margin = junction_limit - junction_temperature
        if margin < 0.0:
            raise ThermalNetworkError(
                f"{report['joint_id']} thermal resistance produces a power-stage "
                "temperature above its limit")
        joints.append({
            "joint_id": report["joint_id"],
            "known_heat_sources_w": {
                "power_stage_continuous_budget": loss,
                "motor_copper": None,
                "motor_core": None,
                "reducer_and_bearing": None,
                "brake_coil_released": None,
            },
            "known_thermal_paths_degc_per_w": {
                "power_stage_to_ambient_candidate_max": path_rth,
                "junction_to_board": None,
                "board_to_hub": None,
                "winding_to_stator": None,
                "stator_to_hub": None,
                "hub_to_ambient": None,
                "joint_to_adjacent_links": None,
            },
            "uncoupled_power_stage_junction_degc": round(junction_temperature, 6),
            "power_stage_junction_margin_degc": round(margin, 6),
            "power_stage_screen_status": "pass_candidate_path_not_measured",
            "motor_winding_temperature_degc": None,
            "coupled_solution_status": "incomplete_motor_and_interface_parameters_missing",
        })
    return {
        "schema": "design-studio.robot-coupled-thermal-contract/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "network_nodes": [
            "motor_winding", "motor_stator", "reducer_and_bearing", "brake_coil",
            "power_stage_junction", "pcb_copper", "joint_hub", "adjacent_links", "ambient",
        ],
        "boundary_conditions": {
            "maximum_ambient_degc": ambient,
            "maximum_motor_winding_degc": winding_limit,
            "maximum_driver_junction_degc": junction_limit,
            "continuous_duty_factor_max": _upper(
                spec["thermal"]["continuous_duty_factor"]),
        },
        "joints": joints,
        "power_stage_screen_result": "pass_candidate_paths",
        "coupled_network_result": "incomplete_exact_motor_and_path_data_missing",
        "required_inputs": [
            "exact_motor_reducer_brake_and_bearing_bindings",
            "motor_phase_resistance_vs_temperature_and_torque_speed_efficiency_map",
            "motor_core_reducer_bearing_and_brake_coil_loss_maps",
            "winding_stator_hub_board_interface_and_hub_ambient_thermal_resistances",
            "node_heat_capacities_and_contact_interface_tolerances",
            "time_resolved_torque_speed_brake_and_ambient_duty_cycle",
        ],
        "required_validation": [
            "calibrated_lumped_network_transient_solve",
            "three_dimensional_conjugate_heat_transfer_cross_check",
            "instrumented_winding_hub_board_and_ambient_temperature_test",
        ],
    }
