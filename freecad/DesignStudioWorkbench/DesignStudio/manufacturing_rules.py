"""Deterministic manufacturing profiles and design-for-manufacture checks.

This module intentionally contains policy and explainability, not geometry
kernel code.  Exact measurements are performed by the host geometry backend;
this layer selects the profile, computes permitted ranges, and produces stable
findings that can be shown to a designer or consumed by an AI proposal agent.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any


SCHEMA = "design-studio.manufacturing-profile/1"


class ManufacturingRuleError(ValueError):
    """A manufacturing profile or feature violates the typed contract."""


def _finite(value: Any, path: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ManufacturingRuleError(f"{path} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ManufacturingRuleError(f"{path} must be >= {minimum:g}")
    return result


def _positive(value: Any, path: str) -> float:
    return _finite(value, path, minimum=1.0e-9)


def _strict(value: Any, expected: set[str], path: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManufacturingRuleError(f"{path} fields differ from the contract")


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_profile(value: Any) -> dict[str, Any]:
    """Validate and normalize a revisionable manufacturing profile."""
    if not isinstance(value, dict):
        raise ManufacturingRuleError("manufacturing profile must be an object")
    required = {
        "schema", "profile_id", "process", "material", "wall", "rib",
        "draft", "fillet", "clearance", "boss", "provenance",
    }
    _strict(value, required, "profile")
    if value["schema"] != SCHEMA:
        raise ManufacturingRuleError(f"schema must be {SCHEMA}")
    if not isinstance(value["profile_id"], str) or not value["profile_id"]:
        raise ManufacturingRuleError("profile_id is required")
    if value["process"] not in {"injection_molding", "fdm", "sls", "cnc"}:
        raise ManufacturingRuleError("unsupported manufacturing process")
    if not isinstance(value["material"], str) or not value["material"]:
        raise ManufacturingRuleError("material is required")

    _strict(value["wall"], {"minimum_mm", "nominal_mm", "maximum_mm"}, "wall")
    wall_min = _positive(value["wall"]["minimum_mm"], "wall.minimum_mm")
    wall_nom = _positive(value["wall"]["nominal_mm"], "wall.nominal_mm")
    wall_max = _positive(value["wall"]["maximum_mm"], "wall.maximum_mm")
    if not wall_min <= wall_nom <= wall_max:
        raise ManufacturingRuleError("wall limits must be minimum <= nominal <= maximum")

    _strict(value["rib"], {"minimum_mm", "maximum_mm", "ratio_to_wall",
                             "height_to_thickness"}, "rib")
    rib_min = _positive(value["rib"]["minimum_mm"], "rib.minimum_mm")
    rib_max = _positive(value["rib"]["maximum_mm"], "rib.maximum_mm")
    rib_ratio = _finite(value["rib"]["ratio_to_wall"], "rib.ratio_to_wall")
    rib_height_ratio = _finite(value["rib"]["height_to_thickness"],
                               "rib.height_to_thickness")
    if rib_min > rib_max or not 0.0 < rib_ratio <= 1.0 or not rib_height_ratio > 0.0:
        raise ManufacturingRuleError("rib limits or ratios are invalid")

    _strict(value["draft"], {"minimum_deg", "preferred_deg"}, "draft")
    draft_min = _finite(value["draft"]["minimum_deg"], "draft.minimum_deg")
    draft_preferred = _finite(value["draft"]["preferred_deg"], "draft.preferred_deg")
    if draft_min < 0.0 or draft_preferred < draft_min or draft_preferred > 45.0:
        raise ManufacturingRuleError("draft limits are invalid")

    _strict(value["fillet"], {"minimum_radius_mm", "preferred_radius_mm"}, "fillet")
    fillet_min = _positive(value["fillet"]["minimum_radius_mm"],
                           "fillet.minimum_radius_mm")
    fillet_preferred = _positive(value["fillet"]["preferred_radius_mm"],
                                 "fillet.preferred_radius_mm")
    if fillet_preferred < fillet_min:
        raise ManufacturingRuleError("preferred fillet must be >= minimum fillet")

    _strict(value["clearance"], {"component_mm", "assembly_mm", "tool_access_mm"},
            "clearance")
    clearances = {key: _finite(value["clearance"][key], f"clearance.{key}", minimum=0.0)
                  for key in ("component_mm", "assembly_mm", "tool_access_mm")}

    _strict(value["boss"], {"minimum_wall_mm", "screw_diameter_mm",
                             "diameter_ratio", "height_ratio"}, "boss")
    boss_wall = _positive(value["boss"]["minimum_wall_mm"], "boss.minimum_wall_mm")
    screw_diameter = _positive(value["boss"]["screw_diameter_mm"],
                                "boss.screw_diameter_mm")
    diameter_ratio = _finite(value["boss"]["diameter_ratio"], "boss.diameter_ratio")
    height_ratio = _finite(value["boss"]["height_ratio"], "boss.height_ratio")
    if diameter_ratio <= 1.0 or height_ratio <= 0.0:
        raise ManufacturingRuleError("boss ratios are invalid")
    if not isinstance(value["provenance"], dict) or not value["provenance"].get("source"):
        raise ManufacturingRuleError("profile provenance.source is required")

    normalized = deepcopy(value)
    normalized["wall"] = {"minimum_mm": wall_min, "nominal_mm": wall_nom,
                            "maximum_mm": wall_max}
    normalized["rib"] = {"minimum_mm": rib_min, "maximum_mm": rib_max,
                           "ratio_to_wall": rib_ratio,
                           "height_to_thickness": rib_height_ratio}
    normalized["draft"] = {"minimum_deg": draft_min,
                             "preferred_deg": draft_preferred}
    normalized["fillet"] = {"minimum_radius_mm": fillet_min,
                              "preferred_radius_mm": fillet_preferred}
    normalized["clearance"] = clearances
    normalized["boss"] = {"minimum_wall_mm": boss_wall,
                            "screw_diameter_mm": screw_diameter,
                            "diameter_ratio": diameter_ratio,
                            "height_ratio": height_ratio}
    return normalized


def default_profile(process: str = "injection_molding",
                    material: str = "PC-ABS") -> dict[str, Any]:
    """Return a conservative profile for early camera-grip studies."""
    if process == "injection_molding":
        profile = {
            "schema": SCHEMA,
            "profile_id": "injection-molding-pc-abs-v1",
            "process": process,
            "material": material,
            "wall": {"minimum_mm": 1.6, "nominal_mm": 2.0, "maximum_mm": 3.2},
            "rib": {"minimum_mm": 0.8, "maximum_mm": 1.4,
                    "ratio_to_wall": 0.55, "height_to_thickness": 4.0},
            "draft": {"minimum_deg": 0.5, "preferred_deg": 1.0},
            "fillet": {"minimum_radius_mm": 0.4, "preferred_radius_mm": 0.8},
            "clearance": {"component_mm": 0.35, "assembly_mm": 0.5,
                          "tool_access_mm": 1.0},
            "boss": {"minimum_wall_mm": 1.2, "screw_diameter_mm": 2.5,
                     "diameter_ratio": 2.0, "height_ratio": 2.5},
            "provenance": {"source": "DesignStudio conservative default",
                           "revision": "1"},
        }
    else:
        profile = {
            "schema": SCHEMA,
            "profile_id": f"{process}-generic-v1",
            "process": process,
            "material": material,
            "wall": {"minimum_mm": 1.2, "nominal_mm": 2.0, "maximum_mm": 5.0},
            "rib": {"minimum_mm": 1.0, "maximum_mm": 2.5,
                    "ratio_to_wall": 0.65, "height_to_thickness": 5.0},
            "draft": {"minimum_deg": 0.0, "preferred_deg": 0.5},
            "fillet": {"minimum_radius_mm": 0.2, "preferred_radius_mm": 0.5},
            "clearance": {"component_mm": 0.25, "assembly_mm": 0.4,
                          "tool_access_mm": 0.8},
            "boss": {"minimum_wall_mm": 1.0, "screw_diameter_mm": 2.5,
                     "diameter_ratio": 1.8, "height_ratio": 2.5},
            "provenance": {"source": "DesignStudio generic default", "revision": "1"},
        }
    return validate_profile(profile)


def recommended_rib(profile: dict[str, Any], wall_mm: float | None = None) -> dict[str, float]:
    profile = validate_profile(profile)
    wall = profile["wall"]["nominal_mm"] if wall_mm is None else _positive(wall_mm, "wall_mm")
    rib = min(profile["rib"]["maximum_mm"],
              max(profile["rib"]["minimum_mm"], wall * profile["rib"]["ratio_to_wall"]))
    return {"wall_mm": wall, "thickness_mm": rib,
            "height_mm": rib * profile["rib"]["height_to_thickness"],
            "draft_deg": profile["draft"]["preferred_deg"],
            "root_fillet_mm": max(profile["fillet"]["minimum_radius_mm"],
                                    min(profile["fillet"]["preferred_radius_mm"], rib / 2.0))}


def check_feature(feature: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stable DFM findings; an empty list means no rule violation."""
    profile = validate_profile(profile)
    findings: list[dict[str, Any]] = []
    feature_id = str(feature.get("id", "feature"))
    kind = feature.get("kind")

    def finding(code: str, severity: str, actual: float, required: float, field: str):
        findings.append({"code": code, "severity": severity, "feature_id": feature_id,
                         "field": field, "actual_mm": actual,
                         "required_mm": required})

    if kind in {"wall", "shell"} and "thickness_mm" in feature:
        value = _finite(feature["thickness_mm"], f"{feature_id}.thickness_mm")
        if value < profile["wall"]["minimum_mm"]:
            finding("WALL_TOO_THIN", "error", value, profile["wall"]["minimum_mm"],
                    "thickness_mm")
    if kind == "rib" and "thickness_mm" in feature:
        value = _finite(feature["thickness_mm"], f"{feature_id}.thickness_mm")
        if value < profile["rib"]["minimum_mm"]:
            finding("RIB_TOO_THIN", "error", value, profile["rib"]["minimum_mm"],
                    "thickness_mm")
        if value > profile["rib"]["maximum_mm"]:
            finding("RIB_TOO_THICK", "warning", value, profile["rib"]["maximum_mm"],
                    "thickness_mm")
    if kind in {"rib", "boss", "wall", "shell"} and "draft_deg" in feature:
        value = _finite(feature["draft_deg"], f"{feature_id}.draft_deg")
        if value < profile["draft"]["minimum_deg"]:
            finding("DRAFT_TOO_LOW", "error", value, profile["draft"]["minimum_deg"],
                    "draft_deg")
    if kind in {"rib", "boss"} and "root_fillet_mm" in feature:
        value = _finite(feature["root_fillet_mm"], f"{feature_id}.root_fillet_mm")
        if value < profile["fillet"]["minimum_radius_mm"]:
            finding("ROOT_FILLET_TOO_SMALL", "error", value,
                    profile["fillet"]["minimum_radius_mm"], "root_fillet_mm")
    if "clearance_mm" in feature:
        value = _finite(feature["clearance_mm"], f"{feature_id}.clearance_mm")
        required = profile["clearance"]["component_mm"]
        if value < required:
            finding("CLEARANCE_TOO_SMALL", "error", value, required, "clearance_mm")
    return findings


__all__ = ["SCHEMA", "ManufacturingRuleError", "canonical_digest",
           "validate_profile", "default_profile", "recommended_rib", "check_feature"]
