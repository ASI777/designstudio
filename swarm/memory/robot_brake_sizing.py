"""Deterministic static-holding brake sizing for serial robot joints.

This module sizes a motor-side, spring-applied brake for loss-of-power holding.
It deliberately does not treat the holding brake as the dynamic stopping device:
the drive must execute the controlled stop before brake engagement.
"""
from __future__ import annotations

import math
from typing import Any


class BrakeSizingError(ValueError):
    """Raised when brake requirements are internally inconsistent."""


def _maximum(quantity: dict[str, Any]) -> float:
    return float(quantity["value"] + quantity["tolerance"]["plus"])


def build_robot_brake_sizing(
    spec: dict[str, Any], *, requirements_sha256: str,
) -> dict[str, Any]:
    """Build conservative static-hold requirements for every robot joint."""

    segments = spec["kinematics"]["segments"]
    braking = spec["braking"]
    gravity = _maximum(spec["system"]["gravity"])
    payload_mass = (
        _maximum(spec["system"]["payload"])
        + _maximum(spec["system"]["end_effector_mass"])
    )
    lengths_m = [_maximum(segment["length"]) / 1000.0 for segment in segments]
    masses_kg = [_maximum(segment["assembly_mass"]) for segment in segments]
    uncertainty = {
        key: spec["system"][key]["value"]
        for key in (
            "load_uncertainty_factor", "model_uncertainty_factor", "consequence_factor",
        )
    }
    gravity_uncertainty_factor = math.prod(uncertainty.values())
    holding_load_factor = braking["holding_load_factor"]["value"]
    hot_retention = braking["minimum_hot_torque_retention"]["value"]
    path_efficiency = braking["minimum_brake_path_efficiency"]["value"]
    disturbance_fraction = braking["minimum_non_gravity_holding_fraction"]["value"]
    if not 0.0 < hot_retention <= 1.0:
        raise BrakeSizingError("minimum hot brake torque retention must be in (0, 1]")
    if not 0.0 < path_efficiency <= 1.0:
        raise BrakeSizingError("minimum brake-path efficiency must be in (0, 1]")
    if holding_load_factor < 1.0:
        raise BrakeSizingError("holding load factor must be at least one")

    joints: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        distance_before = 0.0
        mass_moment_kg_m = 0.0
        for downstream in range(index, len(segments)):
            center_distance = distance_before + lengths_m[downstream] / 2.0
            mass_moment_kg_m += masses_kg[downstream] * center_distance
            distance_before += lengths_m[downstream]
        mass_moment_kg_m += payload_mass * distance_before
        # J1 is the fixed vertical base axis, so gravity cannot create torque
        # about its axis. Every movable downstream axis is screened against an
        # all-horizontal upper-bound pose rather than only the neutral pose.
        gravity_torque = 0.0 if index == 0 else gravity * mass_moment_kg_m
        factored_gravity = gravity_torque * gravity_uncertainty_factor
        selected_output_torque = segment["selected_output_torque"]["value"]
        disturbance_floor = selected_output_torque * disturbance_fraction
        governing_service_torque = max(factored_gravity, disturbance_floor)
        required_hot_output = governing_service_torque * holding_load_factor
        required_cold_output = required_hot_output / hot_retention
        required_motor_brake = required_cold_output / (
            segment["gear_ratio"]["value"] * path_efficiency)
        governing_case = (
            "all_horizontal_gravity_upper_bound"
            if factored_gravity >= disturbance_floor
            else "non_gravity_disturbance_floor"
        )
        joints.append({
            "joint_id": segment["joint_id"],
            "gravity_coupling_basis": (
                "fixed_vertical_base_axis_zero"
                if index == 0 else "all_horizontal_serial_chain_upper_bound"),
            "maximum_downstream_mass_moment_kg_m": round(mass_moment_kg_m, 6),
            "unfactored_gravity_torque_nm": round(gravity_torque, 6),
            "factored_gravity_torque_nm": round(factored_gravity, 6),
            "non_gravity_disturbance_floor_nm": round(disturbance_floor, 6),
            "governing_case": governing_case,
            "required_hot_output_holding_torque_nm": round(required_hot_output, 6),
            "minimum_cold_rated_output_equivalent_nm": round(required_cold_output, 6),
            "gear_ratio": segment["gear_ratio"]["value"],
            "minimum_brake_path_efficiency": path_efficiency,
            "minimum_motor_side_brake_rating_nm": round(required_motor_brake, 6),
            "selected_brake_manufacturer": None,
            "selected_brake_part_number": None,
            "selected_brake_cold_rating_nm": None,
            "selection_status": "component_binding_required",
        })

    return {
        "schema": "design-studio.robot-brake-sizing/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "architecture": braking["architecture"],
        "dynamic_stopping_policy": braking["dynamic_stopping_policy"],
        "maximum_engagement_time_ms": round(
            braking["maximum_engagement_time"]["value"] * 1000.0, 6),
        "holding_load_factor": holding_load_factor,
        "minimum_hot_torque_retention": hot_retention,
        "gravity_uncertainty_budget": uncertainty,
        "gravity_uncertainty_factor": round(gravity_uncertainty_factor, 6),
        "minimum_non_gravity_holding_fraction": disturbance_fraction,
        "joints": joints,
        "maximum_required_motor_side_brake_rating_nm": max(
            joint["minimum_motor_side_brake_rating_nm"] for joint in joints),
        "sizing_result": "requirements_computed",
        "component_selection_result": "incomplete_component_bindings_missing",
        "dynamic_engagement_result": "incomplete_residual_speed_and_inertia_missing",
        "required_component_evidence": [
            "exact_manufacturer_and_orderable_part_number",
            "cold_and_max_temperature_static_torque_curves",
            "response_time_release_time_and_coil_voltage_tolerance",
            "wear_life_and_permitted_emergency_engagement_energy",
            "brake_path_efficiency_backlash_and_reducer_reverse_load_rating",
            "encoder_or_switch_based_brake_state_diagnostic_coverage",
        ],
        "verification_tests": [
            "worst_pose_payload_power_loss_hold_test",
            "maximum_temperature_static_hold_test",
            "minimum_voltage_release_and_power_loss_engagement_time_test",
            "controlled_stop_then_brake_sequence_test",
            "single_fault_brake_monitoring_diagnostic_test",
        ],
    }
