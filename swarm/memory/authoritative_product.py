"""Compile a traceable cross-domain hardware definition into immutable assets.

The compiler is deliberately requirements-first.  It accepts no inferred
dimensions from visual media, performs every deterministic engineering check
before writing, and publishes a complete directory with one atomic rename.
Visual references can constrain form language and packaging relationships but
cannot silently become millimetres or copied surface geometry.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from swarm.memory.authoritative_dimensions import (
    canonical_bytes,
    canonical_digest,
    validate_dimension_set,
)
from swarm.memory.electronics_components import (
    evaluate_joint_controller_selection,
    validate_component_bindings,
)
from swarm.memory.joint_electronics import build_joint_electronics_contract
from swarm.memory.harness_motion import build_harness_motion_contract
from swarm.memory.robot_brake_sizing import BrakeSizingError, build_robot_brake_sizing
from swarm.memory.robot_fatigue_contract import (
    FatigueContractError,
    build_robot_fatigue_contract,
)
from swarm.memory.robot_electrical_integrity import (
    ElectricalIntegrityError,
    build_robot_electrical_integrity_contract,
)
from swarm.memory.robot_functional_safety import (
    FunctionalSafetyError,
    build_robot_functional_safety_contract,
)
from swarm.memory.robot_accuracy_controls import (
    AccuracyControlsError,
    build_robot_accuracy_controls_contract,
)
from swarm.memory.robot_thermal_network import (
    ThermalNetworkError,
    build_robot_thermal_network_contract,
)
from swarm.memory.robot_structural_screening import (
    StructuralScreeningError,
    build_robot_structural_screening,
    derive_link_structures,
)


SPEC_SCHEMA = "design-studio.cross-domain-product-requirements/2"
BUNDLE_SCHEMA = "design-studio.authoritative-product-bundle/1"
EVIDENCE_SCHEMA = "design-studio.design-evidence-set/1"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_Q_FIELDS = {"value", "unit", "tolerance", "criticality"}


class ProductCompileError(ValueError):
    """Raised before publication when a product definition is incomplete."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def _sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")


def _finite(value: Any, path: str, issues: list[str], *, positive: bool = False,
            minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(f"{path} must be a finite number")
        return 0.0
    result = float(value)
    if not math.isfinite(result):
        issues.append(f"{path} must be a finite number")
        return 0.0
    if positive and result <= 0:
        issues.append(f"{path} must be positive")
    if minimum is not None and result < minimum:
        issues.append(f"{path} must be at least {minimum}")
    if maximum is not None and result > maximum:
        issues.append(f"{path} must be at most {maximum}")
    return result


def _quantity(raw: Any, path: str, issues: list[str], *, unit: str | None = None,
              positive: bool = True, minimum: float | None = None,
              maximum: float | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _Q_FIELDS:
        issues.append(f"{path} must contain exactly {sorted(_Q_FIELDS)}")
        return {"value": 0.0, "unit": unit or "1", "tolerance": {"minus": 0, "plus": 0},
                "criticality": "functional"}
    value = _finite(raw.get("value"), f"{path}.value", issues, positive=positive,
                    minimum=minimum, maximum=maximum)
    if unit is not None and raw.get("unit") != unit:
        issues.append(f"{path}.unit must be {unit!r}")
    tolerance = raw.get("tolerance")
    if not isinstance(tolerance, dict) or set(tolerance) != {"minus", "plus"}:
        issues.append(f"{path}.tolerance must contain exactly minus and plus")
        tolerance = {"minus": 0, "plus": 0}
    else:
        minus = _finite(tolerance.get("minus"), f"{path}.tolerance.minus", issues,
                        minimum=0)
        plus = _finite(tolerance.get("plus"), f"{path}.tolerance.plus", issues,
                       minimum=0)
        tolerance = {"minus": minus, "plus": plus}
    if raw.get("criticality") not in {
        "interface", "safety", "functional", "manufacturing", "cosmetic"
    }:
        issues.append(f"{path}.criticality is unsupported")
    return {"value": value, "unit": raw.get("unit"), "tolerance": tolerance,
            "criticality": raw.get("criticality")}


def _quantity_upper(quantity: dict[str, Any]) -> float:
    """Return the upper manufactured or operating bound."""
    return float(quantity["value"] + quantity["tolerance"]["plus"])


def _quantity_lower(quantity: dict[str, Any]) -> float:
    """Return the lower manufactured or operating bound."""
    return float(quantity["value"] - quantity["tolerance"]["minus"])


def _unit_vector(raw: Any, path: str, issues: list[str]) -> list[float]:
    if not isinstance(raw, list) or len(raw) != 3:
        issues.append(f"{path} must be a three-value vector")
        return [1.0, 0.0, 0.0]
    values = [_finite(item, f"{path}[{index}]", issues) for index, item in enumerate(raw)]
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        issues.append(f"{path} must be non-zero")
        return [1.0, 0.0, 0.0]
    return [value / norm for value in values]


def _validate_evidence(records: Any, issues: list[str]) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        issues.append("visual_evidence must contain at least one source")
        return []
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(records):
        path = f"visual_evidence[{index}]"
        required = {
            "id", "media_kind", "locator", "creator", "rights_basis", "use",
            "geometry_copy_allowed", "dimensional_measurement_allowed", "observations",
            "observed_date", "source_fingerprint_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            issues.append(f"{path} fields differ from the evidence contract")
            continue
        evidence_id = raw.get("id")
        if not isinstance(evidence_id, str) or not _ID.fullmatch(evidence_id) or evidence_id in ids:
            issues.append(f"{path}.id must be unique and stable")
        else:
            ids.add(evidence_id)
        if raw.get("media_kind") not in {"local_video", "external_page", "local_image"}:
            issues.append(f"{path}.media_kind is unsupported")
        for key in ("locator", "creator", "rights_basis", "use", "observed_date"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                issues.append(f"{path}.{key} is required")
        if raw.get("geometry_copy_allowed") is not False:
            issues.append(f"{path}.geometry_copy_allowed must be false")
        if raw.get("dimensional_measurement_allowed") is not False:
            issues.append(f"{path}.dimensional_measurement_allowed must be false")
        digest = raw.get("source_fingerprint_sha256")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            issues.append(f"{path}.source_fingerprint_sha256 is invalid")
        observations = raw.get("observations")
        if not isinstance(observations, list) or not observations or any(
                not isinstance(item, str) or not item.strip() for item in observations):
            issues.append(f"{path}.observations must be non-empty statements")
        normalized.append(dict(raw))
    return normalized


def _source_quantity(identifier: str, raw: dict[str, Any], source_digest: str,
                     source_revision: str, reference: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "value": raw["value"],
        "unit": raw["unit"],
        "tolerance": raw["tolerance"],
        "criticality": raw["criticality"],
        "source": {
            "kind": "engineering_requirement",
            "reference": reference,
            "revision": source_revision,
            "sha256": source_digest,
        },
    }


def _derived_quantity(identifier: str, value: float, unit: str, criticality: str,
                      source_digest: str, source_revision: str, inputs: list[str],
                      method: str, tolerance: float = 0.001) -> dict[str, Any]:
    return {
        "id": identifier,
        "value": round(value, 9),
        "unit": unit,
        "tolerance": {"minus": tolerance, "plus": tolerance},
        "criticality": criticality,
        "source": {
            "kind": "derived_calculation",
            "reference": f"compiler:{identifier}",
            "revision": source_revision,
            "sha256": source_digest,
            "input_quantity_ids": inputs,
            "method": method,
        },
    }


def _command(identifier: str, operation: str, params: dict[str, Any],
             references: list[str]) -> dict[str, Any]:
    return {"id": identifier, "op": operation, "params": params,
            "provenance": references}


def _point_add(first: list[float], direction: list[float], length: float) -> list[float]:
    return [first[index] + direction[index] * length for index in range(3)]


def _point_shift(first: list[float], direction: list[float], distance: float) -> list[float]:
    return [first[index] + direction[index] * distance for index in range(3)]


def _append_conical_profile(
    commands: list[dict[str, Any]],
    identifier: str,
    stations_mm: list[float],
    radii_mm: list[float],
    base_mm: list[float],
    direction: list[float],
    references: list[str],
) -> None:
    """Append one piecewise-linear radius profile and fuse its segments."""

    if len(stations_mm) != len(radii_mm) or len(stations_mm) < 2:
        raise ProductCompileError([f"{identifier} profile stations and radii are inconsistent"])
    if len(stations_mm) == 2:
        commands.append(_command(identifier, "part.cone", {
            "radius1_mm": radii_mm[0], "radius2_mm": radii_mm[1],
            "height_mm": stations_mm[1] - stations_mm[0],
            "base_mm": _point_shift(base_mm, direction, stations_mm[0]),
            "direction": direction,
        }, references))
        return

    segment_ids: list[str] = []
    for index in range(len(stations_mm) - 1):
        segment_id = f"{identifier}-segment-{index + 1}"
        segment_ids.append(segment_id)
        commands.append(_command(segment_id, "part.cone", {
            "radius1_mm": radii_mm[index], "radius2_mm": radii_mm[index + 1],
            "height_mm": stations_mm[index + 1] - stations_mm[index],
            "base_mm": _point_shift(base_mm, direction, stations_mm[index]),
            "direction": direction,
        }, references))
    fused = segment_ids[0]
    for index, segment_id in enumerate(segment_ids[1:], 2):
        fuse_id = identifier if index == len(segment_ids) else f"{identifier}-fuse-{index}"
        commands.append(_command(fuse_id, "feature.fuse", {
            "base": fused, "tool": segment_id,
        }, references))
        fused = fuse_id


def _cross(first: list[float], second: list[float]) -> list[float]:
    return [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]


def _normalized(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude <= 1.0e-12:
        raise ProductCompileError(["cannot construct an orthonormal packaging basis"])
    return [component / magnitude for component in vector]


def _plane_basis(normal: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Return a deterministic right-handed basis whose Z axis is ``normal``."""
    basis_z = _normalized(normal)
    reference = [0.0, 0.0, 1.0] if abs(basis_z[2]) < 0.9 else [1.0, 0.0, 0.0]
    basis_x = _normalized(_cross(reference, basis_z))
    basis_y = _normalized(_cross(basis_z, basis_x))
    return basis_x, basis_y, basis_z


def validate_product_requirements(document: Any, *, source_digest: str) -> dict[str, Any]:
    issues: list[str] = []
    top_fields = {
        "schema", "product_id", "revision", "title", "use_case", "visual_evidence",
        "system", "kinematics", "materials", "electrical", "electronics", "thermal",
        "industrial_design", "manufacturing", "braking", "durability",
        "electrical_integrity", "functional_safety", "accuracy_and_controls",
        "safety", "ai_usage_records",
    }
    if not isinstance(document, dict):
        raise ProductCompileError(["product requirements must be an object"])
    if set(document) != top_fields:
        issues.append(
            f"product requirement fields differ; missing={sorted(top_fields - set(document))}, "
            f"unknown={sorted(set(document) - top_fields)}")
    if document.get("schema") != SPEC_SCHEMA:
        issues.append(f"schema must equal {SPEC_SCHEMA!r}")
    product_id = document.get("product_id")
    if not isinstance(product_id, str) or not _ID.fullmatch(product_id):
        issues.append("product_id is invalid")
    revision = document.get("revision")
    if type(revision) is not int or revision < 1:
        issues.append("revision must be a positive integer")
    for key in ("title", "use_case"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            issues.append(f"{key} is required")
    if not _DIGEST.fullmatch(source_digest):
        issues.append("source_digest must bind the exact requirement bytes")

    evidence = _validate_evidence(document.get("visual_evidence"), issues)

    system = document.get("system")
    system_required = {
        "payload", "end_effector_mass", "gravity", "load_uncertainty_factor",
        "material_variability_factor", "manufacturing_variability_factor",
        "model_uncertainty_factor", "consequence_factor", "target_reach",
        "target_repeatability", "maximum_design_load_tool_deflection",
        "minimum_screening_first_mode",
    }
    if not isinstance(system, dict) or set(system) != system_required:
        issues.append("system requirements are incomplete")
        system = {}
    normalized_system = {
        "payload": _quantity(system.get("payload"), "system.payload", issues, unit="kg"),
        "end_effector_mass": _quantity(system.get("end_effector_mass"),
                                         "system.end_effector_mass", issues, unit="kg"),
        "gravity": _quantity(system.get("gravity"), "system.gravity", issues,
                              unit="m_per_s2"),
        "load_uncertainty_factor": _quantity(system.get("load_uncertainty_factor"),
                                              "system.load_uncertainty_factor", issues,
                                              unit="1", minimum=1),
        "material_variability_factor": _quantity(system.get("material_variability_factor"),
                                                  "system.material_variability_factor", issues,
                                                  unit="1", minimum=1),
        "manufacturing_variability_factor": _quantity(
            system.get("manufacturing_variability_factor"),
            "system.manufacturing_variability_factor", issues, unit="1", minimum=1),
        "model_uncertainty_factor": _quantity(system.get("model_uncertainty_factor"),
                                              "system.model_uncertainty_factor", issues,
                                              unit="1", minimum=1),
        "consequence_factor": _quantity(system.get("consequence_factor"),
                                        "system.consequence_factor", issues,
                                        unit="1", minimum=1),
        "target_reach": _quantity(system.get("target_reach"), "system.target_reach",
                                   issues, unit="mm"),
        "target_repeatability": _quantity(system.get("target_repeatability"),
                                           "system.target_repeatability", issues, unit="mm"),
        "maximum_design_load_tool_deflection": _quantity(
            system.get("maximum_design_load_tool_deflection"),
            "system.maximum_design_load_tool_deflection", issues, unit="mm"),
        "minimum_screening_first_mode": _quantity(
            system.get("minimum_screening_first_mode"),
            "system.minimum_screening_first_mode", issues, unit="Hz"),
    }

    kinematics = document.get("kinematics")
    if not isinstance(kinematics, dict) or set(kinematics) != {"axis_count", "segments"}:
        issues.append("kinematics must contain axis_count and segments")
        kinematics = {}
    if kinematics.get("axis_count") != 7:
        issues.append("kinematics.axis_count must be exactly 7 for this benchmark")
    segments_raw = kinematics.get("segments")
    if not isinstance(segments_raw, list) or len(segments_raw) != 7:
        issues.append("kinematics.segments must contain exactly seven joint segments")
        segments_raw = []
    segment_fields = {
        "joint_id", "axis", "gravity_sensitive", "neutral_direction", "length",
        "assembly_mass", "outer_diameter", "hub_structural_diameter",
        "link_structural_diameter", "gear_ratio",
        "gear_efficiency", "motor_torque_constant", "peak_angular_acceleration",
        "max_speed", "selected_output_torque", "selected_thermal_resistance",
        "structural_material",
    }
    segments: list[dict[str, Any]] = []
    for index, raw in enumerate(segments_raw):
        path = f"kinematics.segments[{index}]"
        if not isinstance(raw, dict) or set(raw) != segment_fields:
            issues.append(f"{path} fields differ from the joint-segment contract")
            continue
        expected_id = f"J{index + 1}"
        if raw.get("joint_id") != expected_id:
            issues.append(f"{path}.joint_id must equal {expected_id}")
        if type(raw.get("gravity_sensitive")) is not bool:
            issues.append(f"{path}.gravity_sensitive must be boolean")
        material = raw.get("structural_material")
        if material not in {"Al_7075_T6", "Al_6061_T6", "CFRP_tube"}:
            issues.append(f"{path}.structural_material is unsupported")
        segment = {
            "joint_id": raw.get("joint_id"),
            "axis": _unit_vector(raw.get("axis"), f"{path}.axis", issues),
            "gravity_sensitive": raw.get("gravity_sensitive"),
            "neutral_direction": _unit_vector(raw.get("neutral_direction"),
                                                f"{path}.neutral_direction", issues),
            "length": _quantity(raw.get("length"), f"{path}.length", issues, unit="mm"),
            "assembly_mass": _quantity(raw.get("assembly_mass"), f"{path}.assembly_mass",
                                        issues, unit="kg"),
            "outer_diameter": _quantity(raw.get("outer_diameter"),
                                         f"{path}.outer_diameter", issues, unit="mm"),
            "hub_structural_diameter": _quantity(raw.get("hub_structural_diameter"),
                                                  f"{path}.hub_structural_diameter", issues,
                                                  unit="mm"),
            "link_structural_diameter": _quantity(raw.get("link_structural_diameter"),
                                                   f"{path}.link_structural_diameter", issues,
                                                   unit="mm"),
            "gear_ratio": _quantity(raw.get("gear_ratio"), f"{path}.gear_ratio", issues,
                                     unit="1", minimum=1),
            "gear_efficiency": _quantity(raw.get("gear_efficiency"),
                                          f"{path}.gear_efficiency", issues, unit="1",
                                          minimum=0.01, maximum=1),
            "motor_torque_constant": _quantity(raw.get("motor_torque_constant"),
                                                f"{path}.motor_torque_constant", issues,
                                                unit="Nm_per_A"),
            "peak_angular_acceleration": _quantity(raw.get("peak_angular_acceleration"),
                                                    f"{path}.peak_angular_acceleration", issues,
                                                    unit="rad_per_s2"),
            "max_speed": _quantity(raw.get("max_speed"), f"{path}.max_speed", issues,
                                    unit="rad_per_s"),
            "selected_output_torque": _quantity(raw.get("selected_output_torque"),
                                                 f"{path}.selected_output_torque", issues,
                                                 unit="Nm"),
            "selected_thermal_resistance": _quantity(
                raw.get("selected_thermal_resistance"),
                f"{path}.selected_thermal_resistance", issues, unit="degC_per_W"),
            "structural_material": material,
        }
        if segment["hub_structural_diameter"]["value"] >= segment["outer_diameter"]["value"]:
            issues.append(f"{path}.hub_structural_diameter must fit inside outer_diameter")
        if segment["link_structural_diameter"]["value"] >= segment["hub_structural_diameter"]["value"]:
            issues.append(f"{path}.link_structural_diameter must fit inside hub_structural_diameter")
        segments.append(segment)

    materials_raw = document.get("materials")
    expected_material_ids = {"Al_7075_T6", "Al_6061_T6"}
    if not isinstance(materials_raw, dict) or set(materials_raw) != expected_material_ids:
        issues.append("materials must define exactly the supported structural materials")
        materials_raw = {}
    material_fields = {
        "material_id", "temper", "product_form", "screening_properties",
        "sources", "qualification",
    }
    property_units = {
        "density": "kg_per_m3",
        "elastic_modulus": "GPa",
        "poisson_ratio": "1",
        "minimum_yield_strength": "MPa",
        "minimum_ultimate_strength": "MPa",
    }
    source_fields = {
        "source_id", "publisher", "title", "url", "revision", "locator",
        "document_sha256", "digest_status", "property_coverage",
    }
    qualification_fields = {
        "status", "supplier_certificate_required", "required_product_form_match",
        "prohibited_uses",
    }
    normalized_materials: dict[str, Any] = {}
    for material_id in sorted(expected_material_ids):
        raw = materials_raw.get(material_id)
        path = f"materials.{material_id}"
        if not isinstance(raw, dict) or set(raw) != material_fields:
            issues.append(f"{path} fields differ from the material contract")
            continue
        if raw.get("material_id") != material_id:
            issues.append(f"{path}.material_id must equal its map key")
        for key in ("temper", "product_form"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                issues.append(f"{path}.{key} is required")
        properties_raw = raw.get("screening_properties")
        if not isinstance(properties_raw, dict) or set(properties_raw) != set(property_units):
            issues.append(f"{path}.screening_properties is incomplete")
            properties_raw = {}
        properties = {
            key: _quantity(properties_raw.get(key), f"{path}.screening_properties.{key}",
                           issues, unit=unit,
                           minimum=0.0 if key == "poisson_ratio" else None,
                           maximum=0.5 if key == "poisson_ratio" else None)
            for key, unit in property_units.items()
        }
        if (properties["minimum_ultimate_strength"]["value"]
                < properties["minimum_yield_strength"]["value"]):
            issues.append(f"{path} ultimate strength must not be below yield strength")
        sources_raw = raw.get("sources")
        if not isinstance(sources_raw, list) or not sources_raw:
            issues.append(f"{path}.sources must be a non-empty array")
            sources_raw = []
        sources: list[dict[str, Any]] = []
        covered_properties: set[str] = set()
        for source_index, source in enumerate(sources_raw):
            source_path = f"{path}.sources[{source_index}]"
            if not isinstance(source, dict) or set(source) != source_fields:
                issues.append(f"{source_path} fields differ from the source contract")
                continue
            for key in ("source_id", "publisher", "title", "revision", "locator",
                        "digest_status"):
                if not isinstance(source.get(key), str) or not source[key].strip():
                    issues.append(f"{source_path}.{key} is required")
            if not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
                issues.append(f"{source_path}.url must be an HTTPS locator")
            document_digest = source.get("document_sha256")
            if document_digest is not None and (
                    not isinstance(document_digest, str) or not _DIGEST.fullmatch(document_digest)):
                issues.append(f"{source_path}.document_sha256 is invalid")
            if source.get("digest_status", "").startswith("sha256_verified") \
                    and document_digest is None:
                issues.append(f"{source_path} claims a verified digest without one")
            coverage = source.get("property_coverage")
            if not isinstance(coverage, list) or not coverage or any(
                    item not in property_units for item in coverage):
                issues.append(f"{source_path}.property_coverage is invalid")
                coverage = []
            covered_properties.update(coverage)
            sources.append(dict(source))
        if covered_properties != set(property_units):
            issues.append(f"{path}.sources do not cover every screening property")
        qualification = raw.get("qualification")
        if not isinstance(qualification, dict) or set(qualification) != qualification_fields:
            issues.append(f"{path}.qualification fields differ")
            qualification = {}
        if qualification.get("status") != "screening_only_not_a_certified_design_allowable":
            issues.append(f"{path}.qualification.status must remain screening-only")
        if qualification.get("supplier_certificate_required") is not True:
            issues.append(f"{path} must require a supplier certificate")
        if qualification.get("required_product_form_match") is not True:
            issues.append(f"{path} must require a product-form match")
        prohibited = qualification.get("prohibited_uses")
        required_prohibited = {
            "production_acceptance", "fatigue_life", "fracture_mechanics",
            "nonlinear_plasticity",
        }
        if not isinstance(prohibited, list) or not required_prohibited <= set(prohibited):
            issues.append(f"{path}.qualification.prohibited_uses is incomplete")
        normalized_materials[material_id] = {
            "material_id": material_id,
            "temper": raw.get("temper"),
            "product_form": raw.get("product_form"),
            "screening_properties": properties,
            "sources": sources,
            "qualification": dict(qualification),
        }

    electrical = document.get("electrical")
    electrical_fields = {
        "bus_voltage", "bus_transient_ceiling", "semiconductor_voltage_derating_factor",
        "drive_efficiency", "peak_power_factor",
        "simultaneous_motion_factor", "max_voltage_drop_percent", "trunk_resistance",
        "trunk_ampacity", "branch_resistance", "branch_ampacity",
        "harness_bundle_diameter", "service_loop_length",
    }
    if not isinstance(electrical, dict) or set(electrical) != electrical_fields:
        issues.append("electrical requirements are incomplete")
        electrical = {}
    normalized_electrical = {
        "bus_voltage": _quantity(electrical.get("bus_voltage"), "electrical.bus_voltage",
                                 issues, unit="V"),
        "bus_transient_ceiling": _quantity(
            electrical.get("bus_transient_ceiling"), "electrical.bus_transient_ceiling",
            issues, unit="V"),
        "semiconductor_voltage_derating_factor": _quantity(
            electrical.get("semiconductor_voltage_derating_factor"),
            "electrical.semiconductor_voltage_derating_factor", issues, unit="1",
            minimum=1.0),
        "drive_efficiency": _quantity(electrical.get("drive_efficiency"),
                                      "electrical.drive_efficiency", issues, unit="1",
                                      minimum=0.01, maximum=1),
        "peak_power_factor": _quantity(electrical.get("peak_power_factor"),
                                       "electrical.peak_power_factor", issues, unit="1",
                                       minimum=0.01, maximum=1),
        "simultaneous_motion_factor": _quantity(
            electrical.get("simultaneous_motion_factor"),
            "electrical.simultaneous_motion_factor", issues, unit="1",
            minimum=0.01, maximum=1),
        "max_voltage_drop_percent": _quantity(
            electrical.get("max_voltage_drop_percent"),
            "electrical.max_voltage_drop_percent", issues, unit="1", maximum=20),
        "trunk_resistance": _quantity(electrical.get("trunk_resistance"),
                                      "electrical.trunk_resistance", issues, unit="ohm_per_m"),
        "trunk_ampacity": _quantity(electrical.get("trunk_ampacity"),
                                    "electrical.trunk_ampacity", issues, unit="A"),
        "branch_resistance": _quantity(electrical.get("branch_resistance"),
                                       "electrical.branch_resistance", issues,
                                       unit="ohm_per_m"),
        "branch_ampacity": _quantity(electrical.get("branch_ampacity"),
                                     "electrical.branch_ampacity", issues, unit="A"),
        "harness_bundle_diameter": _quantity(
            electrical.get("harness_bundle_diameter"),
            "electrical.harness_bundle_diameter", issues, unit="mm"),
        "service_loop_length": _quantity(electrical.get("service_loop_length"),
                                         "electrical.service_loop_length", issues, unit="mm"),
    }

    electronics = document.get("electronics")
    electronics_fields = {
        "joint_board_width", "joint_board_height", "joint_board_thickness",
        "joint_board_stack_gap", "joint_board_count_per_module",
        "coordinator_board_width", "coordinator_board_height", "control_frequency",
        "system_boundary_isolation_voltage", "can_fd_channels", "safety_channels",
        "package_to_courtyard_area_factor", "logic_board_reserved_fraction",
        "power_board_reserved_fraction", "maximum_estimated_board_utilization",
        "component_bindings",
    }
    if not isinstance(electronics, dict) or set(electronics) != electronics_fields:
        issues.append("electronics requirements are incomplete")
        electronics = {}
    normalized_electronics = {
        "joint_board_width": _quantity(electronics.get("joint_board_width"),
                                       "electronics.joint_board_width", issues, unit="mm"),
        "joint_board_height": _quantity(electronics.get("joint_board_height"),
                                        "electronics.joint_board_height", issues, unit="mm"),
        "joint_board_thickness": _quantity(electronics.get("joint_board_thickness"),
                                           "electronics.joint_board_thickness", issues,
                                           unit="mm"),
        "joint_board_stack_gap": _quantity(electronics.get("joint_board_stack_gap"),
                                             "electronics.joint_board_stack_gap", issues,
                                             unit="mm"),
        "joint_board_count_per_module": electronics.get("joint_board_count_per_module"),
        "coordinator_board_width": _quantity(electronics.get("coordinator_board_width"),
                                             "electronics.coordinator_board_width", issues,
                                             unit="mm"),
        "coordinator_board_height": _quantity(electronics.get("coordinator_board_height"),
                                              "electronics.coordinator_board_height", issues,
                                              unit="mm"),
        "control_frequency": _quantity(electronics.get("control_frequency"),
                                       "electronics.control_frequency", issues, unit="Hz"),
        "system_boundary_isolation_voltage": _quantity(
            electronics.get("system_boundary_isolation_voltage"),
            "electronics.system_boundary_isolation_voltage", issues, unit="V"),
        "can_fd_channels": electronics.get("can_fd_channels"),
        "safety_channels": electronics.get("safety_channels"),
        "package_to_courtyard_area_factor": _quantity(
            electronics.get("package_to_courtyard_area_factor"),
            "electronics.package_to_courtyard_area_factor", issues, unit="1",
            minimum=1.0),
        "logic_board_reserved_fraction": _quantity(
            electronics.get("logic_board_reserved_fraction"),
            "electronics.logic_board_reserved_fraction", issues, unit="1",
            minimum=0.0, maximum=0.8),
        "power_board_reserved_fraction": _quantity(
            electronics.get("power_board_reserved_fraction"),
            "electronics.power_board_reserved_fraction", issues, unit="1",
            minimum=0.0, maximum=0.8),
        "maximum_estimated_board_utilization": _quantity(
            electronics.get("maximum_estimated_board_utilization"),
            "electronics.maximum_estimated_board_utilization", issues, unit="1",
            minimum=0.2, maximum=0.9),
        "component_bindings": validate_component_bindings(
            electronics.get("component_bindings"), issues),
    }
    for key in ("can_fd_channels", "safety_channels"):
        if type(normalized_electronics[key]) is not int or normalized_electronics[key] < 2:
            issues.append(f"electronics.{key} must be an integer of at least two")
    if normalized_electronics["joint_board_count_per_module"] != 2:
        issues.append("electronics.joint_board_count_per_module must be two")

    thermal = document.get("thermal")
    thermal_fields = {"maximum_ambient", "maximum_motor_winding", "maximum_driver_junction",
                      "continuous_duty_factor"}
    if not isinstance(thermal, dict) or set(thermal) != thermal_fields:
        issues.append("thermal requirements are incomplete")
        thermal = {}
    normalized_thermal = {
        "maximum_ambient": _quantity(thermal.get("maximum_ambient"),
                                     "thermal.maximum_ambient", issues, unit="degC",
                                     positive=False),
        "maximum_motor_winding": _quantity(thermal.get("maximum_motor_winding"),
                                           "thermal.maximum_motor_winding", issues, unit="degC"),
        "maximum_driver_junction": _quantity(thermal.get("maximum_driver_junction"),
                                             "thermal.maximum_driver_junction", issues,
                                             unit="degC"),
        "continuous_duty_factor": _quantity(thermal.get("continuous_duty_factor"),
                                            "thermal.continuous_duty_factor", issues, unit="1",
                                            minimum=0.01, maximum=1),
    }
    if normalized_thermal["maximum_motor_winding"]["value"] <= normalized_thermal["maximum_ambient"]["value"]:
        issues.append("maximum motor winding temperature must exceed ambient")

    industrial = document.get("industrial_design")
    industrial_fields = {
        "form_language", "exterior_colors", "visible_fastener_count", "service_seam_zones",
        "default_view", "copy_reference_geometry", "cosmetic_retention",
        "structural_retention",
    }
    if not isinstance(industrial, dict) or set(industrial) != industrial_fields:
        issues.append("industrial_design requirements are incomplete")
        industrial = {}
    if industrial.get("visible_fastener_count") != 0:
        issues.append("industrial_design.visible_fastener_count must be zero")
    if industrial.get("default_view") != "exterior":
        issues.append("industrial_design.default_view must be exterior")
    if industrial.get("copy_reference_geometry") is not False:
        issues.append("industrial_design.copy_reference_geometry must be false")
    if industrial.get("cosmetic_retention") not in {"bayonet_with_internal_latch", "internal_snap_with_service_key"}:
        issues.append("industrial_design.cosmetic_retention is unsupported")
    if industrial.get("structural_retention") != "internal_preloaded_fasteners":
        issues.append("industrial_design.structural_retention must be internal_preloaded_fasteners")
    for key in ("form_language", "exterior_colors", "service_seam_zones"):
        value = industrial.get(key)
        if not isinstance(value, list) or not value or any(
                not isinstance(item, str) or not item.strip() for item in value):
            issues.append(f"industrial_design.{key} must be a non-empty string array")

    manufacturing = document.get("manufacturing")
    manufacturing_fields = {
        "structural_process", "shell_process", "shell_wall", "rib_to_wall_ratio",
        "minimum_draft", "minimum_root_fillet", "minimum_structural_wall",
        "service_clearance", "link_endpoint_radius_ratio", "link_body_radius_ratio",
        "link_transition_length",
        "structural_shell_clearance", "target_structural_shell_wall",
    }
    if not isinstance(manufacturing, dict) or set(manufacturing) != manufacturing_fields:
        issues.append("manufacturing requirements are incomplete")
        manufacturing = {}
    if manufacturing.get("structural_process") not in {"machined_aluminum", "die_cast_aluminum"}:
        issues.append("manufacturing.structural_process is unsupported")
    if manufacturing.get("shell_process") != "injection_molding":
        issues.append("manufacturing.shell_process must be injection_molding")
    normalized_manufacturing = {
        "structural_process": manufacturing.get("structural_process"),
        "shell_process": manufacturing.get("shell_process"),
        "shell_wall": _quantity(manufacturing.get("shell_wall"),
                                "manufacturing.shell_wall", issues, unit="mm"),
        "rib_to_wall_ratio": _quantity(manufacturing.get("rib_to_wall_ratio"),
                                       "manufacturing.rib_to_wall_ratio", issues, unit="1",
                                       minimum=0.35, maximum=0.7),
        "minimum_draft": _quantity(manufacturing.get("minimum_draft"),
                                   "manufacturing.minimum_draft", issues, unit="deg"),
        "minimum_root_fillet": _quantity(manufacturing.get("minimum_root_fillet"),
                                         "manufacturing.minimum_root_fillet", issues,
                                         unit="mm"),
        "minimum_structural_wall": _quantity(
            manufacturing.get("minimum_structural_wall"),
            "manufacturing.minimum_structural_wall", issues, unit="mm"),
        "service_clearance": _quantity(manufacturing.get("service_clearance"),
                                       "manufacturing.service_clearance", issues, unit="mm"),
        "link_endpoint_radius_ratio": _quantity(
            manufacturing.get("link_endpoint_radius_ratio"),
            "manufacturing.link_endpoint_radius_ratio", issues, unit="1",
            minimum=0.20, maximum=0.32),
        "link_body_radius_ratio": _quantity(
            manufacturing.get("link_body_radius_ratio"),
            "manufacturing.link_body_radius_ratio", issues, unit="1",
            minimum=0.20, maximum=0.42),
        "link_transition_length": _quantity(
            manufacturing.get("link_transition_length"),
            "manufacturing.link_transition_length", issues, unit="mm"),
        "structural_shell_clearance": _quantity(
            manufacturing.get("structural_shell_clearance"),
            "manufacturing.structural_shell_clearance", issues, unit="mm"),
        "target_structural_shell_wall": _quantity(
            manufacturing.get("target_structural_shell_wall"),
            "manufacturing.target_structural_shell_wall", issues, unit="mm"),
    }
    if (normalized_manufacturing["target_structural_shell_wall"]["value"]
            < normalized_manufacturing["minimum_structural_wall"]["value"]):
        issues.append(
            "manufacturing.target_structural_shell_wall must not be below "
            "minimum_structural_wall")
    if (normalized_manufacturing["link_body_radius_ratio"]["value"]
            < normalized_manufacturing["link_endpoint_radius_ratio"]["value"]):
        issues.append(
            "manufacturing.link_body_radius_ratio must not be below "
            "link_endpoint_radius_ratio")

    braking = document.get("braking")
    braking_fields = {
        "holding_load_factor", "minimum_hot_torque_retention",
        "minimum_brake_path_efficiency", "minimum_non_gravity_holding_fraction",
        "maximum_engagement_time", "architecture", "dynamic_stopping_policy",
    }
    if not isinstance(braking, dict) or set(braking) != braking_fields:
        issues.append("braking requirements are incomplete")
        braking = {}
    if braking.get("architecture") \
            != "spring_applied_power_released_motor_side_brake_per_joint":
        issues.append("braking.architecture must fail safe on loss of power")
    if braking.get("dynamic_stopping_policy") \
            != "controlled_drive_stop_before_static_brake_engagement":
        issues.append("braking.dynamic_stopping_policy must separate stopping and holding")
    normalized_braking = {
        "holding_load_factor": _quantity(
            braking.get("holding_load_factor"), "braking.holding_load_factor", issues,
            unit="1", minimum=1.0),
        "minimum_hot_torque_retention": _quantity(
            braking.get("minimum_hot_torque_retention"),
            "braking.minimum_hot_torque_retention", issues, unit="1",
            minimum=0.1, maximum=1.0),
        "minimum_brake_path_efficiency": _quantity(
            braking.get("minimum_brake_path_efficiency"),
            "braking.minimum_brake_path_efficiency", issues, unit="1",
            minimum=0.1, maximum=1.0),
        "minimum_non_gravity_holding_fraction": _quantity(
            braking.get("minimum_non_gravity_holding_fraction"),
            "braking.minimum_non_gravity_holding_fraction", issues, unit="1",
            minimum=0.0, maximum=1.0),
        "maximum_engagement_time": _quantity(
            braking.get("maximum_engagement_time"),
            "braking.maximum_engagement_time", issues, unit="s"),
        "architecture": braking.get("architecture"),
        "dynamic_stopping_policy": braking.get("dynamic_stopping_policy"),
    }

    durability = document.get("durability")
    durability_fields = {
        "target_service_life", "target_motion_cycles", "maximum_miner_damage",
        "load_spectrum", "cycle_counting_method", "damage_accumulation_method",
    }
    if not isinstance(durability, dict) or set(durability) != durability_fields:
        issues.append("durability requirements are incomplete")
        durability = {}
    if durability.get("cycle_counting_method") != "ASTM_E1049_rainflow":
        issues.append("durability.cycle_counting_method is unsupported")
    if durability.get("damage_accumulation_method") \
            != "Palmgren_Miner_with_design_damage_limit":
        issues.append("durability.damage_accumulation_method is unsupported")
    normalized_spectrum: list[dict[str, Any]] = []
    spectrum = durability.get("load_spectrum")
    if not isinstance(spectrum, list) or not spectrum:
        issues.append("durability.load_spectrum must be a non-empty array")
        spectrum = []
    seen_bins: set[str] = set()
    for index, raw_bin in enumerate(spectrum):
        path = f"durability.load_spectrum[{index}]"
        fields = {"bin_id", "cycle_fraction", "output_torque_fraction", "stress_ratio"}
        if not isinstance(raw_bin, dict) or set(raw_bin) != fields:
            issues.append(f"{path} fields differ from the duty-spectrum contract")
            continue
        bin_id = raw_bin.get("bin_id")
        if not isinstance(bin_id, str) or not _ID.fullmatch(bin_id) or bin_id in seen_bins:
            issues.append(f"{path}.bin_id is invalid or duplicated")
            continue
        seen_bins.add(bin_id)
        normalized_spectrum.append({
            "bin_id": bin_id,
            "cycle_fraction": _finite(
                raw_bin.get("cycle_fraction"), f"{path}.cycle_fraction", issues,
                minimum=0.0, maximum=1.0),
            "output_torque_fraction": _finite(
                raw_bin.get("output_torque_fraction"),
                f"{path}.output_torque_fraction", issues, minimum=0.0, maximum=1.0),
            "stress_ratio": _finite(
                raw_bin.get("stress_ratio"), f"{path}.stress_ratio", issues,
                positive=False, minimum=-1.0, maximum=1.0),
        })
    if normalized_spectrum and not math.isclose(
            sum(item["cycle_fraction"] for item in normalized_spectrum),
            1.0, rel_tol=0.0, abs_tol=1.0e-9):
        issues.append("durability.load_spectrum cycle fractions must sum to one")
    normalized_durability = {
        "target_service_life": _quantity(
            durability.get("target_service_life"), "durability.target_service_life",
            issues, unit="h"),
        "target_motion_cycles": _quantity(
            durability.get("target_motion_cycles"), "durability.target_motion_cycles",
            issues, unit="cycle"),
        "maximum_miner_damage": _quantity(
            durability.get("maximum_miner_damage"), "durability.maximum_miner_damage",
            issues, unit="1", minimum=0.01, maximum=1.0),
        "load_spectrum": normalized_spectrum,
        "cycle_counting_method": durability.get("cycle_counting_method"),
        "damage_accumulation_method": durability.get("damage_accumulation_method"),
    }

    electrical_integrity = document.get("electrical_integrity")
    integrity_fields = {
        "can_fd_data_rate", "nominal_differential_impedance",
        "minimum_propagation_velocity", "maximum_stub_length",
        "sample_point_fraction", "minimum_geometric_timing_margin",
        "maximum_joint_bus_ripple", "maximum_can_ground_offset",
        "shield_bonding_topology", "emc_test_targets",
    }
    if (not isinstance(electrical_integrity, dict)
            or set(electrical_integrity) != integrity_fields):
        issues.append("electrical_integrity requirements are incomplete")
        electrical_integrity = {}
    if electrical_integrity.get("shield_bonding_topology") \
            != "continuous_chassis_reference_with_360_degree_base_entry_bond":
        issues.append("electrical_integrity.shield_bonding_topology is unsupported")
    emc_targets = electrical_integrity.get("emc_test_targets")
    required_emc_targets = {
        "conducted_emissions", "radiated_emissions", "conducted_rf_immunity",
        "radiated_rf_immunity", "esd", "eft_burst", "surge",
    }
    if (not isinstance(emc_targets, list) or len(emc_targets) != len(set(emc_targets))
            or not required_emc_targets <= set(emc_targets)):
        issues.append("electrical_integrity.emc_test_targets are incomplete or duplicated")
        emc_targets = []
    normalized_electrical_integrity = {
        "can_fd_data_rate": _quantity(
            electrical_integrity.get("can_fd_data_rate"),
            "electrical_integrity.can_fd_data_rate", issues, unit="bit_per_s"),
        "nominal_differential_impedance": _quantity(
            electrical_integrity.get("nominal_differential_impedance"),
            "electrical_integrity.nominal_differential_impedance", issues, unit="ohm"),
        "minimum_propagation_velocity": _quantity(
            electrical_integrity.get("minimum_propagation_velocity"),
            "electrical_integrity.minimum_propagation_velocity", issues, unit="m_per_s"),
        "maximum_stub_length": _quantity(
            electrical_integrity.get("maximum_stub_length"),
            "electrical_integrity.maximum_stub_length", issues, unit="mm"),
        "sample_point_fraction": _quantity(
            electrical_integrity.get("sample_point_fraction"),
            "electrical_integrity.sample_point_fraction", issues,
            unit="1", minimum=0.5, maximum=0.95),
        "minimum_geometric_timing_margin": _quantity(
            electrical_integrity.get("minimum_geometric_timing_margin"),
            "electrical_integrity.minimum_geometric_timing_margin", issues,
            unit="1", minimum=0.1, maximum=1.0),
        "maximum_joint_bus_ripple": _quantity(
            electrical_integrity.get("maximum_joint_bus_ripple"),
            "electrical_integrity.maximum_joint_bus_ripple", issues, unit="V"),
        "maximum_can_ground_offset": _quantity(
            electrical_integrity.get("maximum_can_ground_offset"),
            "electrical_integrity.maximum_can_ground_offset", issues, unit="V"),
        "shield_bonding_topology": electrical_integrity.get("shield_bonding_topology"),
        "emc_test_targets": list(emc_targets),
    }

    functional_safety = document.get("functional_safety")
    functional_safety_fields = {
        "target_performance_level", "target_architecture_category",
        "minimum_diagnostic_coverage", "maximum_safe_state_response_time",
        "safe_state", "manual_reset_required", "restart_interlock_required",
        "single_fault_tolerance_required",
    }
    if (not isinstance(functional_safety, dict)
            or set(functional_safety) != functional_safety_fields):
        issues.append("functional_safety requirements are incomplete")
        functional_safety = {}
    if functional_safety.get("target_performance_level") != "PL_d":
        issues.append("functional_safety.target_performance_level must be PL_d")
    if functional_safety.get("target_architecture_category") != "Category_3":
        issues.append("functional_safety.target_architecture_category must be Category_3")
    if functional_safety.get("safe_state") \
            != "controlled_stop_then_torque_off_and_spring_brakes_engaged":
        issues.append("functional_safety.safe_state is unsupported")
    for key in (
        "manual_reset_required", "restart_interlock_required",
        "single_fault_tolerance_required",
    ):
        if functional_safety.get(key) is not True:
            issues.append(f"functional_safety.{key} must be true")
    normalized_functional_safety = {
        "target_performance_level": functional_safety.get("target_performance_level"),
        "target_architecture_category": functional_safety.get(
            "target_architecture_category"),
        "minimum_diagnostic_coverage": _quantity(
            functional_safety.get("minimum_diagnostic_coverage"),
            "functional_safety.minimum_diagnostic_coverage", issues,
            unit="1", minimum=0.9, maximum=1.0),
        "maximum_safe_state_response_time": _quantity(
            functional_safety.get("maximum_safe_state_response_time"),
            "functional_safety.maximum_safe_state_response_time", issues, unit="s"),
        "safe_state": functional_safety.get("safe_state"),
        "manual_reset_required": functional_safety.get("manual_reset_required"),
        "restart_interlock_required": functional_safety.get("restart_interlock_required"),
        "single_fault_tolerance_required": functional_safety.get(
            "single_fault_tolerance_required"),
    }

    accuracy_controls = document.get("accuracy_and_controls")
    accuracy_fields = {
        "maximum_joint_position_bandwidth", "minimum_structural_mode_separation",
        "maximum_command_to_pwm_latency", "tool_variance_allocation",
        "joint_variance_allocation", "allocation_method",
    }
    if not isinstance(accuracy_controls, dict) or set(accuracy_controls) != accuracy_fields:
        issues.append("accuracy_and_controls requirements are incomplete")
        accuracy_controls = {}
    if accuracy_controls.get("allocation_method") \
            != "root_sum_square_small_angle_worst_reach":
        issues.append("accuracy_and_controls.allocation_method is unsupported")

    def variance_allocation(
        raw: Any, path: str, expected: set[str],
    ) -> dict[str, float]:
        if not isinstance(raw, dict) or set(raw) != expected:
            issues.append(f"{path} fields are incomplete")
            return {key: 0.0 for key in expected}
        result = {
            key: _finite(raw[key], f"{path}.{key}", issues, minimum=0.0, maximum=1.0)
            for key in sorted(expected)
        }
        if not math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1.0e-9):
            issues.append(f"{path} fractions must sum to one")
        return result

    normalized_accuracy_controls = {
        "maximum_joint_position_bandwidth": _quantity(
            accuracy_controls.get("maximum_joint_position_bandwidth"),
            "accuracy_and_controls.maximum_joint_position_bandwidth", issues, unit="Hz"),
        "minimum_structural_mode_separation": _quantity(
            accuracy_controls.get("minimum_structural_mode_separation"),
            "accuracy_and_controls.minimum_structural_mode_separation", issues,
            unit="1", minimum=1.0),
        "maximum_command_to_pwm_latency": _quantity(
            accuracy_controls.get("maximum_command_to_pwm_latency"),
            "accuracy_and_controls.maximum_command_to_pwm_latency", issues, unit="s"),
        "tool_variance_allocation": variance_allocation(
            accuracy_controls.get("tool_variance_allocation"),
            "accuracy_and_controls.tool_variance_allocation",
            {"joint_sensing_and_transmission", "structural_hysteresis",
             "thermal_drift", "calibration_residual"}),
        "joint_variance_allocation": variance_allocation(
            accuracy_controls.get("joint_variance_allocation"),
            "accuracy_and_controls.joint_variance_allocation",
            {"encoder", "reducer_lost_motion", "bearing_and_joint_deflection",
             "servo_tracking_and_quantization"}),
        "allocation_method": accuracy_controls.get("allocation_method"),
    }

    safety = document.get("safety")
    safety_fields = {"standards", "functions", "compliance_claimed", "risk_assessment_required"}
    if not isinstance(safety, dict) or set(safety) != safety_fields:
        issues.append("safety requirements are incomplete")
        safety = {}
    standards = safety.get("standards")
    required_standards = {"ISO 10218-1:2025", "ISO 10218-2:2025", "ISO 13849-1:2023"}
    if not isinstance(standards, list) or not required_standards <= set(standards):
        issues.append("safety.standards must include the current robot and control references")
    functions = safety.get("functions")
    required_functions = {"emergency_stop", "dual_channel_sto", "brake_control", "joint_limit_monitoring"}
    if not isinstance(functions, list) or not required_functions <= set(functions):
        issues.append("safety.functions are incomplete")
    if safety.get("compliance_claimed") is not False:
        issues.append("safety.compliance_claimed must remain false until external qualification")
    if safety.get("risk_assessment_required") is not True:
        issues.append("safety.risk_assessment_required must be true")

    usage_records = document.get("ai_usage_records")
    if not isinstance(usage_records, list):
        issues.append("ai_usage_records must be an array")
        usage_records = []
    for index, record in enumerate(usage_records):
        if not isinstance(record, dict) or record.get("budget_policy") != "observe_only_no_hard_cap":
            issues.append(f"ai_usage_records[{index}] must use observation-only accounting")

    if issues:
        raise ProductCompileError(issues)
    return {
        "schema": SPEC_SCHEMA,
        "product_id": product_id,
        "revision": revision,
        "title": document["title"],
        "use_case": document["use_case"],
        "visual_evidence": evidence,
        "system": normalized_system,
        "kinematics": {"axis_count": 7, "segments": segments},
        "materials": normalized_materials,
        "electrical": normalized_electrical,
        "electronics": normalized_electronics,
        "thermal": normalized_thermal,
        "industrial_design": dict(industrial),
        "manufacturing": normalized_manufacturing,
        "braking": normalized_braking,
        "durability": normalized_durability,
        "electrical_integrity": normalized_electrical_integrity,
        "functional_safety": normalized_functional_safety,
        "accuracy_and_controls": normalized_accuracy_controls,
        "safety": dict(safety),
        "ai_usage_records": list(usage_records),
        "requirements_sha256": source_digest,
    }


def _compile_assets(spec: dict[str, Any]) -> dict[str, Any]:
    product_id = spec["product_id"]
    revision_text = str(spec["revision"])
    source_digest = spec["requirements_sha256"]
    quantities: list[dict[str, Any]] = []

    def add_requirement(identifier: str, raw: dict[str, Any], reference: str) -> None:
        quantities.append(_source_quantity(identifier, raw, source_digest, revision_text, reference))

    for key, raw in spec["system"].items():
        add_requirement(f"system.{key}", raw, f"{product_id}#system.{key}")
    for index, segment in enumerate(spec["kinematics"]["segments"], 1):
        for key in (
            "length", "assembly_mass", "outer_diameter", "hub_structural_diameter",
            "link_structural_diameter",
            "gear_ratio", "gear_efficiency", "motor_torque_constant",
            "peak_angular_acceleration", "max_speed", "selected_output_torque",
            "selected_thermal_resistance",
        ):
            add_requirement(f"joint.J{index}.{key}", segment[key],
                            f"{product_id}#kinematics.segments[{index - 1}].{key}")
    for material_id, material in sorted(spec["materials"].items()):
        for key, raw in material["screening_properties"].items():
            add_requirement(f"material.{material_id}.{key}", raw,
                            f"{product_id}#materials.{material_id}.screening_properties.{key}")
    for domain in (
        "electrical", "electronics", "thermal", "braking", "durability",
        "electrical_integrity",
        "functional_safety",
        "accuracy_and_controls",
    ):
        for key, raw in spec[domain].items():
            if isinstance(raw, dict) and set(raw) == _Q_FIELDS:
                add_requirement(f"{domain}.{key}", raw, f"{product_id}#{domain}.{key}")
    for key, raw in spec["manufacturing"].items():
        if isinstance(raw, dict):
            add_requirement(f"manufacturing.{key}", raw,
                            f"{product_id}#manufacturing.{key}")

    segments = spec["kinematics"]["segments"]
    try:
        link_structures = derive_link_structures(spec)
        system_structural_screening = build_robot_structural_screening(
            spec,
            requirements_sha256=source_digest,
            link_structures=link_structures,
        )
    except StructuralScreeningError as error:
        raise ProductCompileError([str(error)]) from error
    try:
        brake_sizing = build_robot_brake_sizing(
            spec, requirements_sha256=source_digest)
    except BrakeSizingError as error:
        raise ProductCompileError([str(error)]) from error
    try:
        fatigue_contract = build_robot_fatigue_contract(
            spec, requirements_sha256=source_digest)
    except FatigueContractError as error:
        raise ProductCompileError([str(error)]) from error
    if system_structural_screening["screening_result"] != "pass":
        static = system_structural_screening["static_tool_deflection"]
        frequency = system_structural_screening["frequency_screen"]
        raise ProductCompileError([
            "whole-robot structural screen failed: "
            f"tool deflection {static['magnitude_mm']:.6f} mm "
            f"(limit {static['maximum_allowed_mm']:.6f} mm), "
            f"frequency estimate {frequency['minimum_estimate_hz']:.6f} Hz "
            f"(minimum {frequency['minimum_required_hz']:.6f} Hz)"
        ])
    lengths_m = [_quantity_upper(segment["length"]) / 1000.0 for segment in segments]
    masses = [_quantity_upper(segment["assembly_mass"]) for segment in segments]
    payload_mass = (
        _quantity_upper(spec["system"]["payload"])
        + _quantity_upper(spec["system"]["end_effector_mass"]))
    gravity = _quantity_upper(spec["system"]["gravity"])
    uncertainty_budget = {
        key: spec["system"][key]["value"]
        for key in (
            "load_uncertainty_factor", "material_variability_factor",
            "manufacturing_variability_factor", "model_uncertainty_factor",
            "consequence_factor",
        )
    }
    load_factor = math.prod(uncertainty_budget.values())
    drive_efficiency = _quantity_lower(spec["electrical"]["drive_efficiency"])
    power_factor = _quantity_upper(spec["electrical"]["peak_power_factor"])
    duty = _quantity_upper(spec["thermal"]["continuous_duty_factor"])
    ambient = _quantity_upper(spec["thermal"]["maximum_ambient"])
    winding_limit = _quantity_lower(spec["thermal"]["maximum_motor_winding"])
    driver_junction_limit = _quantity_lower(spec["thermal"]["maximum_driver_junction"])
    minimum_bus_voltage = _quantity_lower(spec["electrical"]["bus_voltage"])
    binding_by_role = {
        binding["role"]: binding for binding in spec["electronics"]["component_bindings"]}
    mosfet_capabilities = binding_by_role["power_mosfet"]["capabilities"]
    shunt_capabilities = binding_by_role["phase_current_shunt"]["capabilities"]
    mosfet_rds_on_25c_ohm = mosfet_capabilities["rds_on_max_ohm"]
    mosfet_hot_resistance_factor = 2.0
    gate_drive_screening_voltage_v = 10.0
    inverter_gate_charge_power_w = (
        6.0 * mosfet_capabilities["qg_typ_nc"] * 1.0e-9
        * gate_drive_screening_voltage_v
        * spec["electronics"]["control_frequency"]["value"])
    shunt_resistance_ohm = shunt_capabilities["resistance_ohm"]

    joint_reports: list[dict[str, Any]] = []
    deterministic_failures: list[str] = []
    for index, segment in enumerate(segments):
        downstream_lengths = lengths_m[index:]
        gravity_moment = 0.0
        inertia = 0.0
        distance_before = 0.0
        input_ids: list[str] = [
            "system.payload", "system.end_effector_mass", "system.gravity",
            "system.load_uncertainty_factor", "system.material_variability_factor",
            "system.manufacturing_variability_factor", "system.model_uncertainty_factor",
            "system.consequence_factor",
            f"joint.J{index + 1}.peak_angular_acceleration",
        ]
        for downstream in range(index, len(segments)):
            com_distance = distance_before + lengths_m[downstream] / 2.0
            gravity_moment += masses[downstream] * com_distance
            inertia += masses[downstream] * (
                distance_before * distance_before
                + distance_before * lengths_m[downstream]
                + lengths_m[downstream] * lengths_m[downstream] / 3.0
            )
            distance_before += lengths_m[downstream]
            input_ids.extend([
                f"joint.J{downstream + 1}.length",
                f"joint.J{downstream + 1}.assembly_mass",
            ])
        gravity_moment += payload_mass * distance_before
        inertia += payload_mass * distance_before * distance_before
        static_torque = gravity * gravity_moment if segment["gravity_sensitive"] else 0.0
        acceleration_torque = inertia * _quantity_upper(
            segment["peak_angular_acceleration"])
        required_output = load_factor * (static_torque + acceleration_torque)
        selected = _quantity_lower(segment["selected_output_torque"])
        torque_margin = selected / max(required_output, 1e-9)
        motor_torque = required_output / (
            _quantity_lower(segment["gear_ratio"])
            * _quantity_lower(segment["gear_efficiency"]))
        phase_current = motor_torque / _quantity_lower(
            segment["motor_torque_constant"])
        mechanical_power = required_output * _quantity_upper(
            segment["max_speed"]) * power_factor
        bus_power = mechanical_power / drive_efficiency
        bus_current = bus_power / minimum_bus_voltage
        efficiency_based_loss = max(0.0, bus_power - mechanical_power) * duty
        hot_two_device_path_loss = (
            2.0 * phase_current ** 2 * mosfet_rds_on_25c_ohm
            * mosfet_hot_resistance_factor)
        three_shunt_peak_loss = 3.0 * phase_current ** 2 * shunt_resistance_ohm
        component_screen_loss = (
            hot_two_device_path_loss + three_shunt_peak_loss
            + inverter_gate_charge_power_w) * duty
        continuous_loss = max(efficiency_based_loss, component_screen_loss)
        loss_budget_basis = (
            "component_loss_screen" if component_screen_loss > efficiency_based_loss
            else "drive_efficiency_budget")
        allowable_rth = (
            (driver_junction_limit - ambient) / max(continuous_loss, 1e-9))
        selected_rth = _quantity_upper(segment["selected_thermal_resistance"])
        if torque_margin < 1.0:
            deterministic_failures.append(
                f"{segment['joint_id']} selected output torque margin is {torque_margin:.3f}")
        if selected_rth > allowable_rth:
            deterministic_failures.append(
                f"{segment['joint_id']} thermal resistance {selected_rth:.3f} exceeds "
                f"{allowable_rth:.3f} degC/W")
        torque_id = f"joint.J{index + 1}.required_output_torque"
        quantities.append(_derived_quantity(
            torque_id, required_output, "Nm", "safety", source_digest, revision_text,
            list(dict.fromkeys(input_ids)),
            "product(explicit_uncertainty_factors)*(gravity*sum(mass*lever_arm)+sum(inertia)*peak_angular_acceleration)",
        ))
        current_id = f"joint.J{index + 1}.required_phase_current"
        quantities.append(_derived_quantity(
            current_id, phase_current, "A", "functional", source_digest, revision_text,
            [torque_id, f"joint.J{index + 1}.gear_ratio",
             f"joint.J{index + 1}.gear_efficiency",
             f"joint.J{index + 1}.motor_torque_constant"],
            "required_output_torque/(gear_ratio*gear_efficiency*motor_torque_constant)",
        ))
        joint_reports.append({
            "joint_id": segment["joint_id"],
            "gravity_torque_nm": round(static_torque, 4),
            "acceleration_torque_nm": round(acceleration_torque, 4),
            "required_output_torque_nm": round(required_output, 4),
            "selected_output_torque_nm": selected,
            "torque_margin": round(torque_margin, 4),
            "required_motor_torque_nm": round(motor_torque, 4),
            "required_phase_current_a": round(phase_current, 4),
            "peak_mechanical_power_w": round(mechanical_power, 3),
            "peak_bus_power_w": round(bus_power, 3),
            "peak_bus_current_a": round(bus_current, 3),
            "efficiency_based_continuous_loss_w": round(efficiency_based_loss, 6),
            "component_screen_continuous_loss_w": round(component_screen_loss, 6),
            "continuous_power_stage_loss_budget_w": round(continuous_loss, 6),
            "loss_budget_basis": loss_budget_basis,
            "hot_two_mosfet_path_loss_w": round(hot_two_device_path_loss, 6),
            "three_shunt_peak_loss_w": round(three_shunt_peak_loss, 6),
            "six_mosfet_gate_charge_power_w": round(inverter_gate_charge_power_w, 6),
            "maximum_power_stage_to_ambient_thermal_resistance_degc_per_w": round(
                allowable_rth, 4),
            "selected_thermal_resistance_degc_per_w": selected_rth,
            "uncertainty_budget": dict(uncertainty_budget),
            "combined_uncertainty_factor": round(load_factor, 6),
        })

    try:
        coupled_thermal = build_robot_thermal_network_contract(
            spec, joint_reports, requirements_sha256=source_digest)
    except ThermalNetworkError as error:
        raise ProductCompileError([str(error)]) from error

    reach_mm = sum(segment["length"]["value"] for segment in segments)
    target_reach = spec["system"]["target_reach"]["value"]
    if reach_mm < target_reach:
        deterministic_failures.append(
            f"neutral chain reach {reach_mm:.3f} mm is below target {target_reach:.3f} mm")
    quantities.append(_derived_quantity(
        "system.chain_reach", reach_mm, "mm", "functional", source_digest, revision_text,
        [f"joint.J{index + 1}.length" for index in range(7)], "sum(segment.length)",
    ))
    dimension_set = validate_dimension_set({
        "schema": "design-studio.authoritative-dimension-set/1",
        "set_id": f"{product_id}-dimensions-r{revision_text}",
        "units_policy": "explicit_no_implicit_conversion",
        "quantities": quantities,
    })

    bus_voltage = minimum_bus_voltage
    simultaneity = _quantity_upper(spec["electrical"]["simultaneous_motion_factor"])
    service_loop_m = _quantity_upper(spec["electrical"]["service_loop_length"]) / 1000.0
    trunk_r = _quantity_upper(spec["electrical"]["trunk_resistance"])
    trunk_ampacity = _quantity_lower(spec["electrical"]["trunk_ampacity"])
    branch_r = _quantity_upper(spec["electrical"]["branch_resistance"])
    branch_ampacity = _quantity_lower(spec["electrical"]["branch_ampacity"])
    harness_motion = build_harness_motion_contract(
        product_id=product_id,
        requirements_sha256=source_digest,
        segments=segments,
        electrical=spec["electrical"],
        manufacturing=spec["manufacturing"],
    )
    screening_bend_radius_mm = harness_motion["screening_assumptions"][
        "provisional_dynamic_bend_radius_mm"]
    harness_segments: list[dict[str, Any]] = []
    cumulative_drop = 0.0
    for index in range(7):
        downstream_current = sum(report["peak_bus_current_a"] for report in joint_reports[index:]) * simultaneity
        length_m = lengths_m[index] + service_loop_m
        voltage_drop = downstream_current * 2.0 * length_m * trunk_r
        cumulative_drop += voltage_drop
        ampacity_margin = trunk_ampacity / max(downstream_current, 1e-9)
        if ampacity_margin < 1.25:
            deterministic_failures.append(
                f"harness segment H{index + 1} ampacity margin is {ampacity_margin:.3f}")
        harness_segments.append({
            "segment_id": f"H{index + 1}",
            "from": "base" if index == 0 else f"J{index}",
            "to": f"J{index + 1}",
            "route": "structural_core_centerline_electrical_length_model",
            "electrical_length_mm": round(length_m * 1000.0, 3),
            "electrical_service_length_allowance_mm": round(service_loop_m * 1000.0, 3),
            "carried_peak_current_a": round(downstream_current, 3),
            "conductor": "paired_flexible_copper_trunk",
            "conductor_resistance_ohm_per_m": trunk_r,
            "ampacity_a": trunk_ampacity,
            "ampacity_margin": round(ampacity_margin, 4),
            "segment_voltage_drop_v": round(voltage_drop, 5),
            "screening_dynamic_bend_radius_mm": screening_bend_radius_mm,
            "screening_bend_radius_basis": (
                "provisional_8x_worst_case_bundle_diameter_not_supplier_rating"),
            "motion_route_status": "incomplete_straight_centerline_envelope_only",
            "signals": ["48V", "0V", "CANFD_A_H", "CANFD_A_L", "CANFD_B_H", "CANFD_B_L",
                        "STO_A", "STO_B", "PE"],
        })
    worst_drop_pct = cumulative_drop / bus_voltage * 100.0
    if worst_drop_pct > spec["electrical"]["max_voltage_drop_percent"]["value"]:
        deterministic_failures.append(
            f"end-of-chain voltage drop {worst_drop_pct:.3f}% exceeds requirement")
    for report in joint_reports:
        branch_margin = branch_ampacity / max(report["peak_bus_current_a"], 1e-9)
        if branch_margin < 1.25:
            deterministic_failures.append(
                f"{report['joint_id']} branch conductor ampacity margin is {branch_margin:.3f}")

    if deterministic_failures:
        raise ProductCompileError(deterministic_failures)

    bus_operating_max_v = (
        spec["electrical"]["bus_voltage"]["value"]
        + spec["electrical"]["bus_voltage"]["tolerance"]["plus"])
    try:
        component_plan = evaluate_joint_controller_selection(
            spec["electronics"]["component_bindings"],
            can_fd_channels=spec["electronics"]["can_fd_channels"],
            bus_operating_max_v=bus_operating_max_v,
            bus_transient_ceiling_v=spec["electrical"]["bus_transient_ceiling"]["value"],
            semiconductor_voltage_derating_factor=spec["electrical"]
            ["semiconductor_voltage_derating_factor"]["value"],
            maximum_phase_current_a=max(
                report["required_phase_current_a"] for report in joint_reports),
            control_frequency_hz=spec["electronics"]["control_frequency"]["value"],
            board_width_mm=spec["electronics"]["joint_board_width"]["value"],
            board_height_mm=spec["electronics"]["joint_board_height"]["value"],
            package_to_courtyard_area_factor=spec["electronics"]
            ["package_to_courtyard_area_factor"]["value"],
            logic_reserved_fraction=spec["electronics"]
            ["logic_board_reserved_fraction"]["value"],
            power_reserved_fraction=spec["electronics"]
            ["power_board_reserved_fraction"]["value"],
            maximum_estimated_utilization=spec["electronics"]
            ["maximum_estimated_board_utilization"]["value"],
        )
    except ValueError as error:
        raise ProductCompileError([str(error)]) from error
    component_plan["product_id"] = product_id
    component_plan["revision"] = int(spec["revision"])
    component_plan["joint_controller_module_count"] = 7
    component_plan["joint_controller_board_topology"] = "stacked_logic_and_power_boards"
    try:
        joint_net_contract = build_joint_electronics_contract(
            component_plan["bindings"],
            operating_bus_v=spec["electrical"]["bus_voltage"]["value"],
            transient_ceiling_v=spec["electrical"]["bus_transient_ceiling"]["value"],
        )
    except ValueError as error:
        raise ProductCompileError([str(error)]) from error
    joint_net_contract["product_id"] = product_id
    joint_net_contract["revision"] = int(spec["revision"])
    for finding in joint_net_contract["unresolved_exact_bindings"]:
        if finding["finding_id"] == "ELEC-SAFE-001":
            finding["known_inputs"].extend([
                "target_performance_level_PL_d",
                "target_architecture_Category_3",
                "maximum_safe_state_response_time_0.20_s",
                "minimum_diagnostic_coverage_0.90",
                "manual_reset_restart_interlock_and_single_fault_tolerance_required",
            ])
            finding["missing_inputs"] = [
                "exact_safety_logic_and_input_components",
                "two_independent_gate_disable_paths",
                "component_failure_rate_and_diagnostic_data",
                "common_cause_and_independence_analysis",
            ]
        elif finding["finding_id"] == "ELEC-BRAKE-001":
            finding["known_inputs"].extend([
                "spring_applied_power_released_motor_side_architecture",
                "controlled_stop_before_static_brake_engagement",
                "per_joint_motor_side_static_torque_requirements_computed",
            ])
    try:
        functional_safety = build_robot_functional_safety_contract(
            spec,
            brake_sizing,
            joint_net_contract,
            requirements_sha256=source_digest,
        )
    except FunctionalSafetyError as error:
        raise ProductCompileError([str(error)]) from error
    try:
        accuracy_controls = build_robot_accuracy_controls_contract(
            spec,
            system_structural_screening,
            joint_net_contract,
            requirements_sha256=source_digest,
        )
    except AccuracyControlsError as error:
        raise ProductCompileError([str(error)]) from error

    commands: list[dict[str, Any]] = []
    semantic_parts: list[dict[str, Any]] = []
    harness_channel_checks: list[dict[str, Any]] = []
    structural_link_screens: list[dict[str, Any]] = []
    joint_origins: list[list[float]] = []
    joint_packaging: list[dict[str, Any]] = []
    molding_wall = spec["manufacturing"]["shell_wall"]["value"]
    harness_radius = spec["electrical"]["harness_bundle_diameter"]["value"] / 2.0
    channel_clearance = spec["manufacturing"]["service_clearance"]["value"]
    channel_radius = harness_radius + channel_clearance
    worst_channel_radius = (
        (spec["electrical"]["harness_bundle_diameter"]["value"]
         + spec["electrical"]["harness_bundle_diameter"]["tolerance"]["plus"]) / 2.0
        + spec["manufacturing"]["service_clearance"]["value"]
        + spec["manufacturing"]["service_clearance"]["tolerance"]["plus"])
    minimum_structural_wall = spec["manufacturing"]["minimum_structural_wall"]["value"]
    joint_board_width = spec["electronics"]["joint_board_width"]["value"]
    joint_board_height = spec["electronics"]["joint_board_height"]["value"]
    board_thickness = spec["electronics"]["joint_board_thickness"]["value"]
    board_stack_gap = spec["electronics"]["joint_board_stack_gap"]["value"]
    board_stack_depth = 2.0 * board_thickness + board_stack_gap
    coordinator_width = spec["electronics"]["coordinator_board_width"]["value"]
    coordinator_height = spec["electronics"]["coordinator_board_height"]["value"]
    position = [0.0, 0.0, 0.0]
    requirement_ref = f"requirements:{source_digest}"
    base_radius = segments[0]["outer_diameter"]["value"] * 0.72
    commands.append(_command("base-shell-outer", "part.cylinder", {
        "radius_mm": base_radius, "height_mm": 72.0,
        "base_mm": [-0.0, -0.0, -72.0], "direction": [0.0, 0.0, 1.0],
    }, [requirement_ref, "industrial_design#smooth-compact-base"]))
    commands.append(_command("base-shell-cavity", "part.cylinder", {
        "radius_mm": base_radius - molding_wall,
        "height_mm": 72.0 - 2.0 * molding_wall,
        "base_mm": [0.0, 0.0, -72.0 + molding_wall],
        "direction": [0.0, 0.0, 1.0],
    }, [requirement_ref, "manufacturing#hollow-base-shell"]))
    commands.append(_command("base-shell-body", "feature.cut", {
        "base": "base-shell-outer", "tool": "base-shell-cavity",
    }, [requirement_ref, "manufacturing#base-shell-wall"]))
    base_joint_port_radius = segments[0]["outer_diameter"]["value"] / 2.0 + channel_clearance
    commands.append(_command("base-shell-joint-port", "part.cylinder", {
        "radius_mm": base_joint_port_radius,
        "height_mm": 2.0 * (molding_wall + channel_clearance),
        "base_mm": [0.0, 0.0, -(molding_wall + channel_clearance)],
        "direction": [0.0, 0.0, 1.0],
    }, [requirement_ref, "manufacturing#base-to-j1-collar-opening"]))
    commands.append(_command("base-shell", "feature.cut", {
        "base": "base-shell-body", "tool": "base-shell-joint-port",
    }, [requirement_ref, "manufacturing#finished-base-shell"]))
    semantic_parts.append({"command_id": "base-shell", "role": "cosmetic_shell",
                           "default_visible": True, "fastener_exposure": "none"})
    for index, segment in enumerate(segments):
        link_structure = link_structures[index]
        joint_origins.append(list(position))
        joint_id = segment["joint_id"].lower()
        joint_axis = segment["axis"]
        outer_radius = segment["outer_diameter"]["value"] / 2.0
        hub_core_radius = segment["hub_structural_diameter"]["value"] / 2.0
        nominal_joint_width = max(24.0, segment["outer_diameter"]["value"] * 0.42)
        hub_width = nominal_joint_width * 0.82
        # Electronics occupy a dedicated one-sided axial bay outside the
        # structural hub.  The previous centered boxes intersected every hub.
        # Two service clearances isolate the stack from the hub and collar edge;
        # a third service-clearance allowance remains as deterministic margin.
        joint_width = max(
            nominal_joint_width,
            hub_width + 2.0 * (
                board_stack_depth + 2.0 * channel_clearance + molding_wall),
        )
        joint_base = _point_shift(position, joint_axis, -joint_width / 2.0)
        commands.append(_command(f"{joint_id}-cover-blank", "part.cylinder", {
            "radius_mm": outer_radius, "height_mm": joint_width,
            "base_mm": joint_base, "direction": joint_axis,
        }, [requirement_ref, f"industrial_design#joint-cover-envelope-{index + 1}"]))
        commands.append(_command(f"{joint_id}-cover-outer", "feature.fillet", {
            "base": f"{joint_id}-cover-blank",
            "radius_mm": min(joint_width * 0.20, outer_radius * 0.14),
        }, [requirement_ref, f"industrial_design#joint-cover-{index + 1}"]))
        commands.append(_command(f"{joint_id}-cover-cavity", "part.cylinder", {
            "radius_mm": outer_radius - molding_wall,
            "height_mm": joint_width - 2.0 * molding_wall,
            "base_mm": _point_shift(joint_base, joint_axis, molding_wall),
            "direction": joint_axis,
        }, [requirement_ref, f"manufacturing#joint-cover-cavity-{index + 1}"]))
        commands.append(_command(f"{joint_id}-cover-sleeve", "feature.cut", {
            "base": f"{joint_id}-cover-outer",
            "tool": f"{joint_id}-cover-cavity",
        }, [requirement_ref, f"manufacturing#joint-cover-wall-{index + 1}"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-cover", "role": "joint_cover",
            "default_visible": True, "fastener_exposure": "none",
            "retention": spec["industrial_design"]["cosmetic_retention"],
        })
        link_direction = segment["neutral_direction"]
        length = segment["length"]["value"]
        profile_stations = link_structure["profile_stations_mm"]
        cosmetic_profile_radii = link_structure["cosmetic_outer_radii_mm"]
        # Follow the actual tapered link envelope instead of punching a
        # circumscribed cylindrical hole through the whole collar.  The latter
        # was collision-free but left visually oversized crescent openings.
        _append_conical_profile(
            commands,
            f"{joint_id}-cover-outgoing-port",
            profile_stations,
            [radius + channel_clearance for radius in cosmetic_profile_radii],
            position,
            link_direction,
            [requirement_ref,
             f"manufacturing#joint-cover-outgoing-link-port-{index + 1}"],
        )
        outgoing_cover_id = (
            f"{joint_id}-cover" if index == 0 else f"{joint_id}-cover-outgoing-open")
        commands.append(_command(outgoing_cover_id, "feature.cut", {
            "base": f"{joint_id}-cover-sleeve",
            "tool": f"{joint_id}-cover-outgoing-port",
        }, [requirement_ref, f"manufacturing#joint-cover-outgoing-opening-{index + 1}"]))
        if index > 0:
            previous_segment = segments[index - 1]
            previous_structure = link_structures[index - 1]
            incoming_direction = previous_segment["neutral_direction"]
            incoming_link_length = previous_segment["length"]["value"]
            _append_conical_profile(
                commands,
                f"{joint_id}-cover-incoming-port",
                previous_structure["profile_stations_mm"],
                [radius + channel_clearance for radius in
                 previous_structure["cosmetic_outer_radii_mm"]],
                _point_shift(position, incoming_direction, -incoming_link_length),
                incoming_direction,
                [requirement_ref,
                 f"manufacturing#joint-cover-incoming-link-port-{index + 1}"],
            )
            commands.append(_command(f"{joint_id}-cover", "feature.cut", {
                "base": f"{joint_id}-cover-outgoing-open",
                "tool": f"{joint_id}-cover-incoming-port",
            }, [requirement_ref,
                f"manufacturing#joint-cover-incoming-opening-{index + 1}"]))
        # A cross-bore must span the whole finite hub even when the link direction
        # has a component along the joint axis.  The old 2*hub-radius tool was too
        # short for J5: its oblique harness escaped through an end face after the
        # Boolean tool had already stopped.  A centered tool longer than the hub's
        # circumscribed sphere is conservative for every direction and keeps this
        # construction independent of pose-specific trigonometric edge cases.
        hub_channel_half_length = (
            math.hypot(hub_core_radius, hub_width / 2.0) + channel_clearance)
        commands.append(_command(f"{joint_id}-hub-blank", "part.cylinder", {
            "radius_mm": hub_core_radius, "height_mm": hub_width,
            "base_mm": _point_shift(position, joint_axis, -hub_width / 2.0),
            "direction": joint_axis,
        }, [requirement_ref, f"kinematics#{segment['joint_id']}"]))
        commands.append(_command(f"{joint_id}-hub-harness-channel", "part.cylinder", {
            "radius_mm": channel_radius,
            "height_mm": 2.0 * hub_channel_half_length,
            "base_mm": _point_shift(
                position, link_direction, -hub_channel_half_length),
            "direction": link_direction,
        }, [requirement_ref, f"electrical#joint-harness-channel-{index + 1}"]))
        commands.append(_command(f"{joint_id}-hub", "feature.cut", {
            "base": f"{joint_id}-hub-blank",
            "tool": f"{joint_id}-hub-harness-channel",
        }, [requirement_ref, f"manufacturing#joint-harness-bore-{index + 1}"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-hub", "role": "internal_structure",
            "default_visible": False, "material": segment["structural_material"],
        })
        if outer_radius <= hub_core_radius + molding_wall:
            raise ProductCompileError([
                f"{segment['joint_id']} hub structure plus shell wall exceeds joint envelope"
            ])
        _append_conical_profile(
            commands,
            f"{joint_id}-link-shell-outer",
            profile_stations,
            cosmetic_profile_radii,
            position,
            link_direction,
            [requirement_ref, f"industrial_design#continuous-necked-link-{index + 1}"],
        )
        _append_conical_profile(
            commands,
            f"{joint_id}-link-shell-cavity",
            profile_stations,
            [radius - molding_wall for radius in cosmetic_profile_radii],
            position,
            link_direction,
            [requirement_ref, f"manufacturing#link-shell-cavity-{index + 1}"],
        )
        commands.append(_command(f"{joint_id}-link-shell", "feature.cut", {
            "base": f"{joint_id}-link-shell-outer",
            "tool": f"{joint_id}-link-shell-cavity",
        }, [requirement_ref, f"manufacturing#link-shell-wall-{index + 1}"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-link-shell", "role": "cosmetic_shell",
            "default_visible": True, "fastener_exposure": "none",
            "service_seam_zone": spec["industrial_design"]["service_seam_zones"][
                index % len(spec["industrial_design"]["service_seam_zones"])],
        })
        basis_x, basis_y, basis_z = _plane_basis(joint_axis)
        outgoing_axis_projection = sum(
            link_direction[component] * basis_z[component]
            for component in range(3))
        electronics_bay_side = -1.0 if outgoing_axis_projection >= 0.0 else 1.0
        joint_packaging.append({
            "joint_id": segment["joint_id"],
            "origin_mm": list(position),
            "joint_width_mm": joint_width,
            "hub_width_mm": hub_width,
            "basis_x": basis_x,
            "basis_y": basis_y,
            "basis_z": basis_z,
            "electronics_bay_side": int(electronics_bay_side),
            "bay_side_basis": "opposite_outgoing_harness_axis_projection",
        })
        worst_wall = link_structure["minimum_realized_wall_mm"]
        if worst_wall < minimum_structural_wall:
            raise ProductCompileError([
                f"{segment['joint_id']} harness channel wall {worst_wall:.3f} mm "
                f"is below minimum structural wall {minimum_structural_wall:.3f} mm"
            ])
        harness_channel_checks.append({
            "joint_id": segment["joint_id"],
            "nominal_harness_radius_mm": round(harness_radius, 4),
            "nominal_channel_radius_mm": round(channel_radius, 4),
            "worst_case_remaining_wall_mm": round(worst_wall, 4),
            "minimum_structural_wall_mm": round(minimum_structural_wall, 4),
            "status": "pass",
        })
        material = spec["materials"][segment["structural_material"]]
        material_properties = material["screening_properties"]
        screening_index = min(
            range(len(link_structure["worst_outer_radii_mm"])),
            key=link_structure["worst_outer_radii_mm"].__getitem__,
        )
        screening_outer_radius_mm = link_structure["worst_outer_radii_mm"][screening_index]
        screening_inner_radius_mm = link_structure["worst_inner_radii_mm"][screening_index]
        worst_outer_diameter_mm = 2.0 * screening_outer_radius_mm
        worst_inner_diameter_mm = 2.0 * screening_inner_radius_mm
        outer_m = worst_outer_diameter_mm / 1000.0
        inner_m = worst_inner_diameter_mm / 1000.0
        area_m2 = math.pi / 4.0 * (outer_m ** 2 - inner_m ** 2)
        second_moment_m4 = math.pi / 64.0 * (outer_m ** 4 - inner_m ** 4)
        polar_moment_m4 = 2.0 * second_moment_m4
        axis_alignment = min(1.0, abs(sum(
            joint_axis[component] * link_direction[component]
            for component in range(3))))
        root_moment_nm = joint_reports[index]["required_output_torque_nm"]
        bending_moment_nm = root_moment_nm * math.sqrt(max(0.0, 1.0 - axis_alignment ** 2))
        torsional_moment_nm = root_moment_nm * axis_alignment
        outer_radius_m = outer_m / 2.0
        bending_stress_pa = bending_moment_nm * outer_radius_m / second_moment_m4
        torsional_stress_pa = torsional_moment_nm * outer_radius_m / polar_moment_m4
        von_mises_pa = math.sqrt(
            bending_stress_pa ** 2 + 3.0 * torsional_stress_pa ** 2)
        yield_strength_pa = material_properties["minimum_yield_strength"]["value"] * 1.0e6
        yield_margin = yield_strength_pa / max(von_mises_pa, 1.0e-12)
        elastic_modulus_pa = material_properties["elastic_modulus"]["value"] * 1.0e9
        poisson_ratio = material_properties["poisson_ratio"]["value"]
        shear_modulus_pa = elastic_modulus_pa / (2.0 * (1.0 + poisson_ratio))
        worst_length_m = (
            segment["length"]["value"] + segment["length"]["tolerance"]["plus"]) / 1000.0
        end_rotation_rad = bending_moment_nm * worst_length_m / (
            elastic_modulus_pa * second_moment_m4)
        tip_deflection_m = bending_moment_nm * worst_length_m ** 2 / (
            2.0 * elastic_modulus_pa * second_moment_m4)
        torsional_twist_rad = torsional_moment_nm * worst_length_m / (
            shear_modulus_pa * polar_moment_m4)
        estimated_tube_mass_kg = system_structural_screening[
            "estimated_structural_tube_masses_kg"][index]
        structural_link_screens.append({
            "joint_id": segment["joint_id"],
            "material_id": segment["structural_material"],
            "model": "annular_uniform_beam_with_joint_axis_end_moment_decomposition",
            "scope": "first_order_linear_elastic_link_tube_only",
            "worst_case_outer_diameter_mm": round(worst_outer_diameter_mm, 6),
            "worst_case_inner_diameter_mm": round(worst_inner_diameter_mm, 6),
            "worst_case_length_mm": round(worst_length_m * 1000.0, 6),
            "area_m2": round(area_m2, 12),
            "second_moment_m4": round(second_moment_m4, 15),
            "polar_moment_m4": round(polar_moment_m4, 15),
            "load_factored_root_moment_nm": round(root_moment_nm, 6),
            "bending_moment_nm": round(bending_moment_nm, 6),
            "torsional_moment_nm": round(torsional_moment_nm, 6),
            "bending_stress_mpa": round(bending_stress_pa / 1.0e6, 6),
            "torsional_shear_stress_mpa": round(torsional_stress_pa / 1.0e6, 6),
            "von_mises_stress_mpa": round(von_mises_pa / 1.0e6, 6),
            "screening_yield_strength_mpa": material_properties[
                "minimum_yield_strength"]["value"],
            "yield_margin": round(yield_margin, 6),
            "yield_screen_status": "pass" if yield_margin >= 1.0 else "fail",
            "end_rotation_deg": round(math.degrees(end_rotation_rad), 6),
            "tip_deflection_mm": round(tip_deflection_m * 1000.0, 6),
            "torsional_twist_deg": round(math.degrees(torsional_twist_rad), 6),
            "estimated_tube_mass_kg": round(estimated_tube_mass_kg, 6),
            "stiffness_acceptance_status": "covered_by_system_model",
        })
        _append_conical_profile(
            commands,
            f"{joint_id}-structural-core-blank",
            profile_stations,
            link_structure["structural_outer_radii_mm"],
            position,
            link_direction,
            [requirement_ref,
             f"manufacturing#necked-structural-exoskeleton-{index + 1}"],
        )
        _append_conical_profile(
            commands,
            f"{joint_id}-structural-core-channel",
            profile_stations,
            link_structure["structural_inner_radii_mm"],
            position,
            link_direction,
            [requirement_ref, f"manufacturing#structural-shell-cavity-{index + 1}"],
        )
        commands.append(_command(f"{joint_id}-structural-core", "feature.cut", {
            "base": f"{joint_id}-structural-core-blank",
            "tool": f"{joint_id}-structural-core-channel",
        }, [requirement_ref, f"manufacturing#hollow-structural-core-{index + 1}"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-structural-core", "role": "internal_structure",
            "default_visible": False, "material": segment["structural_material"],
        })
        commands.append(_command(f"{joint_id}-harness", "part.cylinder", {
            "radius_mm": harness_radius, "height_mm": length,
            "base_mm": position, "direction": link_direction,
        }, [requirement_ref, f"electrical#internal-harness-{index + 1}"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-harness", "role": "harness",
            "default_visible": False,
            "route": "straight_centerline_static_packaging_envelope_only",
            "motion_qualification_status": "incomplete",
            "structural_channel_clearance_mm": channel_clearance,
        })
        commands.append(_command(f"{joint_id}-internal-fastener", "part.cylinder", {
            "radius_mm": max(2.0, hub_core_radius * 0.10),
            "height_mm": hub_core_radius * 1.2,
            "base_mm": _point_shift(position, joint_axis, -hub_core_radius * 0.6),
            "direction": joint_axis,
        }, [requirement_ref, "industrial_design#internal-structural-retention"]))
        semantic_parts.append({
            "command_id": f"{joint_id}-internal-fastener", "role": "internal_fastener",
            "default_visible": False, "fastener_exposure": "internal",
            "retention": spec["industrial_design"]["structural_retention"],
        })
        position = _point_add(position, link_direction, length)

    final_direction = segments[-1]["neutral_direction"]
    flange_radius = segments[-1]["outer_diameter"]["value"] * 0.31
    flange_depth = 18.0
    commands.append(_command("tool-flange", "part.cylinder", {
        "radius_mm": flange_radius, "height_mm": flange_depth,
        "base_mm": _point_shift(position, final_direction, -flange_depth * 0.25),
        "direction": final_direction,
    }, [requirement_ref, "industrial_design#defined-tool-interface"]))
    semantic_parts.append({
        "command_id": "tool-flange", "role": "tool_flange",
        "default_visible": True, "fastener_exposure": "none",
        "interface": "ISO_9409_style_pattern_pending_exact_selection",
    })

    commands.append(_command("pcb-control-01", "part.box", {
        "length_mm": coordinator_width, "width_mm": coordinator_height,
        "height_mm": board_thickness,
        "origin_mm": [-coordinator_width / 2.0, -coordinator_height / 2.0, -48.0],
    }, [requirement_ref, "electronics#base-coordinator-envelope"]))
    semantic_parts.append({
        "command_id": "pcb-control-01", "role": "pcb", "default_visible": False,
        "board_id": "PCB-CONTROL-01", "fidelity": "physical_envelope_not_routed_board",
    })
    for index, packaging in enumerate(joint_packaging, 1):
        hub_edge_offset = packaging["hub_width_mm"] / 2.0 + channel_clearance
        bay_side = packaging["electronics_bay_side"]
        board_centers = {
            "logic": bay_side * (hub_edge_offset + board_thickness / 2.0),
            "power": bay_side * (
                hub_edge_offset + board_thickness + board_stack_gap
                + board_thickness / 2.0),
        }
        for board_role, center_offset in board_centers.items():
            board_center = _point_shift(
                packaging["origin_mm"], packaging["basis_z"], center_offset)
            board_origin = list(board_center)
            board_origin = _point_shift(
                board_origin, packaging["basis_x"], -joint_board_width / 2.0)
            board_origin = _point_shift(
                board_origin, packaging["basis_y"], -joint_board_height / 2.0)
            board_origin = _point_shift(
                board_origin, packaging["basis_z"], -board_thickness / 2.0)
            command_id = f"pcb-joint-{index:02d}-{board_role}"
            board_id = f"PCB-JOINT-{index:02d}-{board_role.upper()}"
            commands.append(_command(command_id, "part.box", {
                "length_mm": joint_board_width, "width_mm": joint_board_height,
                "height_mm": board_thickness, "origin_mm": board_origin,
                "basis_x": packaging["basis_x"],
                "basis_y": packaging["basis_y"],
                "basis_z": packaging["basis_z"],
            }, [requirement_ref,
                f"electronics#joint-controller-{board_role}-envelope-{index}"]))
            semantic_parts.append({
                "command_id": command_id, "role": "pcb",
                "default_visible": False, "board_id": board_id,
                "board_function": f"joint_{board_role}",
                "fidelity": "physical_envelope_not_placed_or_routed_board",
            })

    failed_structural_screens = [
        item for item in structural_link_screens if item["yield_screen_status"] != "pass"]
    if failed_structural_screens:
        raise ProductCompileError([
            f"{item['joint_id']} first-order link yield margin "
            f"{item['yield_margin']:.3f} is below 1.0"
            for item in failed_structural_screens
        ])

    cad_program = {
        "schema": "design-studio.mechanical-cad-program/1",
        "program_id": f"{product_id}-r{revision_text}",
        "units": "mm",
        "author": "DesignStudio deterministic cross-domain compiler",
        "commands": commands,
        "checks": [
            {"kind": "valid_shape", "target": part["command_id"]}
            for part in semantic_parts
        ],
    }
    mechanical = {
        "schema": "design-studio.robot-mechanical-definition/1",
        "product_id": product_id,
        "axis_count": 7,
        "neutral_pose_joint_origins_mm": [],
        "semantic_parts": semantic_parts,
        "hidden_fastener_policy": {
            "externally_visible_fastener_count": 0,
            "structural_load_path": "internal_preloaded_fasteners_and_machined_hubs",
            "cosmetic_covers_are_structural": False,
            "service_access": "remove_keyed_joint_collars_after_energy_isolation",
        },
        "joint_loads": joint_reports,
        "harness_channel_checks": harness_channel_checks,
        "joint_packaging": joint_packaging,
        "analysis_plan": [
            "nonlinear_bearing_and_gear-contact_model",
            "link_and-joint_static_fea_for_every_defined_load_case",
            "modal_analysis_across_representative_robot_poses",
            "fatigue_and-brake-hold-evaluation",
            "collision_self-collision_and-cable-sweep-analysis",
        ],
    }
    material_contract = {
        "schema": "design-studio.material-screening-contract/1",
        "product_id": product_id,
        "requirements_sha256": source_digest,
        "materials": list(spec["materials"].values()),
        "all_source_documents_digest_pinned": all(
            source["document_sha256"] is not None
            for material in spec["materials"].values()
            for source in material["sources"]),
        "qualification_status": "incomplete",
        "qualification_reason": (
            "screening properties are not lot-specific certified design allowables and "
            "the 7075 source documents are not content-digest pinned"),
        "required_next_evidence": [
            "selected_supplier_and_exact_product_form",
            "procurement_specification_and_heat_lot_certificate",
            "direction_and_thickness_dependent_allowables",
            "fatigue_fracture_and_environmental_property_basis",
        ],
    }
    structural_screening = {
        "schema": "design-studio.structural-link-screening/2",
        "product_id": product_id,
        "requirements_sha256": source_digest,
        "load_basis": "load_factored_joint_moments_from_mechanical_definition",
        "dimension_basis": "minimum_outer_diameter_maximum_channel_and_maximum_length",
        "material_contract": "mechanical/material-screening-contract.json",
        "method": {
            "section": "closed_form_annular_section",
            "stress": "linear_bending_plus_torsion_von_mises",
            "deformation": "uniform_cantilever_under_end_moment",
            "moment_decomposition": "joint_axis_projected_onto_neutral_link_centerline",
        },
        "links": structural_link_screens,
        "minimum_yield_margin": round(
            min(item["yield_margin"] for item in structural_link_screens), 6),
        "maximum_single_link_tip_deflection_mm": round(
            max(item["tip_deflection_mm"] for item in structural_link_screens), 6),
        "maximum_single_link_torsional_twist_deg": round(
            max(item["torsional_twist_deg"] for item in structural_link_screens), 6),
        "yield_screen_result": "pass",
        "system_screening_reference": "mechanical/robot-system-structural-screening.json",
        "stiffness_acceptance_result": "pass_via_system_screening",
        "qualification_status": "screening_only",
        "excluded_physics": [
            "joint_bearing_and_gear_compliance",
            "hub_and_fastener_contact",
            "pose_dependent_assembly_load_paths",
            "local_stress_concentrations_and_buckling",
            "fatigue_fracture_and_residual_stress",
            "nonlinear_material_and_contact_behavior",
        ],
    }
    position = [0.0, 0.0, 0.0]
    for segment in segments:
        mechanical["neutral_pose_joint_origins_mm"].append({
            "joint_id": segment["joint_id"], "origin_mm": [round(item, 6) for item in position],
            "axis": segment["axis"],
        })
        position = _point_add(position, segment["neutral_direction"], segment["length"]["value"])

    board_width = joint_board_width
    board_height = joint_board_height
    shell_wall = molding_wall
    service_clearance = spec["manufacturing"]["service_clearance"]["value"]
    board_diagonal = math.hypot(board_width, board_height)
    board_fit_reports = []
    for segment, packaging in zip(segments, joint_packaging, strict=True):
        usable_diameter = segment["outer_diameter"]["value"] - 2.0 * (
            shell_wall + service_clearance)
        fit_margin = usable_diameter - board_diagonal
        if fit_margin < 0.0:
            raise ProductCompileError([
                f"{segment['joint_id']} joint PCB diagonal {board_diagonal:.3f} mm exceeds "
                f"usable radial envelope {usable_diameter:.3f} mm"
            ])
        electronics_bay_depth = (
            (packaging["joint_width_mm"] - packaging["hub_width_mm"]) / 2.0
            - 2.0 * service_clearance)
        stack_margin = electronics_bay_depth - board_stack_depth
        if stack_margin < 0.0:
            raise ProductCompileError([
                f"{segment['joint_id']} joint PCB stack depth {board_stack_depth:.3f} mm "
                f"exceeds dedicated axial electronics bay {electronics_bay_depth:.3f} mm"
            ])
        board_fit_reports.append({
            "joint_id": segment["joint_id"],
            "board_diagonal_mm": round(board_diagonal, 4),
            "usable_radial_envelope_mm": round(usable_diameter, 4),
            "packaging_margin_mm": round(fit_margin, 4),
            "stack_depth_mm": round(board_stack_depth, 4),
            "dedicated_axial_bay_depth_mm": round(electronics_bay_depth, 4),
            "stack_depth_margin_mm": round(stack_margin, 4),
            "hub_width_mm": round(packaging["hub_width_mm"], 4),
            "joint_cover_width_mm": round(packaging["joint_width_mm"], 4),
            "hub_overlap_by_construction": False,
        })
    boards: list[dict[str, Any]] = []
    coordinator = {
        "schema": "design-studio.pcb-design-definition/1",
        "board_id": "PCB-CONTROL-01", "role": "base_coordinator",
        "envelope_mm": [spec["electronics"]["coordinator_board_width"]["value"],
                        spec["electronics"]["coordinator_board_height"]["value"],
                        board_thickness],
        "stackup": {"layers": 6, "outer_copper_oz": 2, "controlled_impedance": True},
        "functional_blocks": [
            "dual_channel_safety_io", "dual_isolated_can_fd", "industrial_ethernet",
            "48v_input_protection", "isolated_auxiliary_power", "brake_energy_management",
        ],
        "net_classes": ["48V_POWER", "SAFETY_A", "SAFETY_B", "CANFD_A", "CANFD_B", "CONTROL"],
        "placement_zones": {
            "high_energy": "connector_edge_and_bonded_base_heatsink",
            "isolated_safety": "opposite_board_edge_with_creepage_corridor",
            "communication": "shielded_connector_edge",
        },
        "section_access": "base underside service panel; no exterior screw head",
        "fabrication_scope": "architecture_floorplan_and_constraints",
    }
    boards.append(coordinator)
    component_ids_by_board = {
        board_role: [
            record["binding_id"] for record in component_plan["bindings"]
            if record["applies_to_board"] == board_role
        ]
        for board_role in ("joint_logic", "joint_power")
    }
    for index, report in enumerate(joint_reports):
        module_id = f"JCM-{index + 1:02d}"
        logic_board_id = f"PCB-JOINT-{index + 1:02d}-LOGIC"
        power_board_id = f"PCB-JOINT-{index + 1:02d}-POWER"
        common = {
            "schema": "design-studio.pcb-design-definition/1",
            "joint_id": report["joint_id"],
            "joint_controller_module_id": module_id,
            "envelope_mm": [board_width, board_height, board_thickness],
            "stackup": {"layers": 6, "outer_copper_oz": 2, "controlled_impedance": True},
            "radial_packaging_check": board_fit_reports[index],
            "section_access": (
                "joint collar removal exposes the dedicated axial electronics bay "
                "after energy isolation"),
            "fabrication_scope": "evidence_bound_floorplan_and_constraints_not_routed_fabrication_data",
        }
        boards.append({
            **common,
            "board_id": logic_board_id, "role": "joint_servo_logic",
            "electrical_requirements": {
                "bus_voltage_v": bus_voltage,
                "control_frequency_hz": spec["electronics"]["control_frequency"]["value"],
                "can_fd_channels": spec["electronics"]["can_fd_channels"],
                "safety_channels": spec["electronics"]["safety_channels"],
                "galvanic_isolation_location": "base_system_boundary",
            },
            "functional_blocks": [
                "motor_control_mcu", "absolute_encoder_interface", "winding_temperature_input",
                "dual_can_fd_pass_through", "dual_safety_input_and_diagnostics",
            ],
            "component_binding_ids": component_ids_by_board["joint_logic"],
            "net_classes": ["ENCODER", "CANFD_A", "CANFD_B", "SAFETY_A", "SAFETY_B",
                            "CONTROL", "5V", "3V3"],
            "placement_zones": {
                "encoder_analog": "shielded_quiet_zone_away_from_switch_nodes",
                "communication": "two separated transceiver channels adjacent to harness connector",
                "safety": "two independently routed input and diagnostic paths",
                "mezzanine": "short controlled return interconnect to power board",
            },
            "placement_estimate": component_plan["placement_estimates"]["joint_logic"],
            "stack_mate": power_board_id,
        })
        boards.append({
            **common,
            "board_id": power_board_id, "role": "joint_servo_power",
            "electrical_requirements": {
                "bus_voltage_v": bus_voltage,
                "bus_transient_ceiling_v": spec["electrical"]["bus_transient_ceiling"]["value"],
                "phase_current_a": report["required_phase_current_a"],
                "control_frequency_hz": spec["electronics"]["control_frequency"]["value"],
            },
            "functional_blocks": [
                "three_phase_gate_drive", "six_mosfet_power_stage",
                "three_phase_current_sense", "brake_driver", "logic_supply_pre_regulator",
            ],
            "component_binding_ids": component_ids_by_board["joint_power"],
            "net_classes": ["48V_POWER", "PHASE_UVW", "GATE_DRIVE", "CURRENT_SENSE",
                            "BRAKE", "5V", "POWER_GROUND"],
            "placement_zones": {
                "power_stage": "bonded_to_internal_aluminum_hub_heat_spreader",
                "current_sense": "kelvin_routed_between_low_side_devices_and_star_point",
                "switch_nodes": "minimum_area_with_no_logic_routes_or planes_below",
                "mezzanine": "quiet_edge_opposite_phase_and_bus terminals",
            },
            "placement_estimate": component_plan["placement_estimates"]["joint_power"],
            "stack_mate": logic_board_id,
        })

    electronics_architecture = {
        "schema": "design-studio.robot-electronics-architecture/1",
        "product_id": product_id,
        "board_count": len(boards),
        "boards": [{"board_id": board["board_id"], "role": board["role"]} for board in boards],
        "topology": "distributed_servo_control_with_base_coordinator",
        "communications": {
            "primary": "CAN-FD-A", "redundant_diagnostics": "CAN-FD-B",
            "channel_count": spec["electronics"]["can_fd_channels"],
            "joint_transceiver_isolation": "non_isolated_inside_common_robot_power_domain",
            "system_boundary_isolation": {
                "location": "base_coordinator_external_network_boundary",
                "required_withstand_voltage_v": spec["electronics"]
                ["system_boundary_isolation_voltage"]["value"],
            },
        },
        "safety_partition": {
            "channels": spec["electronics"]["safety_channels"],
            "functions": spec["safety"]["functions"],
            "independence_rule": "separate inputs_routes_power_domains_and_diagnostics",
        },
        "required_next_artifacts": [
            "manufacturer_pdf_content_digests", "verified_symbols_land_patterns_and_3d_models",
            "native_schematics_materialized_from_joint_net_contract",
            "closure_of_all_unresolved_exact_bindings", "placed_and_routed_native_pcb",
            "signal_integrity_receipts", "power_integrity_receipts", "thermal_coupling_receipts",
            "independent_functional_safety_analysis_and_hardware_diagnostics",
        ],
    }

    harness = {
        "schema": "design-studio.robot-harness-definition/2",
        "topology": "daisy_chain_power_with_dual_communication_and_safety_channels",
        "segments": harness_segments,
        "joint_branches": [{
            "joint_id": report["joint_id"],
            "peak_current_a": report["peak_bus_current_a"],
            "conductor_resistance_ohm_per_m": branch_r,
            "ampacity_a": branch_ampacity,
            "connector_location": "inside_joint_collar",
            "connector_retention": "positive_internal_latch",
        } for report in joint_reports],
        "end_of_chain_voltage_drop_v": round(cumulative_drop, 5),
        "end_of_chain_voltage_drop_percent": round(worst_drop_pct, 4),
        "maximum_allowed_voltage_drop_percent": spec["electrical"]["max_voltage_drop_percent"]["value"],
        "external_wiring_visible_in_normal_operation": False,
        "selected_cable_part_number": None,
        "motion_qualification_status": "incomplete",
        "motion_contract": "electrical/harness-motion-contract.json",
        "verification": ["bend_radius_sweep", "torsion_life_test", "hipot", "continuity",
                         "shield_bonding", "temperature_rise"],
    }

    try:
        electrical_integrity = build_robot_electrical_integrity_contract(
            spec,
            harness,
            joint_reports,
            joint_net_contract,
            requirements_sha256=source_digest,
        )
    except ElectricalIntegrityError as error:
        raise ProductCompileError([str(error)]) from error

    thermal_budget = {
        "schema": "design-studio.robot-thermal-budget/2",
        "maximum_ambient_degc": ambient,
        "maximum_motor_winding_degc": winding_limit,
        "maximum_driver_junction_degc": driver_junction_limit,
        "motor_winding_analysis_status": "incomplete_motor_loss_map_and_thermal_network_missing",
        "coupled_thermal_contract": "thermal/coupled-thermal-contract.json",
        "power_stage_loss_screen": {
            "mosfet_rds_on_25c_max_ohm": mosfet_rds_on_25c_ohm,
            "assumed_hot_resistance_factor": mosfet_hot_resistance_factor,
            "gate_drive_screening_voltage_v": gate_drive_screening_voltage_v,
            "control_frequency_hz": spec["electronics"]["control_frequency"]["value"],
            "phase_shunt_resistance_ohm": shunt_resistance_ohm,
            "unresolved_terms": [
                "switching_transition_energy",
                "dead_time_body_diode_and_reverse_recovery",
                "gate_driver_and_integrated_buck_quiescent_loss",
                "pcb_copper_connector_and_harness_branch_loss",
            ],
        },
        "joint_budgets": [{
            "joint_id": report["joint_id"],
            "efficiency_based_continuous_loss_w": report[
                "efficiency_based_continuous_loss_w"],
            "component_screen_continuous_loss_w": report[
                "component_screen_continuous_loss_w"],
            "continuous_power_stage_loss_budget_w": report[
                "continuous_power_stage_loss_budget_w"],
            "loss_budget_basis": report["loss_budget_basis"],
            "hot_two_mosfet_path_loss_w": report["hot_two_mosfet_path_loss_w"],
            "three_shunt_peak_loss_w": report["three_shunt_peak_loss_w"],
            "six_mosfet_gate_charge_power_w": report[
                "six_mosfet_gate_charge_power_w"],
            "maximum_power_stage_to_ambient_thermal_resistance_degc_per_w": report[
                "maximum_power_stage_to_ambient_thermal_resistance_degc_per_w"],
            "selected_thermal_resistance_degc_per_w": report["selected_thermal_resistance_degc_per_w"],
            "heat_path": "power_stage_to_aluminum_hub_to_joint_shell_thermal_spreader",
            "temperature_sensitive_zone": "encoder_and_safety_io_on_opposite_board_edge",
        } for report in joint_reports],
        "reference_influence": "section-view packaging and high-temperature housing observations",
        "required_next_artifacts": [
            "switching_loss_model_with_measured_gate_waveforms",
            "3d_conjugate_heat_transfer_model", "motor_loss_map_and_winding_thermal_network",
            "instrumented_duty_cycle_test"],
    }

    industrial_design = {
        "schema": "design-studio.industrial-design-definition/1",
        "form_language": spec["industrial_design"]["form_language"],
        "colors": spec["industrial_design"]["exterior_colors"],
        "reference_use": "abstracted_design_principles_only",
        "copied_reference_geometry": False,
        "exterior_fasteners": [],
        "externally_visible_fastener_count": 0,
        "structural_retention": spec["industrial_design"]["structural_retention"],
        "cosmetic_retention": spec["industrial_design"]["cosmetic_retention"],
        "service_seam_zones": spec["industrial_design"]["service_seam_zones"],
        "default_view": "exterior",
        "view_modes": {
            "exterior": {"visible_roles": ["cosmetic_shell", "joint_cover", "tool_flange"],
                         "hidden_roles": ["internal_structure", "internal_fastener", "pcb", "harness",
                                          "keepout", "thermal_volume"]},
            "service": {"visible_roles": ["internal_structure", "internal_fastener", "pcb", "harness"],
                        "transparent_roles": ["cosmetic_shell", "joint_cover"],
                        "hidden_roles": ["keepout"]},
            "electronics_service": {"visible_roles": ["internal_fastener", "pcb", "harness"],
                                    "hidden_roles": ["cosmetic_shell", "joint_cover",
                                                     "internal_structure", "keepout"]},
            "structure": {"visible_roles": ["internal_structure", "harness"],
                          "hidden_roles": ["cosmetic_shell", "joint_cover", "pcb", "keepout"]},
            "analysis": {"visible_roles": ["internal_structure", "load_case", "thermal_volume"],
                         "hidden_roles": ["cosmetic_shell", "joint_cover", "keepout"]},
        },
    }

    manufacturing = {
        "schema": "design-studio.robot-manufacturing-definition/1",
        "structural_process": spec["manufacturing"]["structural_process"],
        "shell_process": spec["manufacturing"]["shell_process"],
        "shell_wall_mm": spec["manufacturing"]["shell_wall"]["value"],
        "rib_thickness_mm": round(
            spec["manufacturing"]["shell_wall"]["value"]
            * spec["manufacturing"]["rib_to_wall_ratio"]["value"], 4),
        "minimum_draft_deg": spec["manufacturing"]["minimum_draft"]["value"],
        "minimum_root_fillet_mm": spec["manufacturing"]["minimum_root_fillet"]["value"],
        "minimum_structural_wall_mm": minimum_structural_wall,
        "harness_channel_clearance_mm": channel_clearance,
        "service_clearance_mm": spec["manufacturing"]["service_clearance"]["value"],
        "link_endpoint_radius_ratio": (
            spec["manufacturing"]["link_endpoint_radius_ratio"]["value"]),
        "link_body_radius_ratio": (
            spec["manufacturing"]["link_body_radius_ratio"]["value"]),
        "link_transition_length_mm": (
            spec["manufacturing"]["link_transition_length"]["value"]),
        "structural_shell_clearance_mm": (
            spec["manufacturing"]["structural_shell_clearance"]["value"]),
        "target_structural_shell_wall_mm": (
            spec["manufacturing"]["target_structural_shell_wall"]["value"]),
        "assembly_sequence": [
            "install_and_preload_internal_joint_hubs",
            "install_necked_hollow_structural_exoskeleton_links",
            "route_and_retain_harness_through_structural_cores",
            "install_joint_pcbs_and_bond_power_stages_to_heat_spreaders",
            "verify_encoder_alignment_brake_and_electrical_isolation",
            "install_keyed_joint_collars_and_cosmetic_link_shells",
            "verify_zero_visible_fastener_heads_in_normal_views",
        ],
        "required_next_artifacts": ["moldflow_and_warpage", "GD&T_drawings", "process_FMEA",
                                    "assembly_fixture_definition"],
    }

    evidence_set = {
        "schema": EVIDENCE_SCHEMA,
        "measurement_policy": "visual_media_cannot_define_dimensions",
        "geometry_copy_policy": "distinct_geometry_required",
        "sources": spec["visual_evidence"],
        "influenced_requirements": [
            "smooth segmented collaborative-robot form language",
            "compact modular joint collars and hidden service seams",
            "section views for enclosure-to-component packaging",
            "dedicated heat paths for dense power electronics",
            "housing segmentation aligned to assembly thermal and service boundaries",
            "separate material manufacturing load model and consequence uncertainty factors",
            "stateful graph workflow with branching merging and targeted repair loops",
        ],
    }

    engineering_workflow = {
        "schema": "design-studio.engineering-workflow-graph/1",
        "workflow_id": f"{product_id}-engineering-graph-r{revision_text}",
        "state": {
            "identity_fields": ["product_id", "revision", "requirements_sha256"],
            "evidence_fields": ["evidence_digests", "rights_constraints", "observations"],
            "engineering_fields": ["changed_domains", "domain_artifact_digests",
                                   "interface_findings", "deterministic_failures",
                                   "qualification_findings", "bundle_digest_verified"],
            "accounting_fields": ["iteration", "inference_usage_records"],
        },
        "nodes": [
            {"id": "evidence_intake", "owner": "deterministic_software",
             "writes": ["evidence_digests", "rights_constraints", "observations"]},
            {"id": "requirements_normalization", "owner": "deterministic_software",
             "writes": ["requirements_sha256", "changed_domains"]},
            {"id": "domain_synthesis", "owner": "ai_proposal_plus_domain_code",
             "parallel_domains": ["mechanical", "electrical", "electronics", "thermal",
                                  "manufacturing", "industrial_design", "safety"],
             "writes": ["domain_artifact_digests", "inference_usage_records"]},
            {"id": "deterministic_validation", "owner": "deterministic_software",
             "writes": ["deterministic_failures"]},
            {"id": "cross_domain_review", "owner": "ai_critique_plus_engineering_rules",
             "writes": ["interface_findings"]},
            {"id": "repair_scoping", "owner": "deterministic_software",
             "writes": ["changed_domains", "iteration"]},
            {"id": "targeted_repair", "owner": "affected_domain_code",
             "writes": ["domain_artifact_digests", "inference_usage_records"]},
            {"id": "physics_qualification", "owner": "verified_solver_and_test_workers",
             "writes": ["qualification_findings"]},
            {"id": "atomic_publication", "owner": "deterministic_software",
             "writes": ["bundle_digest_verified"]},
            {"id": "finish", "owner": "deterministic_software", "writes": []},
        ],
        "edges": [
            {"from": "evidence_intake", "to": "requirements_normalization", "when": "evidence_valid"},
            {"from": "requirements_normalization", "to": "domain_synthesis", "when": "requirements_valid"},
            {"from": "domain_synthesis", "to": "deterministic_validation", "when": "domain_candidates_written"},
            {"from": "deterministic_validation", "to": "cross_domain_review", "when": "no_deterministic_failures"},
            {"from": "deterministic_validation", "to": "repair_scoping", "when": "deterministic_failure_present"},
            {"from": "cross_domain_review", "to": "physics_qualification", "when": "no_interface_findings"},
            {"from": "cross_domain_review", "to": "repair_scoping", "when": "interface_finding_present"},
            {"from": "repair_scoping", "to": "targeted_repair", "when": "repair_scope_written"},
            {"from": "targeted_repair", "to": "deterministic_validation", "when": "domain_candidates_written"},
            {"from": "physics_qualification", "to": "repair_scoping", "when": "qualification_finding_present"},
            {"from": "physics_qualification", "to": "atomic_publication", "when": "required_qualification_satisfied"},
            {"from": "atomic_publication", "to": "finish", "when": "bundle_digest_verified"},
        ],
        "controls": {
            "ai_may": ["propose", "compare", "critique", "classify_findings"],
            "ai_may_not": ["invent_dimensions", "suppress_failed_checks", "publish_partial_assets"],
            "publication_preconditions": ["validated_typed_state", "no_deterministic_failures",
                                           "required_qualification_satisfied",
                                           "all_artifact_digests_verified"],
            "repair_scope": "only_failed_or_interface-affected_domain_subgraphs",
            "inference_budget_policy": "observe_only_no_hard_cap",
        },
        "reference_influence": "graph nodes edges shared state branching merging and repair cycles",
    }

    measured_usage = [record for record in spec["ai_usage_records"] if record.get("status") == "measured"]
    usage_summary = {
        "schema": "design-studio.product-inference-usage/1",
        "budget_policy": "observe_only_no_hard_cap",
        "records": spec["ai_usage_records"],
        "measured_record_count": len(measured_usage),
        "input_tokens": sum(int(record.get("input_tokens", 0)) for record in measured_usage),
        "output_tokens": sum(int(record.get("output_tokens", 0)) for record in measured_usage),
        "total_tokens": sum(int(record.get("total_tokens", 0)) for record in measured_usage),
        "cached_input_tokens": sum(int(record.get("cached_input_tokens", 0)) for record in measured_usage),
        "optimization": [
            "reuse_digest-bound_system_and_manufacturing_context",
            "send_only_changed_domain_subgraphs_for_revision_work",
            "use_deterministic_compilers_and_solvers_for_numeric_artifacts",
            "reserve_multimodal_inference_for_form_semantics_and_anomaly_review",
        ],
    }
    if not measured_usage:
        usage_summary["measurement_status"] = "no_provider_usage_records_supplied"

    evaluation = {
        "schema": "design-studio.cross-domain-engineering-evaluation/1",
        "result": "computed_requirements_satisfied_analysis_still_required",
        "computed_checks": [
            {"id": "seven-axis-chain", "status": "pass", "value": 7},
            {"id": "selected-joint-torque", "status": "pass",
             "minimum_margin": round(min(report["torque_margin"] for report in joint_reports), 4)},
            {"id": "harness-ampacity", "status": "pass",
             "minimum_margin": round(min(item["ampacity_margin"] for item in harness_segments), 4)},
            {"id": "harness-voltage-drop", "status": "pass", "value_percent": round(worst_drop_pct, 4)},
            {"id": "harness-motion-qualification", "status": "incomplete",
             "provisionally_incompatible_radial_loop_joints": harness_motion[
                 "provisionally_incompatible_radial_loop_joints"],
             "reason": (
                 "cable_rating_joint_travel_motion_strategy_swept_route_and_cycle_test_"
                 "evidence_are_missing")},
            {"id": "power-stage-first-order-rth-screen", "status": "pass",
             "minimum_junction_temperature_margin_degc": min(
                 item["power_stage_junction_margin_degc"]
                 for item in coupled_thermal["joints"])},
            {"id": "coupled-joint-thermal-network", "status": "incomplete",
             "reason": coupled_thermal["coupled_network_result"]},
            {"id": "joint-pcb-radial-packaging", "status": "pass",
             "minimum_margin_mm": round(min(item["packaging_margin_mm"]
                                             for item in board_fit_reports), 4)},
            {"id": "joint-pcb-stack-depth", "status": "pass",
             "minimum_margin_mm": round(min(item["stack_depth_margin_mm"]
                                             for item in board_fit_reports), 4)},
            {"id": "joint-electronics-component-capability", "status": "pass",
             "mcu_package_accessible_fdcan_instances": component_plan["capability_receipts"]
             ["mcu_package_accessible_fdcan_instances"],
             "can_fd_transceiver_count": component_plan["capability_receipts"]
             ["can_fd_transceiver_count"]},
            {"id": "joint-electronics-voltage-derating", "status": "pass",
             "required_rating_v": component_plan["capability_receipts"]
             ["required_semiconductor_voltage_rating_v"],
             "mosfet_rating_v": component_plan["capability_receipts"]["mosfet_vds_max_v"],
             "gate_driver_rating_v": component_plan["capability_receipts"]
             ["gate_driver_abs_max_v"]},
            {"id": "joint-electronics-first-order-placement", "status": "pass",
             "maximum_utilization": max(
                 item["estimated_utilization"]
                 for item in component_plan["placement_estimates"].values())},
            {"id": "joint-electronics-exact-active-pin-topology", "status": "pass",
             "exact_component_count": len(joint_net_contract["exact_components"]),
             "pin_disposition_count": sum(
                 len(item["pins"]) for item in joint_net_contract["exact_components"])},
            {"id": "can-fd-geometric-timing", "status": "pass",
             "minimum_margin": min(
                 item["geometric_timing_margin_fraction"]
                 for item in electrical_integrity["signal_integrity"]["channels"])},
            {"id": "can-fd-full-channel-si", "status": "incomplete",
             "reason": electrical_integrity["signal_integrity"]["result"]},
            {"id": "dynamic-power-integrity", "status": "incomplete",
             "reason": electrical_integrity["power_integrity"]["dynamic_result"]},
            {"id": "system-emc", "status": "incomplete",
             "reason": electrical_integrity["emc"]["result"]},
            {"id": "functional-safety-allocation", "status": "requirements_allocated",
             "target_performance_level": functional_safety[
                 "target_performance_level"],
             "compliance_claimed": functional_safety["compliance_claimed"]},
            {"id": "functional-safety-implementation", "status": "incomplete",
             "reason": functional_safety["hardware_architecture_result"]},
            {"id": "tool-repeatability-budget", "status": "allocated_evidence_incomplete",
             "target_mm": accuracy_controls["target_tool_repeatability_mm"],
             "maximum_common_joint_rms_angle_arcsec": accuracy_controls["joints"][0][
                 "maximum_total_joint_rms_angle_arcsec"]},
            {"id": "control-to-structural-mode-separation", "status": "pass_screening_only",
             "ratio": accuracy_controls["controls"]["structural_mode_separation_ratio"],
             "minimum_required_ratio": accuracy_controls["controls"][
                 "minimum_required_structural_mode_separation_ratio"]},
            {"id": "first-order-link-yield-screen", "status": "pass",
             "minimum_margin": structural_screening["minimum_yield_margin"],
             "qualification_status": "screening_only"},
            {"id": "system-structural-stiffness-acceptance", "status": "pass",
             "neutral_pose_factored_tool_deflection_mm": system_structural_screening[
                 "static_tool_deflection"]["magnitude_mm"],
             "maximum_allowed_mm": system_structural_screening[
                 "static_tool_deflection"]["maximum_allowed_mm"],
             "margin": system_structural_screening["static_tool_deflection"]["margin"],
             "qualification_status": "screening_only"},
            {"id": "system-structural-frequency-screen", "status": "pass",
             "minimum_estimate_hz": system_structural_screening[
                 "frequency_screen"]["minimum_estimate_hz"],
             "minimum_required_hz": system_structural_screening[
                 "frequency_screen"]["minimum_required_hz"],
             "margin": system_structural_screening["frequency_screen"]["margin"],
             "method_is_eigenanalysis": False},
            {"id": "power-loss-static-brake-sizing", "status": "requirements_computed",
             "maximum_required_motor_side_rating_nm": brake_sizing[
                 "maximum_required_motor_side_brake_rating_nm"],
             "component_selection_status": brake_sizing["component_selection_result"]},
            {"id": "fatigue-duty-and-life", "status": "incomplete",
             "target_motion_cycles": fatigue_contract["target_motion_cycles"],
             "reason": fatigue_contract["life_prediction_result"]},
            {"id": "visible-fastener-count", "status": "pass", "value": 0},
            {"id": "visual-source-dimensional-isolation", "status": "pass"},
            {"id": "explicit-uncertainty-budget", "status": "pass",
             "factors": uncertainty_budget, "combined_factor": round(load_factor, 6)},
            {"id": "safety-compliance-claim", "status": "pass", "claimed": False},
        ],
        "qualification_work": [
            "finite_element_stress_deflection_and_modal_evidence",
            "assembly_eigenanalysis_with_joint_bearing_and_reducer_compliance",
            "supplier_product_form_and_heat_lot_material_certificates",
            "qualified_fatigue_curves_local_notch_stress_and_endurance_test_evidence",
            "gearbox_bearing_and_brake_supplier_data",
            "bind_each_exact_brake_and_verify_hot_torque_response_time_and_wear_life",
            "manufacturer_pdf_content_digests_and_verified_component_land_patterns",
            "close_joint_net_contract_unresolved_bindings_and_materialize_native_schematics",
            "native_pcb_routing_and_manufacturing_outputs",
            "full_can_fd_channel_power_integrity_and_emc_models_and_test_receipts",
            "select_flex_rated_harness_and_define_every_joint_motion_strategy",
            "materialize_and_verify_pose_dependent_harness_sweeps",
            "physical_harness_bend_torsion_temperature_and_electrical_cycle_tests",
            "robot_risk_assessment_and_safety_function_validation",
            "functional_safety_fmeca_fmeda_pl_calculation_and_fault_injection_evidence",
            "thermal_simulation_and_instrumented_test",
            "tolerance_stack_and_repeatability_budget",
            "bind_encoder_reducer_bearing_and_control_plant_data_then_verify_repeatability",
            "prototype_assembly_serviceability_and_human-factors_trials",
        ],
    }

    return {
        "dimensions.json": dimension_set,
        "evidence/design-evidence.json": evidence_set,
        "mechanical/mechanical-cad-program.json": cad_program,
        "mechanical/mechanical-definition.json": mechanical,
        "mechanical/material-screening-contract.json": material_contract,
        "mechanical/structural-link-screening.json": structural_screening,
        "mechanical/robot-system-structural-screening.json": system_structural_screening,
        "mechanical/robot-brake-sizing.json": brake_sizing,
        "mechanical/robot-fatigue-contract.json": fatigue_contract,
        "electrical/harness.json": harness,
        "electrical/harness-motion-contract.json": harness_motion,
        "electronics/system-architecture.json": electronics_architecture,
        "electronics/component-evidence-bindings.json": component_plan,
        "electronics/joint-controller-net-contract.json": joint_net_contract,
        "electronics/electrical-integrity.json": electrical_integrity,
        "safety/functional-safety-contract.json": functional_safety,
        "controls/accuracy-and-controls.json": accuracy_controls,
        **{f"electronics/boards/{board['board_id']}.json": board for board in boards},
        "thermal/thermal-budget.json": thermal_budget,
        "thermal/coupled-thermal-contract.json": coupled_thermal,
        "industrial-design/exterior-and-view-policy.json": industrial_design,
        "manufacturing/manufacturing-definition.json": manufacturing,
        "workflow/engineering-graph.json": engineering_workflow,
        "ai/inference-usage.json": usage_summary,
        "verification/engineering-evaluation.json": evaluation,
    }


def validate_bundle(bundle_path: Path) -> dict[str, Any]:
    path = bundle_path.expanduser().resolve()
    manifest_path = path / "product-manifest.json" if path.is_dir() else path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != BUNDLE_SCHEMA:
        raise ProductCompileError(["product manifest schema is invalid"])
    root = manifest_path.parent
    issues: list[str] = []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        issues.append("product manifest has no artifacts")
        artifacts = []
    for record in artifacts:
        relative = Path(str(record.get("path", "")))
        resolved = (root / relative).resolve()
        if relative.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
            issues.append(f"artifact path is missing or escapes bundle: {relative}")
            continue
        if _sha256_file(resolved) != record.get("sha256"):
            issues.append(f"artifact digest mismatch: {relative}")
        if resolved.stat().st_size != record.get("bytes"):
            issues.append(f"artifact byte count mismatch: {relative}")
    if issues:
        raise ProductCompileError(issues)
    return manifest


def compile_product(requirements_path: Path, output_root: Path) -> Path:
    source_path = requirements_path.expanduser().resolve()
    if not source_path.is_file():
        raise ProductCompileError([f"requirements file does not exist: {source_path}"])
    source_bytes = source_path.read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    try:
        raw = json.loads(source_bytes)
    except json.JSONDecodeError as error:
        raise ProductCompileError([f"requirements JSON is invalid: {error}"]) from error
    spec = validate_product_requirements(raw, source_digest=source_digest)
    assets = _compile_assets(spec)

    bundle_digest = canonical_digest({
        "requirements_sha256": source_digest,
        "compiler_schema": BUNDLE_SCHEMA,
        "asset_digests": {path: canonical_digest(value) for path, value in sorted(assets.items())},
    })
    bundle_name = f"{spec['product_id']}-r{spec['revision']}-{bundle_digest[:12]}"
    root = output_root.expanduser().resolve()
    destination = root / bundle_name
    if destination.exists():
        manifest = validate_bundle(destination)
        if manifest.get("bundle_digest") != bundle_digest:
            raise ProductCompileError(["existing deterministic bundle has an unexpected digest"])
        return destination / "product-manifest.json"

    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{bundle_name}.stage-", dir=root))
    try:
        for relative, value in sorted(assets.items()):
            _write_json(stage / relative, value)
        artifact_records = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            artifact_records.append({
                "path": str(path.relative_to(stage)),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "media_type": "application/json",
            })
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "product_id": spec["product_id"],
            "revision": spec["revision"],
            "requirements_sha256": source_digest,
            "bundle_digest": bundle_digest,
            "generation_policy": "validated_inputs_atomic_publication",
            "scope": "cross_domain_design_definition_and_computed_requirements",
            "artifacts": artifact_records,
        }
        _write_json(stage / "product-manifest.json", manifest)
        validate_bundle(stage)
        os.replace(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return destination / "product-manifest.json"


__all__ = [
    "BUNDLE_SCHEMA",
    "EVIDENCE_SCHEMA",
    "ProductCompileError",
    "SPEC_SCHEMA",
    "compile_product",
    "validate_bundle",
    "validate_product_requirements",
]
