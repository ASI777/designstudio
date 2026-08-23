"""Kinematic repeatability allocation and structural/control separation screen."""
from __future__ import annotations

import math
from typing import Any


class AccuracyControlsError(ValueError):
    """Raised when an accuracy or control-separation target is infeasible."""


def _upper(quantity: dict[str, Any]) -> float:
    return float(quantity["value"] + quantity["tolerance"]["plus"])


def build_robot_accuracy_controls_contract(
    spec: dict[str, Any],
    structural_screening: dict[str, Any],
    joint_net_contract: dict[str, Any],
    *,
    requirements_sha256: str,
) -> dict[str, Any]:
    requirements = spec["accuracy_and_controls"]
    segments = spec["kinematics"]["segments"]
    target_mm = spec["system"]["target_repeatability"]["value"]
    tool_allocation = requirements["tool_variance_allocation"]
    joint_allocation = requirements["joint_variance_allocation"]
    lever_arms_m = [
        sum(_upper(segment["length"]) for segment in segments[index:]) / 1000.0
        for index in range(len(segments))
    ]
    joint_tool_budget_mm = target_mm * math.sqrt(
        tool_allocation["joint_sensing_and_transmission"])
    lever_rss_m = math.sqrt(sum(lever * lever for lever in lever_arms_m))
    common_joint_angle_rad = (joint_tool_budget_mm / 1000.0) / lever_rss_m
    if common_joint_angle_rad <= 0.0:
        raise AccuracyControlsError("derived common joint-angle budget is not positive")
    common_joint_angle_deg = math.degrees(common_joint_angle_rad)

    joints = []
    for segment, lever in zip(segments, lever_arms_m, strict=True):
        joints.append({
            "joint_id": segment["joint_id"],
            "worst_reach_lever_arm_m": round(lever, 6),
            "maximum_total_joint_rms_angle_rad": round(common_joint_angle_rad, 12),
            "maximum_total_joint_rms_angle_deg": round(common_joint_angle_deg, 9),
            "maximum_total_joint_rms_angle_arcsec": round(
                common_joint_angle_deg * 3600.0, 6),
            "subsystem_rms_angle_allocations_arcsec": {
                key: round(common_joint_angle_deg * 3600.0 * math.sqrt(fraction), 6)
                for key, fraction in sorted(joint_allocation.items())
            },
            "exact_encoder_binding": None,
            "exact_reducer_binding": None,
            "bearing_stiffness_and_clearance_bound": False,
            "servo_tracking_measurement_bound": False,
            "verification_status": "incomplete_component_and_test_evidence_missing",
        })

    other_tool_budgets = {
        key: round(target_mm * math.sqrt(fraction), 9)
        for key, fraction in sorted(tool_allocation.items())
        if key != "joint_sensing_and_transmission"
    }
    reconstituted = math.sqrt(
        joint_tool_budget_mm ** 2
        + sum(value * value for value in other_tool_budgets.values()))
    first_mode_hz = structural_screening["frequency_screen"]["minimum_estimate_hz"]
    bandwidth_hz = requirements["maximum_joint_position_bandwidth"]["value"]
    separation = first_mode_hz / bandwidth_hz
    minimum_separation = requirements["minimum_structural_mode_separation"]["value"]
    if separation < minimum_separation:
        raise AccuracyControlsError(
            f"structural-mode separation {separation:.6f} is below {minimum_separation:.6f}")
    control_frequency_hz = spec["electronics"]["control_frequency"]["value"]
    pwm_period_s = 1.0 / control_frequency_hz
    maximum_latency_s = requirements["maximum_command_to_pwm_latency"]["value"]
    return {
        "schema": "design-studio.robot-accuracy-controls/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "repeatability_definition": "tool_repeatability_not_absolute_accuracy",
        "allocation_method": requirements["allocation_method"],
        "target_tool_repeatability_mm": target_mm,
        "joint_sensing_and_transmission_tool_rms_budget_mm": round(
            joint_tool_budget_mm, 9),
        "other_tool_rms_budgets_mm": other_tool_budgets,
        "reconstituted_tool_rms_budget_mm": round(reconstituted, 9),
        "joints": joints,
        "repeatability_allocation_result": "pass_budget_allocated_evidence_incomplete",
        "controls": {
            "maximum_joint_position_bandwidth_hz": bandwidth_hz,
            "screened_minimum_structural_mode_hz": first_mode_hz,
            "structural_mode_separation_ratio": round(separation, 6),
            "minimum_required_structural_mode_separation_ratio": minimum_separation,
            "mode_separation_result": "pass_screening_only",
            "pwm_control_frequency_hz": control_frequency_hz,
            "pwm_period_s": round(pwm_period_s, 9),
            "maximum_command_to_pwm_latency_s": maximum_latency_s,
            "maximum_latency_in_pwm_periods": round(maximum_latency_s / pwm_period_s, 6),
            "measured_command_to_pwm_latency_s": None,
            "plant_model_status": "incomplete_motor_reducer_friction_backlash_and_delay_missing",
            "stability_result": "incomplete_frequency_response_and_robustness_analysis_missing",
        },
        "joint_net_contract_status": joint_net_contract["status"],
        "required_evidence": [
            "exact_encoder_resolution_repeatability_latency_and_temperature_error",
            "reducer_lost_motion_hysteresis_torsional_stiffness_and_life_data",
            "bearing_clearance_preload_stiffness_and_temperature_data",
            "calibrated_joint_and_tool_metrology_error_map",
            "motor_reducer_load_inertia_friction_and_delay_frequency_response_model",
            "closed_loop_gain_phase_sensitivity_and_disturbance_rejection_evidence",
            "multi_pose_repeatability_thermal_drift_and_payload_test_receipts",
        ],
    }
