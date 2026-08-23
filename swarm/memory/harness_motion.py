"""Deterministic motion-screening contract for articulated robot harnesses.

Electrical length and voltage-drop calculations do not establish that a cable
can survive joint motion.  This module keeps those concerns separate.  It
performs only an optimistic packaging screen and records the supplier data,
joint travel, CAD path and physical-test evidence that are still required.
"""
from __future__ import annotations

import math
from typing import Any


SCHEMA = "design-studio.robot-harness-motion-contract/1"
_SCREENING_BEND_RADIUS_MULTIPLIER = 8.0


def _quantity_value(container: dict[str, Any], name: str) -> float:
    raw = container.get(name)
    if not isinstance(raw, dict) or isinstance(raw.get("value"), bool):
        raise ValueError(f"{name} must be a normalized quantity")
    value = raw.get("value")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name}.value must be finite")
    return float(value)


def _maximum_quantity(container: dict[str, Any], name: str) -> float:
    raw = container.get(name)
    value = _quantity_value(container, name)
    tolerance = raw.get("tolerance")
    if not isinstance(tolerance, dict) or isinstance(tolerance.get("plus"), bool):
        raise ValueError(f"{name}.tolerance.plus must be finite")
    plus = tolerance.get("plus")
    if not isinstance(plus, (int, float)) or not math.isfinite(float(plus)):
        raise ValueError(f"{name}.tolerance.plus must be finite")
    return value + float(plus)


def _minimum_quantity(container: dict[str, Any], name: str) -> float:
    raw = container.get(name)
    value = _quantity_value(container, name)
    tolerance = raw.get("tolerance")
    if not isinstance(tolerance, dict) or isinstance(tolerance.get("minus"), bool):
        raise ValueError(f"{name}.tolerance.minus must be finite")
    minus = tolerance.get("minus")
    if not isinstance(minus, (int, float)) or not math.isfinite(float(minus)):
        raise ValueError(f"{name}.tolerance.minus must be finite")
    return value - float(minus)


def build_harness_motion_contract(
    *,
    product_id: str,
    requirements_sha256: str,
    segments: list[dict[str, Any]],
    electrical: dict[str, Any],
    manufacturing: dict[str, Any],
) -> dict[str, Any]:
    """Build an explicit incomplete-until-evidenced harness motion contract.

    The 8D bend-radius value is a deliberately labelled screening assumption,
    never a cable rating.  The radial envelope omits motors, gears, bearings,
    boards, connectors and retainers, so a positive margin is only a geometric
    possibility; a negative margin is enough to reject that route concept.
    """
    if not isinstance(product_id, str) or not product_id:
        raise ValueError("product_id must be non-empty")
    if (not isinstance(requirements_sha256, str)
            or len(requirements_sha256) != 64
            or any(character not in "0123456789abcdef" for character in requirements_sha256)):
        raise ValueError("requirements_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(segments, list) or len(segments) != 7:
        raise ValueError("segments must contain exactly seven joints")

    worst_bundle_diameter = _maximum_quantity(electrical, "harness_bundle_diameter")
    service_length = _quantity_value(electrical, "service_loop_length")
    shell_wall = _maximum_quantity(manufacturing, "shell_wall")
    service_clearance = _maximum_quantity(manufacturing, "service_clearance")
    provisional_radius = worst_bundle_diameter * _SCREENING_BEND_RADIUS_MULTIPLIER

    joint_screens: list[dict[str, Any]] = []
    incompatible: list[str] = []
    for index, segment in enumerate(segments, start=1):
        expected_joint_id = f"J{index}"
        if not isinstance(segment, dict) or segment.get("joint_id") != expected_joint_id:
            raise ValueError(f"segments[{index - 1}] must be {expected_joint_id}")
        minimum_outer_radius = _minimum_quantity(segment, "outer_diameter") / 2.0
        optimistic_available_radius = minimum_outer_radius - shell_wall - service_clearance
        margin = optimistic_available_radius - provisional_radius
        status = (
            "optimistic_envelope_can_contain_provisional_radius"
            if margin >= 0.0
            else "optimistic_envelope_cannot_contain_provisional_radius"
        )
        if margin < 0.0:
            incompatible.append(expected_joint_id)
        joint_screens.append({
            "joint_id": expected_joint_id,
            "minimum_outer_envelope_radius_mm": round(minimum_outer_radius, 6),
            "maximum_shell_wall_mm": round(shell_wall, 6),
            "maximum_service_clearance_mm": round(service_clearance, 6),
            "optimistic_available_radial_envelope_mm": round(
                optimistic_available_radius, 6),
            "provisional_dynamic_bend_radius_mm": round(provisional_radius, 6),
            "provisional_radial_margin_mm": round(margin, 6),
            "radial_packaging_screen": status,
            "joint_travel_definition_status": "missing",
            "motion_strategy_status": "unselected",
            "cad_motion_path_status": "straight_centerline_envelope_only",
            "qualification_status": "incomplete",
        })

    return {
        "schema": SCHEMA,
        "product_id": product_id,
        "requirements_sha256": requirements_sha256,
        "overall_status": "incomplete",
        "cad_route_representation": {
            "geometry": "straight_centerline_cylinders",
            "purpose": "static_packaging_and_visibility_only",
            "motion_geometry_status": "not_materialized",
            "finite_corner_radius_status": "not_materialized",
        },
        "selected_cable": {
            "part_number": None,
            "supplier_dynamic_bend_radius_mm": None,
            "supplier_torsion_limit_deg_per_m": None,
            "supplier_qualified_cycle_count": None,
            "status": "missing",
        },
        "screening_assumptions": {
            "worst_case_bundle_diameter_mm": round(worst_bundle_diameter, 6),
            "electrical_service_length_allowance_mm": round(service_length, 6),
            "provisional_dynamic_bend_radius_multiplier": (
                _SCREENING_BEND_RADIUS_MULTIPLIER),
            "provisional_dynamic_bend_radius_mm": round(provisional_radius, 6),
            "authority": "engineering_screen_only_not_a_supplier_cable_rating",
            "envelope_scope": (
                "optimistic_empty_joint_radius_excluding_motor_gear_bearing_pcb_"
                "connector_and_retainer_volumes"),
        },
        "joint_interfaces": joint_screens,
        "provisionally_incompatible_radial_loop_joints": incompatible,
        "blocking_findings": [
            {
                "finding_id": "HARNESS-CABLE-001",
                "owner_domains": ["electrical", "procurement", "mechanical"],
                "missing_inputs": [
                    "selected_harness_part_number_and_construction",
                    "supplier_dynamic_bend_radius",
                    "supplier_torsion_limit",
                    "supplier_cycle_life_and_temperature_derating",
                ],
                "blocked_outputs": ["motion_route_acceptance", "harness_life_prediction"],
            },
            {
                "finding_id": "HARNESS-TRAVEL-001",
                "owner_domains": ["mechanical", "controls", "safety"],
                "missing_inputs": [
                    "joint_minimum_and_maximum_angles",
                    "continuous_rotation_joint_classification",
                    "representative_motion_duty_cycles",
                ],
                "blocked_outputs": ["cable_twist_demand", "swept_route_geometry"],
            },
            {
                "finding_id": "HARNESS-PACKAGING-001",
                "owner_domains": ["mechanical", "electrical", "electronics"],
                "missing_inputs": [
                    "occupied_joint_internal_volumes",
                    "connector_and_strain_relief_geometry",
                    "selected_motion_strategy_per_joint",
                ],
                "blocked_outputs": ["collision_free_service_loop", "retainer_definition"],
                "provisionally_incompatible_joints": incompatible,
            },
            {
                "finding_id": "HARNESS-CAD-001",
                "owner_domains": ["mechanical"],
                "missing_inputs": [
                    "finite_radius_swept_cable_centerline",
                    "pose_dependent_cable_sweep",
                    "strain_relief_and_retainer_features",
                ],
                "blocked_outputs": ["manufacturable_harness_route", "cable_collision_check"],
            },
            {
                "finding_id": "HARNESS-TEST-001",
                "owner_domains": ["electrical", "mechanical", "safety"],
                "missing_inputs": [
                    "bend_and_torsion_cycle_test_receipt",
                    "post_cycle_continuity_and_insulation_results",
                    "temperature_rise_and_shield_bond_results",
                ],
                "blocked_outputs": ["motion_qualification"],
            },
        ],
        "required_next_actions": [
            "define_joint_travel_and_continuous_rotation_requirements",
            "select_or_specify_the_complete_flex_rated_harness",
            "choose_flex_loop_distributed_torsion_or_rotary_interface_per_joint",
            "materialize_finite_radius_routes_and_all_occupied_internal_volumes",
            "run_pose_sweep_collision_bend_and_torsion_checks",
            "perform_environmental_motion_cycle_testing_on_the_physical_harness",
        ],
    }


__all__ = ["SCHEMA", "build_harness_motion_contract"]
