"""Signal, power and EMC integrity contracts for distributed robot electronics."""
from __future__ import annotations

from typing import Any


class ElectricalIntegrityError(ValueError):
    """Raised when a deterministic electrical-integrity requirement fails."""


def build_robot_electrical_integrity_contract(
    spec: dict[str, Any],
    harness: dict[str, Any],
    joint_reports: list[dict[str, Any]],
    joint_net_contract: dict[str, Any],
    *,
    requirements_sha256: str,
) -> dict[str, Any]:
    requirements = spec["electrical_integrity"]
    bitrate = requirements["can_fd_data_rate"]["value"]
    propagation_velocity = requirements["minimum_propagation_velocity"]["value"]
    sample_point = requirements["sample_point_fraction"]["value"]
    minimum_margin = requirements["minimum_geometric_timing_margin"]["value"]
    bus_length_m = sum(
        segment["electrical_length_mm"] for segment in harness["segments"]) / 1000.0
    bit_time_ns = 1.0e9 / bitrate
    round_trip_ns = 2.0 * bus_length_m / propagation_velocity * 1.0e9
    available_before_sample_ns = bit_time_ns * sample_point
    geometric_margin = 1.0 - round_trip_ns / available_before_sample_ns
    if geometric_margin < minimum_margin:
        raise ElectricalIntegrityError(
            f"CAN FD geometric timing margin {geometric_margin:.6f} is below "
            f"{minimum_margin:.6f}")
    maximum_stub_m = requirements["maximum_stub_length"]["value"] / 1000.0
    maximum_stub_round_trip_ns = (
        2.0 * maximum_stub_m / propagation_velocity * 1.0e9)
    impedance = requirements["nominal_differential_impedance"]
    impedance_min = impedance["value"] - impedance["tolerance"]["minus"]
    impedance_max = impedance["value"] + impedance["tolerance"]["plus"]
    channels = [{
        "channel_id": channel,
        "data_rate_bit_per_s": bitrate,
        "bit_time_ns": round(bit_time_ns, 6),
        "maximum_path_length_m": round(bus_length_m, 6),
        "minimum_propagation_velocity_m_per_s": propagation_velocity,
        "path_round_trip_delay_ns": round(round_trip_ns, 6),
        "sample_point_fraction": sample_point,
        "available_before_sample_ns": round(available_before_sample_ns, 6),
        "geometric_timing_margin_fraction": round(geometric_margin, 6),
        "minimum_required_geometric_timing_margin_fraction": minimum_margin,
        "maximum_stub_length_mm": requirements["maximum_stub_length"]["value"],
        "maximum_stub_round_trip_delay_ns": round(maximum_stub_round_trip_ns, 6),
        "nominal_differential_impedance_ohm": impedance["value"],
        "allowed_differential_impedance_range_ohm": [impedance_min, impedance_max],
        "geometric_screen_result": "pass",
        "full_link_result": "incomplete_device_connector_and_cable_models_missing",
    } for channel in ("CAN_FD_A", "CAN_FD_B")]
    return {
        "schema": "design-studio.robot-electrical-integrity/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "signal_integrity": {
            "channels": channels,
            "exact_cable_part_number": None,
            "termination_population_plan": None,
            "transceiver_package_and_connector_models_bound": False,
            "result": "geometric_pass_full_channel_incomplete",
        },
        "power_integrity": {
            "worst_case_tolerance_aware_dc_drop_percent": harness[
                "end_of_chain_voltage_drop_percent"],
            "maximum_allowed_dc_drop_percent": harness[
                "maximum_allowed_voltage_drop_percent"],
            "maximum_joint_peak_bus_current_a": max(
                report["peak_bus_current_a"] for report in joint_reports),
            "maximum_allowed_joint_bus_ripple_v": requirements[
                "maximum_joint_bus_ripple"]["value"],
            "calculated_dynamic_bus_ripple_v": None,
            "local_bulk_capacitance_and_esr_esl_bound": False,
            "regeneration_and_hot_plug_source_impedance_bound": False,
            "dc_result": "pass",
            "dynamic_result": "incomplete_network_and_transient_models_missing",
        },
        "emc": {
            "shield_bonding_topology": requirements["shield_bonding_topology"],
            "maximum_can_ground_offset_v": requirements[
                "maximum_can_ground_offset"]["value"],
            "target_test_families": requirements["emc_test_targets"],
            "can_protection_network_bound": False,
            "motor_phase_common_mode_model_bound": False,
            "test_levels_and_operating_modes_bound": False,
            "result": "test_families_defined_levels_and_evidence_incomplete",
        },
        "net_contract_status": joint_net_contract["status"],
        "required_models_and_evidence": [
            "selected_shielded_twisted_pair_impedance_attenuation_and_velocity_data",
            "connector_via_trace_transceiver_and_termination_channel_models",
            "can_fd_eye_mask_and_bit_error_measurements_at_temperature_and_ground_offset",
            "joint_bus_source_impedance_bulk_capacitance_esr_esl_and_load_step_waveforms",
            "regeneration_hot_plug_and_brake_coil_transient_network",
            "motor_phase_common_mode_parasitic_and_shield_transfer_impedance_model",
            "emissions_immunity_test_levels_operating_modes_and_accredited_test_receipts",
        ],
    }
