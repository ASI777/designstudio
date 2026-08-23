"""Authoritative engineering quantities for deterministic asset generation.

This module deliberately does not contain a manual release/approval concept.
An input either has enough immutable engineering provenance to be compiled, or
generation fails before an asset is written.  Raster images and model guesses
may describe form intent, but are never accepted as dimensional authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


SCHEMA = "design-studio.authoritative-dimension-set/1"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

# Keep the vocabulary intentionally small.  New units must be introduced with
# an explicit conversion/compatibility policy instead of silently accepting a
# misspelling that changes an engineering calculation.
SUPPORTED_UNITS = {
    "1", "mm", "m", "deg", "rad", "kg", "g", "N", "Nm", "Nmm",
    "Pa", "MPa", "GPa", "A", "V", "W", "Wh", "ohm", "Hz", "rpm", "s", "h", "cycle",
    "degC", "W_per_mK", "kg_per_m3", "rad_per_s", "rad_per_s2",
    "Nm_per_A", "ohm_per_m", "m_per_s", "m_per_s2", "W_per_m2K",
    "J_per_kgK", "degC_per_W", "bit_per_s",
}

AUTHORITATIVE_SOURCE_KINDS = {
    "engineering_requirement",
    "verified_drawing",
    "manufacturer_datasheet",
    "calibrated_measurement",
    "standard_constraint",
    "derived_calculation",
}

NON_AUTHORITATIVE_SOURCE_KINDS = {
    "image_inference",
    "render_measurement",
    "uncalibrated_measurement",
    "ai_estimate",
    "visual_guess",
}


class DimensionAuthorityError(ValueError):
    """Raised when engineering quantities cannot authoritatively generate assets."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def canonical_bytes(value: Any) -> bytes:
    """Return canonical JSON bytes, rejecting NaN and unstable float spellings."""

    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _finite_number(value: Any, path: str, issues: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(f"{path} must be a finite number")
        return None
    result = float(value)
    if not math.isfinite(result):
        issues.append(f"{path} must be a finite number")
        return None
    return result


def _validate_source(source: Any, path: str, known_ids: set[str],
                     issues: list[str]) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        issues.append(f"{path} must be an object")
        return None
    kind = source.get("kind")
    if kind in NON_AUTHORITATIVE_SOURCE_KINDS:
        issues.append(
            f"{path}.kind {kind!r} is visual/advisory evidence and cannot define a dimension")
        return None
    if kind not in AUTHORITATIVE_SOURCE_KINDS:
        issues.append(f"{path}.kind is unsupported")
        return None
    reference = source.get("reference")
    revision = source.get("revision")
    digest = source.get("sha256")
    if not isinstance(reference, str) or not reference.strip():
        issues.append(f"{path}.reference must identify the source clause or record")
    if not isinstance(revision, str) or not revision.strip():
        issues.append(f"{path}.revision must be explicit")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        issues.append(f"{path}.sha256 must bind the exact source bytes")

    if kind == "calibrated_measurement":
        calibration = source.get("calibration")
        if not isinstance(calibration, dict):
            issues.append(f"{path}.calibration is required for measured dimensions")
        else:
            if not isinstance(calibration.get("instrument_id"), str) \
                    or not calibration["instrument_id"].strip():
                issues.append(f"{path}.calibration.instrument_id is required")
            if not isinstance(calibration.get("certificate_sha256"), str) \
                    or not _DIGEST.fullmatch(calibration["certificate_sha256"]):
                issues.append(f"{path}.calibration.certificate_sha256 is invalid")
            if not isinstance(calibration.get("valid_at_measurement"), bool) \
                    or calibration.get("valid_at_measurement") is not True:
                issues.append(f"{path}.calibration must be valid at measurement time")

    if kind == "derived_calculation":
        inputs = source.get("input_quantity_ids")
        if not isinstance(inputs, list) or not inputs:
            issues.append(f"{path}.input_quantity_ids must identify calculation inputs")
        else:
            for index, quantity_id in enumerate(inputs):
                if quantity_id not in known_ids:
                    issues.append(
                        f"{path}.input_quantity_ids[{index}] must reference an earlier quantity")
        method = source.get("method")
        if not isinstance(method, str) or not method.strip():
            issues.append(f"{path}.method is required for derived dimensions")
    return dict(source)


def validate_dimension_set(document: Any) -> dict[str, Any]:
    """Validate and normalize a complete dimension set.

    Quantities are ordered so a derived calculation can reference only inputs
    already validated in the document.  This prevents hidden cycles and makes
    the resulting digest reproducible.
    """

    issues: list[str] = []
    if not isinstance(document, dict):
        raise DimensionAuthorityError(["dimension set must be an object"])
    expected = {"schema", "set_id", "units_policy", "quantities"}
    unknown = set(document) - expected
    missing = expected - set(document)
    if missing:
        issues.append(f"dimension set is missing fields {sorted(missing)}")
    if unknown:
        issues.append(f"dimension set has unknown fields {sorted(unknown)}")
    if document.get("schema") != SCHEMA:
        issues.append(f"schema must equal {SCHEMA!r}")
    set_id = document.get("set_id")
    if not isinstance(set_id, str) or not _ID.fullmatch(set_id):
        issues.append("set_id is invalid")
    if document.get("units_policy") != "explicit_no_implicit_conversion":
        issues.append("units_policy must forbid implicit conversion")
    quantities = document.get("quantities")
    if not isinstance(quantities, list) or not quantities:
        issues.append("quantities must be a non-empty array")
        quantities = []

    normalized: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for index, quantity in enumerate(quantities):
        path = f"quantities[{index}]"
        if not isinstance(quantity, dict):
            issues.append(f"{path} must be an object")
            continue
        required = {"id", "value", "unit", "tolerance", "criticality", "source"}
        if set(quantity) != required:
            issues.append(
                f"{path} fields differ; missing={sorted(required - set(quantity))}, "
                f"unknown={sorted(set(quantity) - required)}")
            continue
        quantity_id = quantity.get("id")
        if not isinstance(quantity_id, str) or not _ID.fullmatch(quantity_id):
            issues.append(f"{path}.id is invalid")
            continue
        if quantity_id in known_ids:
            issues.append(f"{path}.id duplicates {quantity_id!r}")
            continue
        value = _finite_number(quantity.get("value"), f"{path}.value", issues)
        unit = quantity.get("unit")
        if unit not in SUPPORTED_UNITS:
            issues.append(f"{path}.unit {unit!r} is unsupported")
        tolerance = quantity.get("tolerance")
        minus = plus = None
        if not isinstance(tolerance, dict) or set(tolerance) != {"minus", "plus"}:
            issues.append(f"{path}.tolerance must contain exactly minus and plus")
        else:
            minus = _finite_number(tolerance.get("minus"), f"{path}.tolerance.minus", issues)
            plus = _finite_number(tolerance.get("plus"), f"{path}.tolerance.plus", issues)
            if minus is not None and minus < 0:
                issues.append(f"{path}.tolerance.minus must be non-negative")
            if plus is not None and plus < 0:
                issues.append(f"{path}.tolerance.plus must be non-negative")
        criticality = quantity.get("criticality")
        if criticality not in {"interface", "safety", "functional", "manufacturing", "cosmetic"}:
            issues.append(f"{path}.criticality is unsupported")
        source = _validate_source(quantity.get("source"), f"{path}.source", known_ids, issues)
        if value is not None and minus is not None and value - minus < 0 \
                and unit not in {"degC", "deg", "rad"}:
            issues.append(f"{path} tolerance permits a physically negative value")

        normalized.append({
            "id": quantity_id,
            "value": value,
            "unit": unit,
            "tolerance": {"minus": minus, "plus": plus},
            "criticality": criticality,
            "source": source,
        })
        known_ids.add(quantity_id)

    if issues:
        raise DimensionAuthorityError(issues)
    result = {
        "schema": SCHEMA,
        "set_id": set_id,
        "units_policy": "explicit_no_implicit_conversion",
        "quantities": normalized,
    }
    result["dimension_set_digest"] = canonical_digest(result)
    return result


def quantity_map(document: Any) -> dict[str, dict[str, Any]]:
    normalized = validate_dimension_set(document)
    return {item["id"]: item for item in normalized["quantities"]}


__all__ = [
    "AUTHORITATIVE_SOURCE_KINDS",
    "DimensionAuthorityError",
    "NON_AUTHORITATIVE_SOURCE_KINDS",
    "SCHEMA",
    "SUPPORTED_UNITS",
    "canonical_bytes",
    "canonical_digest",
    "quantity_map",
    "validate_dimension_set",
]
