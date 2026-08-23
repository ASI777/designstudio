"""Deterministic fatigue-duty allocation without invented material life data."""
from __future__ import annotations

from typing import Any


class FatigueContractError(ValueError):
    """Raised when the design duty spectrum is internally inconsistent."""


def build_robot_fatigue_contract(
    spec: dict[str, Any], *, requirements_sha256: str,
) -> dict[str, Any]:
    durability = spec["durability"]
    spectrum = durability["load_spectrum"]
    fraction_sum = sum(item["cycle_fraction"] for item in spectrum)
    if abs(fraction_sum - 1.0) > 1.0e-9:
        raise FatigueContractError("fatigue duty-spectrum fractions must sum to one")
    target_cycles = durability["target_motion_cycles"]["value"]
    if target_cycles <= 0.0:
        raise FatigueContractError("fatigue target motion cycles must be positive")

    joints: list[dict[str, Any]] = []
    for segment in spec["kinematics"]["segments"]:
        selected_output = (
            segment["selected_output_torque"]["value"]
            - segment["selected_output_torque"]["tolerance"]["minus"])
        bins = []
        for duty_bin in spectrum:
            bins.append({
                "bin_id": duty_bin["bin_id"],
                "allocated_cycles": round(
                    target_cycles * duty_bin["cycle_fraction"], 6),
                "cycle_fraction": duty_bin["cycle_fraction"],
                "output_torque_amplitude_nm": round(
                    selected_output * duty_bin["output_torque_fraction"], 6),
                "stress_ratio": duty_bin["stress_ratio"],
                "local_stress_amplitude_mpa": None,
                "allowable_cycles_from_qualified_curve": None,
                "miner_damage": None,
                "damage_status": "blocked_by_local_stress_and_material_curve",
            })
        joints.append({
            "joint_id": segment["joint_id"],
            "structural_material": segment["structural_material"],
            "selected_output_torque_nm": selected_output,
            "spectrum_bins": bins,
            "cumulative_miner_damage": None,
            "maximum_allowed_miner_damage": durability[
                "maximum_miner_damage"]["value"],
            "life_result": "incomplete_no_qualified_sn_curve_or_notch_stress",
        })

    return {
        "schema": "design-studio.robot-fatigue-contract/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "target_service_life_h": durability["target_service_life"]["value"],
        "target_motion_cycles": target_cycles,
        "cycle_counting_method": durability["cycle_counting_method"],
        "damage_accumulation_method": durability["damage_accumulation_method"],
        "spectrum_definition_status": "complete_design_duty_envelope",
        "joints": joints,
        "life_prediction_result": "incomplete_required_evidence_missing",
        "prohibited_substitutions": [
            "static_yield_strength_as_fatigue_strength",
            "generic_material_name_as_product_form_sn_curve",
            "nominal_beam_stress_as_local_notch_stress",
            "ai_estimated_cycles_to_failure_without_test_or_qualified_curve",
        ],
        "required_evidence": [
            "supplier_product_form_heat_treatment_surface_finish_and_environment",
            "qualified_sn_or_strain_life_curve_with_statistical_basis",
            "pose_and_transient_load_history_rainflow_counts",
            "local_elastic_or_elastoplastic_notch_stress_from_verified_mesh",
            "mean_stress_size_surface_corrosion_and_residual_stress_corrections",
            "weld_fastener_bearing_and_reducer_contact_fatigue_models_where_applicable",
            "representative_coupon_subassembly_and_full_robot_endurance_tests",
        ],
    }
