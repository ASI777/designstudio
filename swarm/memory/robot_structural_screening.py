"""Deterministic whole-robot structural screening for authoritative bundles.

The model deliberately stays below full finite-element qualification.  It uses
Castigliano/unit-load integration over the serial robot's tapered annular
aluminium link structures, including axial, biaxial bending and torsional
compliance.  A conservative equivalent-stiffness frequency screen is reported
separately and must never be described as an assembly eigenanalysis.
"""
from __future__ import annotations

import math
from typing import Any


_GAUSS_POINTS = (
    -0.9602898564975363,
    -0.7966664774136267,
    -0.5255324099163290,
    -0.1834346424956498,
    0.1834346424956498,
    0.5255324099163290,
    0.7966664774136267,
    0.9602898564975363,
)
_GAUSS_WEIGHTS = (
    0.1012285362903763,
    0.2223810344533745,
    0.3137066458778873,
    0.3626837833783620,
    0.3626837833783620,
    0.3137066458778873,
    0.2223810344533745,
    0.1012285362903763,
)
_DIRECTIONS = ("x", "y", "z")


class StructuralScreeningError(ValueError):
    """Raised when a derived structure cannot satisfy a deterministic rule."""


def _add(first: list[float], second: list[float]) -> list[float]:
    return [first[index] + second[index] for index in range(3)]


def _subtract(first: list[float], second: list[float]) -> list[float]:
    return [first[index] - second[index] for index in range(3)]


def _scale(vector: list[float], factor: float) -> list[float]:
    return [component * factor for component in vector]


def _dot(first: list[float], second: list[float]) -> float:
    return sum(first[index] * second[index] for index in range(3))


def _cross(first: list[float], second: list[float]) -> list[float]:
    return [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]


def _magnitude(vector: list[float]) -> float:
    return math.sqrt(max(0.0, _dot(vector, vector)))


def _quantity_max(quantity: dict[str, Any]) -> float:
    return quantity["value"] + quantity["tolerance"]["plus"]


def _quantity_min(quantity: dict[str, Any]) -> float:
    return quantity["value"] - quantity["tolerance"]["minus"]


def derive_link_structures(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive one shared physical section definition for CAD and analysis."""

    segments = spec["kinematics"]["segments"]
    manufacturing = spec["manufacturing"]
    electrical = spec["electrical"]
    endpoint_ratio = manufacturing["link_endpoint_radius_ratio"]["value"]
    body_ratio = manufacturing["link_body_radius_ratio"]["value"]
    maximum_transition_mm = manufacturing["link_transition_length"]["value"]
    skin_wall = manufacturing["shell_wall"]["value"]
    skin_clearance = manufacturing["structural_shell_clearance"]["value"]
    target_wall = manufacturing["target_structural_shell_wall"]["value"]
    minimum_wall = manufacturing["minimum_structural_wall"]["value"]
    target_wall_tolerance = manufacturing["target_structural_shell_wall"]["tolerance"]
    worst_channel_radius = (
        _quantity_max(electrical["harness_bundle_diameter"]) / 2.0
        + _quantity_max(manufacturing["service_clearance"])
    )
    structures: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        next_segment = segments[index + 1] if index + 1 < len(segments) else segment
        nominal_length = segment["length"]["value"]
        worst_length = _quantity_max(segment["length"])
        # Short wrist links remain neck-sized throughout.  Long load-bearing
        # links transition to a deeper body section after clearing each joint.
        if nominal_length >= 140.0:
            transition = min(maximum_transition_mm, nominal_length * 0.20)
            nominal_stations = [0.0, transition, nominal_length - transition, nominal_length]
            worst_stations = [0.0, transition, worst_length - transition, worst_length]
            nominal_cosmetic_radii = [
                segment["outer_diameter"]["value"] * endpoint_ratio,
                segment["outer_diameter"]["value"] * body_ratio,
                next_segment["outer_diameter"]["value"] * body_ratio,
                next_segment["outer_diameter"]["value"] * endpoint_ratio,
            ]
            worst_cosmetic_radii = [
                _quantity_min(segment["outer_diameter"]) * endpoint_ratio,
                _quantity_min(segment["outer_diameter"]) * body_ratio,
                _quantity_min(next_segment["outer_diameter"]) * body_ratio,
                _quantity_min(next_segment["outer_diameter"]) * endpoint_ratio,
            ]
            profile_kind = "necked_tapered_body_with_two_transitions"
        else:
            nominal_stations = [0.0, nominal_length]
            worst_stations = [0.0, worst_length]
            nominal_cosmetic_radii = [
                segment["outer_diameter"]["value"] * endpoint_ratio,
                next_segment["outer_diameter"]["value"] * endpoint_ratio,
            ]
            worst_cosmetic_radii = [
                _quantity_min(segment["outer_diameter"]) * endpoint_ratio,
                _quantity_min(next_segment["outer_diameter"]) * endpoint_ratio,
            ]
            profile_kind = "compact_endpoint_taper"
        structural_outer_radii = [
            radius - skin_wall - skin_clearance
            for radius in nominal_cosmetic_radii
        ]
        selected_wall = target_wall
        if min(structural_outer_radii) <= selected_wall:
            raise StructuralScreeningError(
                f"{segment['joint_id']} structural shell has no positive inner radius")

        worst_outer_radii = [
            radius - _quantity_max(manufacturing["shell_wall"])
            - _quantity_max(manufacturing["structural_shell_clearance"])
            for radius in worst_cosmetic_radii
        ]
        minimum_realized_wall = selected_wall - target_wall_tolerance["minus"]
        maximum_realized_wall = selected_wall + target_wall_tolerance["plus"]
        minimum_channel_inner_radius = min(worst_outer_radii) - maximum_realized_wall
        if minimum_realized_wall < minimum_wall:
            raise StructuralScreeningError(
                f"{segment['joint_id']} structural shell wall falls below the minimum")
        if minimum_channel_inner_radius < worst_channel_radius:
            raise StructuralScreeningError(
                f"{segment['joint_id']} structural shell cannot contain the worst-case harness channel")
        minimum_generated_diameter = 2.0 * min(worst_outer_radii)
        required_diameter = _quantity_max(segment["link_structural_diameter"])
        if minimum_generated_diameter < required_diameter:
            raise StructuralScreeningError(
                f"{segment['joint_id']} generated structural diameter is below its requirement")

        structures.append({
            "joint_id": segment["joint_id"],
            "material_id": segment["structural_material"],
            "profile_kind": profile_kind,
            "length_mm": nominal_length,
            "profile_stations_mm": nominal_stations,
            "worst_profile_stations_mm": worst_stations,
            "cosmetic_outer_radii_mm": nominal_cosmetic_radii,
            "structural_outer_radii_mm": structural_outer_radii,
            "structural_inner_radii_mm": [
                radius - selected_wall for radius in structural_outer_radii],
            "worst_outer_radii_mm": worst_outer_radii,
            "worst_inner_radii_mm": [
                radius - minimum_realized_wall for radius in worst_outer_radii],
            "cosmetic_outer_radius_start_mm": nominal_cosmetic_radii[0],
            "cosmetic_outer_radius_end_mm": nominal_cosmetic_radii[-1],
            "structural_outer_radius_start_mm": structural_outer_radii[0],
            "structural_outer_radius_end_mm": structural_outer_radii[-1],
            "structural_inner_radius_start_mm": structural_outer_radii[0] - selected_wall,
            "structural_inner_radius_end_mm": structural_outer_radii[-1] - selected_wall,
            "selected_nominal_wall_mm": selected_wall,
            "minimum_realized_wall_mm": minimum_realized_wall,
            "worst_outer_radius_start_mm": worst_outer_radii[0],
            "worst_outer_radius_end_mm": worst_outer_radii[-1],
            "worst_inner_radius_start_mm": worst_outer_radii[0] - minimum_realized_wall,
            "worst_inner_radius_end_mm": worst_outer_radii[-1] - minimum_realized_wall,
            "worst_harness_channel_radius_mm": worst_channel_radius,
            "minimum_generated_structural_diameter_mm": minimum_generated_diameter,
            "required_minimum_structural_diameter_mm": required_diameter,
            "wall_check": "pass",
            "channel_check": "pass",
            "diameter_check": "pass",
        })
    return structures


def _interpolate_profile(
    stations: list[float], values: list[float], coordinate: float,
) -> float:
    for index in range(len(stations) - 1):
        if coordinate <= stations[index + 1] + 1.0e-12:
            span = stations[index + 1] - stations[index]
            fraction = (coordinate - stations[index]) / span
            return values[index] + fraction * (values[index + 1] - values[index])
    return values[-1]


def _section_properties(
    structure: dict[str, Any],
    material: dict[str, Any],
    coordinate_mm: float,
) -> dict[str, float]:
    stations = structure["worst_profile_stations_mm"]
    outer_mm = _interpolate_profile(
        stations, structure["worst_outer_radii_mm"], coordinate_mm)
    inner_mm = _interpolate_profile(
        stations, structure["worst_inner_radii_mm"], coordinate_mm)
    outer = outer_mm / 1000.0
    inner = inner_mm / 1000.0
    area = math.pi * (outer * outer - inner * inner)
    second_moment = math.pi / 4.0 * (outer ** 4 - inner ** 4)
    polar_moment = 2.0 * second_moment
    properties = material["screening_properties"]
    elastic_modulus = properties["elastic_modulus"]["value"] * 1.0e9
    poisson_ratio = properties["poisson_ratio"]["value"]
    shear_modulus = elastic_modulus / (2.0 * (1.0 + poisson_ratio))
    return {
        "area_m2": area,
        "second_moment_m4": second_moment,
        "polar_moment_m4": polar_moment,
        "elastic_modulus_pa": elastic_modulus,
        "shear_modulus_pa": shear_modulus,
    }


def _integration_sections(
    structure: dict[str, Any], material: dict[str, Any], length_m: float,
):
    stations = structure["worst_profile_stations_mm"]
    for interval in range(len(stations) - 1):
        start_mm = stations[interval]
        end_mm = stations[interval + 1]
        span_m = (end_mm - start_mm) / 1000.0
        for point, weight in zip(_GAUSS_POINTS, _GAUSS_WEIGHTS, strict=True):
            coordinate_mm = start_mm + (point + 1.0) / 2.0 * (end_mm - start_mm)
            coordinate_m = coordinate_mm / 1000.0
            yield (
                coordinate_m / length_m,
                coordinate_m,
                _section_properties(structure, material, coordinate_mm),
                weight * span_m / 2.0,
            )


def build_robot_structural_screening(
    spec: dict[str, Any],
    *,
    requirements_sha256: str,
    link_structures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate neutral-pose system compliance and conservative frequency."""

    structures = link_structures or derive_link_structures(spec)
    segments = spec["kinematics"]["segments"]
    if len(structures) != len(segments):
        raise StructuralScreeningError("link structure count does not match kinematic segments")

    uncertainty_keys = (
        "load_uncertainty_factor",
        "material_variability_factor",
        "manufacturing_variability_factor",
        "model_uncertainty_factor",
        "consequence_factor",
    )
    uncertainty_factors = {
        key: spec["system"][key]["value"] for key in uncertainty_keys
    }
    combined_factor = math.prod(uncertainty_factors.values())
    gravity = _quantity_max(spec["system"]["gravity"])
    payload_mass = (
        _quantity_max(spec["system"]["payload"])
        + _quantity_max(spec["system"]["end_effector_mass"])
    )
    segment_masses = [_quantity_max(segment["assembly_mass"]) for segment in segments]
    lengths_m = [_quantity_max(segment["length"]) / 1000.0 for segment in segments]
    origins: list[list[float]] = [[0.0, 0.0, 0.0]]
    for segment, length in zip(segments, lengths_m, strict=True):
        origins.append(_add(origins[-1], _scale(segment["neutral_direction"], length)))
    tool_point = origins[-1]
    tool_force = [0.0, 0.0, -payload_mass * gravity * combined_factor]

    component_deflections_m = [0.0, 0.0, 0.0]
    directional_compliances_m_per_n = [0.0, 0.0, 0.0]
    per_link_component_m = [[0.0, 0.0, 0.0] for _ in segments]
    tube_masses_kg: list[float] = []

    for index, (segment, structure, length) in enumerate(
            zip(segments, structures, lengths_m, strict=True)):
        direction = segment["neutral_direction"]
        material = spec["materials"][segment["structural_material"]]
        tube_mass = 0.0
        for normalized, coordinate, properties, integration_weight in (
                _integration_sections(structure, material, length)):
            section_point = _add(origins[index], _scale(direction, coordinate))
            tube_mass += (
                integration_weight * properties["area_m2"]
                * material["screening_properties"]["density"]["value"])

            actual_loads: list[tuple[list[float], list[float]]] = [
                (tool_point, tool_force),
            ]
            for downstream in range(index + 1, len(segments)):
                downstream_midpoint = _add(
                    origins[downstream],
                    _scale(segments[downstream]["neutral_direction"],
                           lengths_m[downstream] / 2.0),
                )
                actual_loads.append((
                    downstream_midpoint,
                    [0.0, 0.0, -segment_masses[downstream] * gravity * combined_factor],
                ))
            remaining_fraction = max(0.0, 1.0 - normalized)
            if remaining_fraction > 0.0:
                remaining_centroid = _add(
                    section_point,
                    _scale(direction, (length - coordinate) / 2.0),
                )
                actual_loads.append((
                    remaining_centroid,
                    [0.0, 0.0, -segment_masses[index] * remaining_fraction
                     * gravity * combined_factor],
                ))

            actual_force = [0.0, 0.0, 0.0]
            actual_moment = [0.0, 0.0, 0.0]
            for load_point, load_force in actual_loads:
                actual_force = _add(actual_force, load_force)
                actual_moment = _add(
                    actual_moment,
                    _cross(_subtract(load_point, section_point), load_force),
                )
            actual_axial = _dot(actual_force, direction)
            actual_torsion = _dot(actual_moment, direction)
            actual_bending = _subtract(
                actual_moment, _scale(direction, actual_torsion))

            for component in range(3):
                unit_force = [0.0, 0.0, 0.0]
                unit_force[component] = 1.0
                unit_moment = _cross(
                    _subtract(tool_point, section_point), unit_force)
                unit_axial = _dot(unit_force, direction)
                unit_torsion = _dot(unit_moment, direction)
                unit_bending = _subtract(
                    unit_moment, _scale(direction, unit_torsion))
                cross_energy_density = (
                    actual_axial * unit_axial
                    / (properties["elastic_modulus_pa"] * properties["area_m2"])
                    + _dot(actual_bending, unit_bending)
                    / (properties["elastic_modulus_pa"]
                       * properties["second_moment_m4"])
                    + actual_torsion * unit_torsion
                    / (properties["shear_modulus_pa"]
                       * properties["polar_moment_m4"])
                )
                compliance_density = (
                    unit_axial * unit_axial
                    / (properties["elastic_modulus_pa"] * properties["area_m2"])
                    + _dot(unit_bending, unit_bending)
                    / (properties["elastic_modulus_pa"]
                       * properties["second_moment_m4"])
                    + unit_torsion * unit_torsion
                    / (properties["shear_modulus_pa"]
                       * properties["polar_moment_m4"])
                )
                contribution = cross_energy_density * integration_weight
                component_deflections_m[component] += contribution
                per_link_component_m[index][component] += contribution
                directional_compliances_m_per_n[component] += (
                    compliance_density * integration_weight)
        tube_masses_kg.append(tube_mass)

    deflections_mm = [value * 1000.0 for value in component_deflections_m]
    magnitude_mm = _magnitude(deflections_mm)
    maximum_deflection = spec["system"]["maximum_design_load_tool_deflection"]["value"]
    static_margin = maximum_deflection / max(magnitude_mm, 1.0e-15)
    static_result = "pass" if static_margin >= 1.0 else "fail"

    compliances_mm_per_n = [value * 1000.0 for value in directional_compliances_m_per_n]
    stiffnesses_n_per_mm = [
        1.0 / max(value, 1.0e-15) for value in compliances_mm_per_n]
    total_moving_mass = payload_mass + sum(segment_masses)
    frequency_estimates = [
        1.0 / (2.0 * math.pi) * math.sqrt(
            (1.0 / max(compliance, 1.0e-15)) / total_moving_mass)
        for compliance in directional_compliances_m_per_n
    ]
    first_frequency = min(frequency_estimates)
    required_frequency = spec["system"]["minimum_screening_first_mode"]["value"]
    modal_margin = first_frequency / required_frequency
    modal_result = "pass" if modal_margin >= 1.0 else "fail"

    return {
        "schema": "design-studio.robot-system-structural-screening/1",
        "product_id": spec["product_id"],
        "requirements_sha256": requirements_sha256,
        "configuration": {
            "name": "requirements_neutral_pose",
            "joint_origins_mm": [
                [round(component * 1000.0, 6) for component in origin]
                for origin in origins[:-1]
            ],
            "tool_point_mm": [round(component * 1000.0, 6) for component in tool_point],
            "fixed_boundary": "all_six_rigid_body_dofs_at_base_origin",
        },
        "load_case": {
            "name": "factored_gravity_payload_and_distributed_assembly_mass",
            "gravity_m_per_s2": round(gravity, 8),
            "payload_and_end_effector_mass_kg": round(payload_mass, 6),
            "segment_assembly_masses_kg": [round(value, 6) for value in segment_masses],
            "uncertainty_factors": uncertainty_factors,
            "combined_uncertainty_factor": round(combined_factor, 8),
            "tool_force_n": [round(value, 6) for value in tool_force],
        },
        "method": {
            "static": "castigliano_unit_load_on_serial_tapered_annular_beams",
            "integrated_terms": ["axial", "biaxial_bending", "torsion"],
            "quadrature": "eight_point_gauss_legendre_per_profile_segment",
            "distributed_mass": "uniform_mass_per_link_integrated_as_downstream_gravity",
            "modal_screen": "directional_equivalent_stiffness_with_full_moving_mass",
            "modal_screen_is_eigenanalysis": False,
            "target_repeatability_used_as_static_limit": False,
        },
        "link_sections": [
            {key: round(value, 6) if isinstance(value, float) else value
             for key, value in structure.items()}
            for structure in structures
        ],
        "static_tool_deflection": {
            "components_mm": {
                direction: round(deflections_mm[index], 6)
                for index, direction in enumerate(_DIRECTIONS)
            },
            "magnitude_mm": round(magnitude_mm, 6),
            "maximum_allowed_mm": maximum_deflection,
            "margin": round(static_margin, 6),
            "result": static_result,
            "per_link_component_contributions_mm": [
                {
                    "joint_id": segments[index]["joint_id"],
                    **{
                        direction: round(per_link_component_m[index][component] * 1000.0, 9)
                        for component, direction in enumerate(_DIRECTIONS)
                    },
                }
                for index in range(len(segments))
            ],
        },
        "directional_tool_compliance": {
            direction: {
                "compliance_mm_per_n": round(compliances_mm_per_n[index], 9),
                "stiffness_n_per_mm": round(stiffnesses_n_per_mm[index], 6),
            }
            for index, direction in enumerate(_DIRECTIONS)
        },
        "frequency_screen": {
            "directional_estimates_hz": {
                direction: round(frequency_estimates[index], 6)
                for index, direction in enumerate(_DIRECTIONS)
            },
            "minimum_estimate_hz": round(first_frequency, 6),
            "minimum_required_hz": required_frequency,
            "margin": round(modal_margin, 6),
            "result": modal_result,
            "full_moving_mass_kg": round(total_moving_mass, 6),
        },
        "estimated_structural_tube_masses_kg": [
            round(value, 6) for value in tube_masses_kg],
        "screening_result": (
            "pass" if static_result == "pass" and modal_result == "pass" else "fail"),
        "qualification_status": "screening_only",
        "excluded_physics": [
            "bearing_and_reducer_compliance",
            "joint_contact_and_preload",
            "base_and_tool_interface_compliance",
            "geometric_nonlinearity",
            "material_nonlinearity",
            "damping",
            "assembly_eigenvectors_and_mode_shapes",
            "pose_sweep",
            "experimental_correlation",
        ],
    }


__all__ = [
    "StructuralScreeningError",
    "build_robot_structural_screening",
    "derive_link_structures",
]
