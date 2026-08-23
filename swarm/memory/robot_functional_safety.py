"""Functional-safety allocation contract without unsupported compliance claims."""
from __future__ import annotations

from typing import Any


class FunctionalSafetyError(ValueError):
    """Raised when mandatory safety-function allocation is missing."""


_ALLOCATIONS = {
    "emergency_stop": {
        "input_subsystem": "dual_channel_external_estop_input",
        "logic_subsystem": "independent_safety_logic_not_yet_bound",
        "final_elements": ["dual_channel_hardware_sto", "spring_applied_joint_brakes"],
        "diagnostics": ["channel_discrepancy", "reset_interlock", "contactor_or_sto_feedback"],
        "implementation_status": "incomplete_safety_logic_and_final_element_bindings",
    },
    "dual_channel_sto": {
        "input_subsystem": "independent_safety_logic_outputs",
        "logic_subsystem": "two_independent_gate_disable_paths_not_yet_materialized",
        "final_elements": ["gate_driver_enable_path_a", "gate_driver_enable_path_b"],
        "diagnostics": ["cross_monitoring", "gate_disable_readback", "periodic_proof_test"],
        "implementation_status": "incomplete_independent_hardware_paths",
    },
    "brake_control": {
        "input_subsystem": "controlled_stop_complete_and_speed_below_threshold",
        "logic_subsystem": "safety_sequencer_not_yet_bound",
        "final_elements": ["spring_applied_brake_coils"],
        "diagnostics": ["brake_command_feedback", "hold_test", "engagement_time_monitoring"],
        "implementation_status": "incomplete_exact_brake_and_driver_bindings",
    },
    "joint_limit_monitoring": {
        "input_subsystem": "independent_position_channels",
        "logic_subsystem": "safe_position_comparator_not_yet_bound",
        "final_elements": ["controlled_stop", "dual_channel_hardware_sto"],
        "diagnostics": ["sensor_cross_comparison", "plausibility", "startup_test"],
        "implementation_status": "incomplete_redundant_position_sensing",
    },
    "safe_speed_monitoring": {
        "input_subsystem": "independent_speed_channels",
        "logic_subsystem": "safe_speed_monitor_not_yet_bound",
        "final_elements": ["controlled_stop", "dual_channel_hardware_sto"],
        "diagnostics": ["sensor_cross_comparison", "speed_threshold_test", "watchdog"],
        "implementation_status": "incomplete_redundant_speed_sensing",
    },
}


def build_robot_functional_safety_contract(
    spec: dict[str, Any],
    brake_sizing: dict[str, Any],
    joint_net_contract: dict[str, Any],
    *,
    requirements_sha256: str,
) -> dict[str, Any]:
    safety_functions = spec["safety"]["functions"]
    missing = set(safety_functions) - set(_ALLOCATIONS)
    if missing:
        raise FunctionalSafetyError(
            f"safety functions lack architecture allocations: {sorted(missing)}")
    targets = spec["functional_safety"]
    allocations = []
    for function in safety_functions:
        allocation = _ALLOCATIONS[function]
        allocations.append({
            "function_id": function,
            "target_performance_level": targets["target_performance_level"],
            "target_architecture_category": targets["target_architecture_category"],
            "input_subsystem": allocation["input_subsystem"],
            "logic_subsystem": allocation["logic_subsystem"],
            "final_elements": allocation["final_elements"],
            "diagnostics": allocation["diagnostics"],
            "implementation_status": allocation["implementation_status"],
            "achieved_performance_level": None,
        })
    return {
        "schema": "design-studio.robot-functional-safety-contract/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "standards_context": spec["safety"]["standards"],
        "compliance_claimed": False,
        "target_performance_level": targets["target_performance_level"],
        "target_architecture_category": targets["target_architecture_category"],
        "minimum_diagnostic_coverage": targets["minimum_diagnostic_coverage"]["value"],
        "maximum_safe_state_response_time_s": targets[
            "maximum_safe_state_response_time"]["value"],
        "safe_state": targets["safe_state"],
        "reset_and_restart_policy": {
            "manual_reset_required": targets["manual_reset_required"],
            "restart_interlock_required": targets["restart_interlock_required"],
            "single_fault_tolerance_required": targets[
                "single_fault_tolerance_required"],
        },
        "function_allocations": allocations,
        "brake_sizing_reference": "mechanical/robot-brake-sizing.json",
        "brake_component_selection_status": brake_sizing["component_selection_result"],
        "joint_net_contract_status": joint_net_contract["status"],
        "risk_assessment_status": "incomplete_hazard_analysis_and_risk_reduction_trace_missing",
        "hardware_architecture_result": "incomplete_exact_safety_components_and_independent_paths_missing",
        "probabilistic_result": "incomplete_mttfd_dcavg_ccf_andmission_profile_missing",
        "validation_result": "incomplete_fault_injection_and_system_tests_missing",
        "required_evidence": [
            "hazard_analysis_risk_estimation_and_required_risk_reduction_per_function",
            "exact_safety_input_logic_sto_brake_sensor_and_power_component_bindings",
            "independence_common_cause_failure_and_segregation_analysis",
            "component_mttfd_b10d_diagnostic_coverage_and_proof_test_data",
            "fmeca_fmeda_and_performance_level_calculation",
            "safe_state_timing_budget_with_worst_case_controlled_stop",
            "single_fault_injection_reset_restart_and_power_cycle_test_receipts",
            "independent_machine_safety_assessment",
        ],
    }
