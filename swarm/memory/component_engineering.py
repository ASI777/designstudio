"""Applicable component-level evidence gates used by design and publication."""
from __future__ import annotations

from typing import Any


_ACTIVE = {"regulator", "mcu", "transistor", "module", "power", "pmic", "converter"}
_POWER = {"regulator", "transistor", "power", "pmic", "converter"}


def _category(status: str, findings: list[dict], *, critical: bool = True) -> dict:
    return {"status": status, "critical": critical, "findings": findings}


def evaluate_component_engineering(record: dict[str, Any]) -> dict[str, dict]:
    """Evaluate evidence, not circuit operation; unknowns remain incomplete."""
    identity = record.get("component") or {}
    category = str(identity.get("category") or "other").lower()
    electrical = record.get("electrical") or {}
    evidence = record.get("datasheet_evidence", record.get("evidence") or {})
    assumptions = record.get("assumptions") or []
    exact = evidence.get("mpn_match") == "exact"

    evidence_findings = []
    if evidence and not exact:
        evidence_findings.append({"rule": "exact_mpn_datasheet_missing", "status": "incomplete",
                                  "message": "Exact-MPN datasheet evidence is not bound"})
    unverified = [item for item in assumptions if item.get("status") != "verified"]
    for item in unverified:
        evidence_findings.append({"rule": "unverified_assumption", "status": "incomplete",
                                  "domain": item.get("domain", "evidence"),
                                  "message": str(item.get("statement") or "Unspecified assumption")})
    evidence_status = "incomplete" if evidence_findings else \
        ("pass" if evidence else "not_applicable")

    reference_findings = []
    circuits = electrical.get("application_circuits") or []
    if exact and category in _ACTIVE:
        if not circuits:
            reference_findings.append({"rule": "manufacturer_reference_circuit_missing",
                                       "status": "incomplete", "message":
                                       "No manufacturer application circuit was captured"})
        for index, circuit in enumerate(circuits):
            if not circuit.get("datasheet_pages") or not circuit.get("evidence_summary"):
                reference_findings.append({"rule": "reference_circuit_provenance_missing",
                                           "status": "incomplete", "message":
                                           f"Application circuit {index + 1} lacks page/figure evidence"})
        reference_status = "incomplete" if reference_findings else "pass"
    else:
        reference_status = "not_applicable"

    thermal_findings = []
    if exact and category in _POWER:
        thermal = electrical.get("thermal") or {}
        theta = thermal.get("theta_ja_c_w")
        junction = thermal.get("tj_max_c")
        models = record.get("simulation_models") or {}
        behavioral = models.get("behavioral_power") or {}
        theta = theta if theta is not None else behavioral.get("theta_ja_c_per_w")
        junction = junction if junction is not None else behavioral.get("max_junction_c")
        if theta is None or junction is None:
            thermal_findings.append({"rule": "thermal_limits_missing", "status": "incomplete",
                                     "message": "Theta-JA and maximum junction temperature are required"})
        thermal_status = "incomplete" if thermal_findings else "pass"
    else:
        thermal_status = "not_applicable"

    safety_findings = []
    domains = electrical.get("power_domains") or []
    limits = [domain.get("vmax_v") for domain in domains if domain.get("vmax_v") is not None]
    supply = electrical.get("supply") or {}
    if supply.get("vmax_v") is not None:
        limits.append(supply["vmax_v"])
    if exact and category in _ACTIVE:
        if not limits:
            safety_findings.append({"rule": "supply_limit_missing", "status": "incomplete",
                                    "message": "No maximum operating supply is available for safety classification"})
        elif max(float(value) for value in limits) > 60:
            safety_findings.append({"rule": "hazardous_voltage_review_required", "status": "incomplete",
                                    "message": "Supply exceeds 60 V; insulation and spacing review is required"})
        safety_status = "incomplete" if safety_findings else "pass"
    else:
        safety_status = "not_applicable"

    routing_findings = []
    high_speed = electrical.get("high_speed") or {}
    routes = list(high_speed.get("diff_pairs") or []) + list(high_speed.get("single_ended") or [])
    for route in routes:
        if route.get("impedance_ohm") is None:
            routing_findings.append({"rule": "impedance_target_missing", "status": "incomplete",
                                     "message": "A high-speed signal lacks a controlled-impedance target"})
    routing_status = "incomplete" if routing_findings else ("pass" if routes else "not_applicable")
    return {
        "evidence": _category(evidence_status, evidence_findings),
        "reference_circuit": _category(reference_status, reference_findings),
        "thermal": _category(thermal_status, thermal_findings),
        "safety": _category(safety_status, safety_findings),
        "routing": _category(routing_status, routing_findings),
    }
