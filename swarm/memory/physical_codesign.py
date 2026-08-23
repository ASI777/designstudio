"""Deterministic component/PCB/mechanical co-design contracts.

This module is deliberately a proposal layer.  It never mutates a PCB or a
FreeCAD document and it never promotes an AI estimate to engineering evidence.
Every approval is bound to the canonical digest of the reviewed proposal.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMAS = {
    "design-studio.component-selection-plan/1": "component-selection-plan-v1.schema.json",
    "design-studio.component-candidate-set/1": "component-candidate-set-v1.schema.json",
    "design-studio.component-decision/1": "component-decision-v1.schema.json",
    "design-studio.pcb-topology-study/1": "pcb-topology-study-v1.schema.json",
    "design-studio.pcb-island-plan/1": "pcb-island-plan-v1.schema.json",
    "design-studio.mechanical-component-requirements/1": "mechanical-component-requirements-v1.schema.json",
}

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_HARD_GATES = (
    "electrical_compatibility",
    "package_identity",
    "mechanical_fit",
    "datasheet_identity",
)
_RELIABILITY_WEIGHTS = {
    "evidence_completeness": 5.0,
    "active_lifecycle": 4.0,
    "stock_coverage": 3.0,
    "source_diversity": 2.0,
    "cad_readiness": 3.0,
    "cost_preference": 1.0,
    "volume_preference": 1.0,
    "routing_accessibility": 2.0,
}


class CoDesignError(ValueError):
    """A fail-closed contract or evidence violation."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest_record(value: dict, digest_field: str) -> str:
    payload = copy.deepcopy(value)
    payload.pop(digest_field, None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _finish(value: dict, digest_field: str) -> dict:
    value[digest_field] = digest_record(value, digest_field)
    return value


def verify_digest(value: dict, digest_field: str) -> None:
    actual = str(value.get(digest_field) or "")
    if not _DIGEST.fullmatch(actual) or actual != digest_record(value, digest_field):
        raise CoDesignError(f"stale or malformed {digest_field}")


def _schema_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    candidates = (root / "docs" / "schemas",
                  root.parent.parent / "schemas",
                  Path("/usr/local/share/DesignStudio/schemas"))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise CoDesignError("DesignStudio schema directory is unavailable")


def validate_document(value: dict) -> None:
    schema_id = value.get("schema")
    filename = SCHEMAS.get(schema_id)
    if not filename:
        raise CoDesignError(f"unsupported co-design schema: {schema_id!r}")
    try:
        import jsonschema
    except ImportError as exc:
        raise CoDesignError("jsonschema is required for co-design validation") from exc
    schema = json.loads((_schema_root() / filename).read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema,
        format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        where = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise CoDesignError(f"{schema_id} {where}: {error.message}")


def assess_component_physical_assets(component: dict) -> dict:
    """Report exact-MPN footprint/STEP readiness without inventing geometry.

    This is intentionally separate from extraction: incomplete evidence remains
    reviewable, but cannot satisfy the CAD-readiness hard gate.
    """
    footprint = component.get("footprint") or {}
    package = component.get("package_3d") or {}
    identity = component.get("component") or {}
    checks: dict[str, dict] = {}

    def check(name: str, passed: bool, reason: str, *, failed: bool = False) -> None:
        checks[name] = {"status": "pass" if passed else "fail" if failed else "incomplete",
                        "reason": reason}

    evidence = component.get("evidence") or {}
    exact = evidence.get("mpn_match") == "exact" and bool(evidence.get("sha256"))
    check("exact_datasheet", exact,
          "datasheet digest and exact requested-MPN match are required",
          failed=evidence.get("mpn_match") == "mismatch")
    pads = footprint.get("pads") or []
    bga = footprint.get("bga") or {}
    check("land_pattern", bool(pads or bga),
          "concrete pad or BGA land geometry is required")
    recommended = footprint.get("recommended_land_pattern") or {}
    check("manufacturer_land_pattern", recommended.get("status") == "verified",
          "manufacturer land-pattern evidence or a verified controlling package standard is required")
    body = footprint.get("body") or {}
    dimensions_ok = all(isinstance(body.get(name), (int, float)) and body[name] > 0
                        for name in ("length_mm", "width_mm", "height_mm"))
    check("package_dimensions", dimensions_ok,
          "verified length, width and height are required")
    check("pin_one", bool(footprint.get("pin_one_orientation")),
          "pin-one pad, direction and provenance are required")
    outlines = footprint.get("outlines") or {}
    check("manufacturing_layers", all(outlines.get(name)
                                      for name in ("silkscreen", "fabrication", "assembly")),
          "silkscreen, fabrication and assembly outlines are required")
    check("mask_paste", (footprint.get("solder_mask_expansion_mm") is not None and
                         (footprint.get("mount") == "through_hole" or
                          footprint.get("paste_reduction_mm") is not None)),
          "verified solder-mask and applicable paste rules are required")
    step = package.get("step_asset") or {}
    check("step_brep", (step.get("format") == "step" and step.get("roundtrip_valid") is True and
                        isinstance(step.get("solid_count"), int) and step.get("solid_count") > 0 and
                        bool(_DIGEST.fullmatch(str(step.get("sha256") or "")))),
          "a digest-bound, solid STEP model with successful round-trip is required")
    is_connector = str(identity.get("category") or "").lower() == "connector"
    interfaces = package.get("mechanical_interfaces") or []
    check("connector_service_geometry", (not is_connector or
          {item.get("kind") for item in interfaces} >= {"mating", "service"}),
          "connectors require mating and service clearance volumes")
    statuses = {item["status"] for item in checks.values()}
    status = "rejected" if "fail" in statuses else "verified" if statuses == {"pass"} else "incomplete"
    result = {"status": status, "checks": checks,
              "geometry_authority": "verified_datasheet_dimensions_and_freecad_brep",
              "raster_geometry_authority": False}
    result["asset_digest"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _identifier(value: Any, field: str) -> str:
    text = str(value or "")
    if not _ID.fullmatch(text):
        raise CoDesignError(f"{field} is not a stable identifier")
    return text


def create_selection_plan(*, plan_id: str, product_id: str, target_quantity: int,
                          market: str, requirements: list[dict]) -> dict:
    if target_quantity < 1:
        raise CoDesignError("target_quantity must be positive")
    plan = {
        "schema": "design-studio.component-selection-plan/1",
        "plan_id": _identifier(plan_id, "plan_id"),
        "product_id": _identifier(product_id, "product_id"),
        "target_quantity": int(target_quantity),
        "market": str(market or "").strip(),
        "ranking_policy": "reliability_first_verified_metrics_only",
        "requirements": copy.deepcopy(requirements),
        "created_utc": _utc_now(),
    }
    _finish(plan, "plan_digest")
    validate_document(plan)
    return plan


def _gate(raw: Any, name: str) -> dict:
    if isinstance(raw, str):
        raw = {"status": raw, "reason": name.replace("_", " ")}
    if not isinstance(raw, dict):
        raw = {"status": "incomplete", "reason": f"{name} was not evaluated"}
    status = str(raw.get("status") or "incomplete")
    if status not in {"pass", "fail", "incomplete", "not_applicable"}:
        raise CoDesignError(f"invalid hard-gate status for {name}")
    result = {"status": status, "reason": str(raw.get("reason") or name)}
    evidence = raw.get("evidence_digest")
    if evidence is not None:
        if not _DIGEST.fullmatch(str(evidence)):
            raise CoDesignError(f"invalid evidence digest for {name}")
        result["evidence_digest"] = str(evidence)
    return result


def _metric(name: str, raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    status = str(raw.get("status") or "incomplete")
    if status not in {"pass", "incomplete", "not_applicable"}:
        raise CoDesignError(f"invalid metric status for {name}")
    value = raw.get("value") if status == "pass" else None
    if status == "pass":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise CoDesignError(f"trusted metric {name} needs a finite numeric value")
        if not 0.0 <= float(value) <= 1.0:
            raise CoDesignError(f"normalized metric {name} must be between zero and one")
    return {"status": status,
            "weight": float(raw.get("weight", _RELIABILITY_WEIGHTS.get(name, 1.0))),
            "value": float(value) if value is not None else None,
            **({"source": str(raw["source"])} if raw.get("source") else {})}


def rank_component_candidates(plan: dict, requirement_id: str,
                              raw_candidates: Iterable[dict], *,
                              candidate_set_id: str | None = None) -> dict:
    validate_document(plan)
    verify_digest(plan, "plan_digest")
    requirement_id = _identifier(requirement_id, "requirement_id")
    if requirement_id not in {item["requirement_id"] for item in plan["requirements"]}:
        raise CoDesignError("requirement does not belong to the selection plan")
    candidates = []
    for index, raw in enumerate(raw_candidates):
        gates = {name: _gate((raw.get("hard_gates") or {}).get(name), name)
                 for name in _REQUIRED_HARD_GATES}
        for name, value in (raw.get("hard_gates") or {}).items():
            if name not in gates:
                gates[str(name)] = _gate(value, str(name))
        statuses = {gate["status"] for gate in gates.values()}
        status = "rejected" if "fail" in statuses else "incomplete" if "incomplete" in statuses else "valid"
        metrics = {name: _metric(name, value)
                   for name, value in sorted((raw.get("metrics") or {}).items())}
        trusted = [item for item in metrics.values()
                   if item["status"] == "pass" and item["weight"] > 0]
        score = (sum(item["value"] * item["weight"] for item in trusted)
                 / sum(item["weight"] for item in trusted)) if trusted else None
        candidates.append({
            "candidate_id": _identifier(raw.get("candidate_id") or f"candidate-{index + 1}", "candidate_id"),
            "mpn": str(raw.get("mpn") or "").strip(),
            "manufacturer": str(raw.get("manufacturer") or "").strip(),
            "status": status,
            "hard_gates": gates,
            "metrics": metrics,
            "score": score if status == "valid" else None,
            "rank": None,
            "datasheet": copy.deepcopy(raw.get("datasheet") or {}),
            "assets": copy.deepcopy(raw.get("assets") or {}),
            "offers": copy.deepcopy(raw.get("offers") or []),
        })
    if not candidates:
        raise CoDesignError("at least one component candidate is required")
    ranked = sorted((item for item in candidates if item["status"] == "valid"),
                    key=lambda item: (-(item["score"] if item["score"] is not None else -1),
                                      item["mpn"], item["candidate_id"]))
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    result = {
        "schema": "design-studio.component-candidate-set/1",
        "candidate_set_id": _identifier(candidate_set_id or f"{plan['plan_id']}-{requirement_id}", "candidate_set_id"),
        "plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"],
        "requirement_id": requirement_id, "candidates": candidates,
        "ranking_policy": "reliability_first_verified_metrics_only", "created_utc": _utc_now(),
    }
    _finish(result, "candidate_set_digest")
    validate_document(result)
    return result


def approve_component(candidate_set: dict, selected_candidate_id: str, *,
                      approved_by: str, rationale: str, waivers: list[str] | None = None,
                      decision_id: str | None = None) -> dict:
    validate_document(candidate_set)
    verify_digest(candidate_set, "candidate_set_digest")
    selected = next((item for item in candidate_set["candidates"]
                     if item["candidate_id"] == selected_candidate_id), None)
    if selected is None or selected["status"] != "valid":
        raise CoDesignError("only a valid candidate can be approved")
    now = _utc_now()
    decision = {
        "schema": "design-studio.component-decision/1",
        "decision_id": _identifier(decision_id or f"decision-{candidate_set['candidate_set_id']}", "decision_id"),
        "candidate_set_id": candidate_set["candidate_set_id"],
        "candidate_set_digest": candidate_set["candidate_set_digest"],
        "selected_candidate_id": selected["candidate_id"], "selected_mpn": selected["mpn"],
        "approval": {"approved_by": str(approved_by).strip(), "approved_utc": now,
                     "rationale": str(rationale).strip(), "waivers": list(waivers or [])},
        "created_utc": now,
    }
    _finish(decision, "decision_digest")
    validate_document(decision)
    return decision


def _position(component: dict) -> tuple[float, float, float] | None:
    value = component.get("position_mm")
    if not isinstance(value, list) or len(value) != 3:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(item) for item in value):
        return None
    return tuple(float(item) for item in value)


def _component_groups(components: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for component in components:
        component_id = _identifier(component.get("component_id"), "component_id")
        group = str(component.get("functional_group") or "ungrouped").strip().lower()
        groups.setdefault(group, []).append(component_id)
    return {name: sorted(ids) for name, ids in sorted(groups.items())}


def _interconnects(nets: list[dict], owner: dict[str, str], kind: str) -> list[dict]:
    result = []
    for net in sorted(nets, key=lambda item: str(item.get("net_id") or "")):
        net_id = str(net.get("net_id") or "")
        endpoints = [str(value) for value in net.get("component_ids") or []]
        islands = sorted({owner[value] for value in endpoints if value in owner})
        if len(islands) > 1:
            slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", net_id).strip("-")[:96]
            if not slug:
                slug = hashlib.sha256(net_id.encode("utf-8")).hexdigest()[:16]
            result.append({"interconnect_id": f"link-{slug}", "kind": kind,
                           "net_id": net_id, "island_ids": islands,
                           "endpoint_component_ids": endpoints,
                           "evidence_status": "incomplete",
                           "connector_pin_map": [],
                           "maximum_length_mm": net.get("maximum_length_mm"),
                           "estimated_length_mm": net.get("estimated_length_mm"),
                           "minimum_bend_radius_mm": None,
                           "estimated_voltage_drop_v": None,
                           "evidence_digest": None})
    return result


def _wire_length(components_by_id: dict[str, dict], nets: list[dict]) -> float | None:
    total = 0.0
    for net in nets:
        positions = [_position(components_by_id[item]) for item in net.get("component_ids") or []
                     if item in components_by_id]
        if len(positions) < 2 or any(position is None for position in positions):
            return None
        anchor = positions[0]
        weight = float(net.get("criticality_weight", 1.0))
        total += sum(math.dist(anchor, position) * weight for position in positions[1:])
    return total


def _metric_record(status: str, value: float | None, unit: str, weight: float,
                   method: str) -> dict:
    return {"status": status, "weight": weight,
            "value": float(value) if status == "pass" and value is not None else None,
            "unit": unit, "method": method}


def score_routing(layout: dict) -> dict:
    """Measure routed copper without treating a ratsnest estimate as a route.

    Segments must be horizontal, vertical or exactly 45 degrees.  High-speed
    nets reject an orthogonal change of direction at a non-pad endpoint.
    """
    nets = {str(item.get("net_id")): item for item in layout.get("nets") or []}
    segments = list(layout.get("segments") or [])
    vias = list(layout.get("vias") or [])
    lengths: dict[str, float] = {name: 0.0 for name in nets}
    directions: dict[tuple[str, int, float, float], list[tuple[int, int]]] = {}
    failures = []
    terminals = list(layout.get("terminals") or [])
    def terminal_endpoint(net_id: str, x: float, y: float) -> bool:
        for terminal in terminals:
            if str(terminal.get("net_id")) != net_id:
                continue
            bounds = terminal.get("bounds_mm") or {}
            minimum, maximum = bounds.get("min"), bounds.get("max")
            if (isinstance(minimum, list) and isinstance(maximum, list)
                    and len(minimum) >= 2 and len(maximum) >= 2
                    and float(minimum[0]) - 1e-7 <= x <= float(maximum[0]) + 1e-7
                    and float(minimum[1]) - 1e-7 <= y <= float(maximum[1]) + 1e-7):
                return True
        return False
    for index, segment in enumerate(segments):
        net_id = str(segment.get("net_id") or "")
        if net_id not in nets:
            failures.append(f"segment {index} references unknown net {net_id}")
            continue
        try:
            ax, ay = float(segment["a_mm"][0]), float(segment["a_mm"][1])
            bx, by = float(segment["b_mm"][0]), float(segment["b_mm"][1])
            layer = int(segment.get("layer", 0))
        except (KeyError, TypeError, ValueError, IndexError):
            failures.append(f"segment {index} has invalid geometry")
            continue
        dx, dy = bx - ax, by - ay
        if not all(math.isfinite(value) for value in (ax, ay, bx, by)) or math.hypot(dx, dy) <= 1e-9:
            failures.append(f"segment {index} is degenerate")
            continue
        tolerance = max(1e-8, math.hypot(dx, dy) * 1e-8)
        if not (abs(dx) <= tolerance or abs(dy) <= tolerance
                or abs(abs(dx) - abs(dy)) <= tolerance
                or terminal_endpoint(net_id, ax, ay)
                or terminal_endpoint(net_id, bx, by)):
            failures.append(f"segment {index} is not horizontal, vertical or 45-degree")
        lengths[net_id] += math.hypot(dx, dy)
        def sign(value): return 0 if abs(value) <= tolerance else 1 if value > 0 else -1
        direction = (sign(dx), sign(dy))
        directions.setdefault((net_id, layer, ax, ay), []).append(direction)
        directions.setdefault((net_id, layer, bx, by), []).append((-direction[0], -direction[1]))
    right_angles: dict[str, int] = {name: 0 for name in nets}
    for (net_id, _layer, _x, _y), values in directions.items():
        if terminal_endpoint(net_id, _x, _y):
            continue
        for first in range(len(values)):
            for second in range(first + 1, len(values)):
                if values[first][0] * values[second][0] + values[first][1] * values[second][1] == 0:
                    right_angles[net_id] += 1
    for net_id, count in right_angles.items():
        if count and bool(nets[net_id].get("high_speed")):
            failures.append(f"high-speed net {net_id} contains {count} right-angle corner(s)")
    unrouted = sorted(net_id for net_id, net in nets.items()
                      if bool(net.get("required", True)) and lengths.get(net_id, 0.0) <= 0)
    if unrouted:
        failures.append("unrouted required nets: " + ", ".join(unrouted))
    weighted_length = sum(lengths[name] * float(nets[name].get("criticality_weight", 1.0))
                          for name in nets)
    return {"status": "fail" if failures else "pass",
            "actual_length_by_net_mm": lengths,
            "weighted_actual_length_mm": weighted_length,
            "via_count": len(vias), "right_angle_corners_by_net": right_angles,
            "unrouted_net_ids": unrouted, "failures": failures,
            "method": "measured copper segment geometry"}


def topology_source_from_project(project: dict, *,
                                 distributed_surfaces: bool = False,
                                 curvature_required: bool = False,
                                 moving_crossing: bool = False) -> dict:
    """Convert an existing dsproj into the topology-study neutral input.

    This adapter preserves pad-level membership and actual copper geometry.  It
    does not infer a physical split from visual clustering.
    """
    document_id = str(project.get("document_id") or "")
    product_id = re.sub(r"[^A-Za-z0-9._:-]", "-", document_id).strip("-")
    if not product_id or not _ID.fullmatch(product_id):
        product_id = "designstudio-product"
    footprints = list(project.get("footprints") or [])
    components = []
    net_members: dict[int, set[str]] = {}
    terminals = []
    for index, footprint in enumerate(footprints):
        component_id = str(footprint.get("ref") or f"component-{index + 1}")
        component_id = re.sub(r"[^A-Za-z0-9._:-]", "-", component_id)
        if not _ID.fullmatch(component_id):
            component_id = f"component-{index + 1}"
        placement = footprint.get("placement") or {}
        rotation = math.radians(float(footprint.get("rot_deg", 0) or 0))
        mirror = -1.0 if int(footprint.get("side", 0) or 0) == 1 else 1.0
        components.append({"component_id": component_id,
            "functional_group": str(placement.get("functional_group") or "ungrouped"),
            "position_mm": [float(footprint.get("x_mm", 0)),
                            float(footprint.get("y_mm", 0)),
                            float(footprint.get("h3d_mm", 0)) / 2.0],
            "bounds_mm": [float(footprint.get("body_w_mm", 0)),
                          float(footprint.get("body_h_mm", 0)),
                          float(footprint.get("h3d_mm", 0))],
            "power_w": float(placement.get("thermal_power_w", 0) or 0)})
        for pad in footprint.get("pads") or []:
            try:
                net_id = int(pad.get("net", -1))
            except (TypeError, ValueError):
                continue
            if net_id >= 0:
                net_members.setdefault(net_id, set()).add(component_id)
                local_x = float(pad.get("x_mm", 0))
                local_y = float(pad.get("y_mm", 0)) * mirror
                center_x = float(footprint.get("x_mm", 0)) + local_x * math.cos(rotation) - local_y * math.sin(rotation)
                center_y = float(footprint.get("y_mm", 0)) + local_x * math.sin(rotation) + local_y * math.cos(rotation)
                width = float(pad.get("w_mm", pad.get("width_mm", 0)) or 0)
                height = float(pad.get("h_mm", pad.get("height_mm", 0)) or 0)
                half_x = abs(math.cos(rotation)) * width / 2 + abs(math.sin(rotation)) * height / 2
                half_y = abs(math.sin(rotation)) * width / 2 + abs(math.cos(rotation)) * height / 2
                terminals.append({"net_id": str(net_id), "component_id": component_id,
                                  "bounds_mm": {"min": [center_x - half_x, center_y - half_y],
                                                "max": [center_x + half_x, center_y + half_y]}})
    classes = {int(item.get("id", -1)): item for item in project.get("net_classes") or []}
    nets = []
    for net in project.get("net_table") or []:
        net_id = int(net.get("id", -1))
        if net_id < 0:
            continue
        net_class = classes.get(int(net.get("class", -1)), {})
        frequency = float(net_class.get("signal_frequency_hz", 0) or 0)
        name = str(net.get("name") or f"net-{net_id}")
        high_speed = frequency >= 100_000_000 or any(
            token in name.upper() for token in ("USB", "DDR", "CLK", "DQS", "PCIE", "MIPI"))
        nets.append({"net_id": str(net_id), "name": name,
                     "component_ids": sorted(net_members.get(net_id, set())),
                     "criticality_weight": 4.0 if high_speed else
                         2.0 if float(net.get("required_current_a", 0) or 0) >= 1.0 else 1.0,
                     "high_speed": high_speed, "required": len(net_members.get(net_id, set())) >= 2})
    routing = {"nets": [{"net_id": item["net_id"], "high_speed": item["high_speed"],
                          "criticality_weight": item["criticality_weight"],
                          "required": item["required"]} for item in nets],
               "segments": [{"net_id": str(trace.get("net")), "layer": int(trace.get("layer", 0)),
                              "a_mm": [float(trace.get("ax_mm", 0)), float(trace.get("ay_mm", 0))],
                              "b_mm": [float(trace.get("bx_mm", 0)), float(trace.get("by_mm", 0))]}
                             for trace in project.get("traces") or [] if not trace.get("pour", False)],
               "vias": [{"net_id": str(via.get("net")),
                          "position_mm": [float(via.get("x_mm", 0)), float(via.get("y_mm", 0))]}
                         for via in project.get("vias") or []],
               "terminals": terminals}
    return {"product_id": product_id, "components": components, "nets": nets,
            "candidate_evidence": {"single_rigid": {"routing": routing}},
            "constraints": {"distributed_surfaces": bool(distributed_surfaces),
                            "curvature_required": bool(curvature_required),
                            "moving_crossing": bool(moving_crossing),
                            "flex_allowed": True, "harness_allowed": True,
                            "current_board_bounds_mm": [float(project.get("board_width_mm", 0)),
                                                        float(project.get("board_height_mm", 0))]}}


def create_topology_study(source: dict, *, study_id: str | None = None) -> dict:
    product_id = _identifier(source.get("product_id"), "product_id")
    components = list(source.get("components") or [])
    nets = list(source.get("nets") or [])
    if not components:
        raise CoDesignError("topology study requires components")
    source_digest = hashlib.sha256(_canonical(source)).hexdigest()
    groups = _component_groups(components)
    components_by_id = {item["component_id"]: item for item in components}
    constraints = source.get("constraints") or {}
    distributed = bool(constraints.get("distributed_surfaces"))
    curved = bool(constraints.get("curvature_required"))
    moving = bool(constraints.get("moving_crossing"))
    flex_allowed = bool(constraints.get("flex_allowed", True))
    harness_allowed = bool(constraints.get("harness_allowed", True))
    group_names = list(groups) or ["all"]
    wire_length = _wire_length(components_by_id, nets)

    definitions = (
        ("single-rigid", "single_rigid", [sorted(components_by_id)], "none"),
        ("rigid-flex", "rigid_flex", [groups[name] for name in group_names], "flex"),
        ("multi-rigid-harness", "multi_rigid_harness", [groups[name] for name in group_names], "harness"),
    )
    candidates = []
    for candidate_id, topology, memberships, link_kind in definitions:
        islands = [{"island_id": f"{candidate_id}-island-{index + 1}",
                    "kind": "rigid", "component_ids": members}
                   for index, members in enumerate(memberships)]
        owner = {component_id: island["island_id"] for island in islands
                 for component_id in island["component_ids"]}
        interconnects = _interconnects(nets, owner, link_kind)
        failures = []
        rationale = []
        if topology == "single_rigid" and (curved or moving):
            failures.append("a single rigid board cannot cross required curvature or motion")
        if topology == "rigid_flex" and not flex_allowed:
            failures.append("rigid-flex is prohibited by the manufacturing constraints")
        if topology == "multi_rigid_harness" and not harness_allowed:
            failures.append("wire-harness islands are prohibited by the manufacturing constraints")
        if distributed and topology != "single_rigid":
            rationale.append("distributed interaction surfaces support separated electronic regions")
        if not distributed and topology == "single_rigid":
            rationale.append("one rigid board avoids unnecessary interconnect reliability penalties")
        if topology != "single_rigid":
            rationale.append(f"functional groups produce {len(islands)} physical region(s)")
        routing_evidence = ((source.get("candidate_evidence") or {}).get(topology) or {}).get("routing")
        route_score = score_routing(routing_evidence) if isinstance(routing_evidence, dict) else None
        measured_length = route_score["weighted_actual_length_mm"] if route_score else wire_length
        length_method = ("measured routed copper" if route_score else
                         "pin-position Euclidean lower-bound; exact routed length pending")
        length_status = "pass" if measured_length is not None else "incomplete"
        if route_score and route_score["status"] == "fail":
            failures.extend(route_score["failures"])
        metrics = {
            "weighted_connection_length_mm": _metric_record(
                length_status, measured_length, "mm", 4.0, length_method),
            "octilinear_route_compliance": _metric_record(
                "pass" if route_score and route_score["status"] == "pass" else
                "fail" if route_score else "incomplete",
                1.0 if route_score and route_score["status"] == "pass" else
                0.0 if route_score else None, "ratio", 4.0,
                "measured 0/45/90-degree copper segments" if route_score else
                "actual routed copper is unavailable"),
            "interconnect_count": _metric_record("pass", len(interconnects), "count", 3.0,
                                                    "cross-island net count"),
            "island_count": _metric_record("pass", len(islands), "count", 1.0,
                                             "deterministic functional partition"),
            "thermal": _metric_record("incomplete", None, "score", 3.0,
                                        "exact thermal solver required"),
            "structural": _metric_record("incomplete", None, "score", 3.0,
                                           "exact structural solver required"),
            "signal_integrity": _metric_record("incomplete", None, "score", 4.0,
                                                 "routed stackup simulation required"),
            "manufacturing_cost": _metric_record("incomplete", None, "currency", 2.0,
                                                   "manufacturer quote required"),
            "interconnect_evidence": _metric_record(
                "incomplete" if interconnects else "not_applicable", None, "status", 4.0,
                "connector pins, route length, voltage drop and bend radius require verified topology geometry"
                if interconnects else "single-board candidate has no cross-island interconnect"),
        }
        incomplete = [name for name, metric in metrics.items() if metric["status"] == "incomplete"]
        status = "rejected" if failures else "incomplete" if incomplete else "valid"
        # Lower is better. Only trusted pass metrics enter this provisional cost.
        trusted_cost = (float(metrics["weighted_connection_length_mm"]["value"] or 0)
                        + len(interconnects) * 20.0 + max(0, len(islands) - 1) * 10.0)
        if topology == "single_rigid" and distributed:
            trusted_cost += 50.0
        candidates.append({"candidate_id": candidate_id, "topology": topology,
                           "status": status, "islands": islands, "interconnects": interconnects,
                           "metrics": metrics, "hard_failures": failures,
                           "incomplete_analyses": incomplete,
                           "score": None if failures else trusted_cost, "rank": None,
                           "rationale": rationale})
    ranked = sorted((candidate for candidate in candidates if candidate["status"] != "rejected"),
                    key=lambda candidate: (candidate["score"], candidate["candidate_id"]))
    for rank, candidate in enumerate(ranked, 1):
        candidate["rank"] = rank
    study = {"schema": "design-studio.pcb-topology-study/1",
             "study_id": _identifier(study_id or f"topology-{product_id}", "study_id"),
             "product_id": product_id, "source_digest": source_digest,
             "ranking_status": "provisional", "candidates": candidates,
             "approval_required": True, "created_utc": _utc_now()}
    _finish(study, "study_digest")
    validate_document(study)
    return study


def approve_island_plan(study: dict, selected_candidate_id: str, *, approved_by: str,
                        rationale: str, island_details: dict[str, dict] | None = None,
                        interconnect_details: dict[str, dict] | None = None,
                        plan_id: str | None = None) -> dict:
    validate_document(study)
    verify_digest(study, "study_digest")
    candidate = next((item for item in study["candidates"]
                      if item["candidate_id"] == selected_candidate_id), None)
    if candidate is None or candidate["status"] == "rejected":
        raise CoDesignError("a rejected or missing topology cannot be approved")
    details = island_details or {}
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    islands = []
    for item in candidate["islands"]:
        detail = details.get(item["island_id"], {})
        geometry_digest = str(detail.get("geometry_digest") or "")
        if not _DIGEST.fullmatch(geometry_digest):
            raise CoDesignError(f"verified geometry digest is required for {item['island_id']}")
        islands.append({"island_id": item["island_id"], "kind": item["kind"],
                        "component_ids": item["component_ids"],
                        "stackup": copy.deepcopy(detail.get("stackup") or {}),
                        "transform_mm": copy.deepcopy(detail.get("transform_mm") or identity),
                        "bounds_mm": copy.deepcopy(detail.get("bounds_mm") or {}),
                        "mount_points_mm": copy.deepcopy(detail.get("mount_points_mm") or []),
                        "connectors": copy.deepcopy(detail.get("connectors") or []),
                        "geometry_digest": geometry_digest})
    verified_interconnects = []
    details_by_link = interconnect_details or {}
    for item in candidate["interconnects"]:
        detail = details_by_link.get(item["interconnect_id"], {})
        missing = [name for name in ("connector_pin_map", "route_length_mm",
                   "minimum_bend_radius_mm", "estimated_voltage_drop_v",
                   "signal_integrity_status", "route_geometry_digest", "evidence_digest")
                   if detail.get(name) is None]
        if missing:
            raise CoDesignError(f"verified interconnect evidence is required for {item['interconnect_id']}: "
                                + ", ".join(missing))
        pins = detail.get("connector_pin_map")
        if not isinstance(pins, list) or len(pins) < 2:
            raise CoDesignError(f"connector pin mapping is incomplete for {item['interconnect_id']}")
        for digest_name in ("route_geometry_digest", "evidence_digest"):
            if not _DIGEST.fullmatch(str(detail.get(digest_name) or "")):
                raise CoDesignError(f"{digest_name} is invalid for {item['interconnect_id']}")
        for numeric in ("route_length_mm", "minimum_bend_radius_mm", "estimated_voltage_drop_v"):
            value = detail.get(numeric)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise CoDesignError(f"{numeric} is invalid for {item['interconnect_id']}")
        if detail.get("signal_integrity_status") not in {"pass", "not_applicable"}:
            raise CoDesignError(f"signal integrity is not verified for {item['interconnect_id']}")
        verified_interconnects.append({
            "interconnect_id": item["interconnect_id"], "kind": item["kind"],
            "net_id": item["net_id"], "island_ids": item["island_ids"],
            "endpoint_component_ids": item["endpoint_component_ids"],
            "connector_pin_map": copy.deepcopy(pins),
            "route_length_mm": float(detail["route_length_mm"]),
            "minimum_bend_radius_mm": float(detail["minimum_bend_radius_mm"]),
            "estimated_voltage_drop_v": float(detail["estimated_voltage_drop_v"]),
            "signal_integrity_status": detail["signal_integrity_status"],
            "route_geometry_digest": detail["route_geometry_digest"],
            "evidence_digest": detail["evidence_digest"],
        })
    now = _utc_now()
    result = {"schema": "design-studio.pcb-island-plan/1",
              "plan_id": _identifier(plan_id or f"plan-{study['study_id']}", "plan_id"),
              "study_id": study["study_id"], "study_digest": study["study_digest"],
              "selected_candidate_id": selected_candidate_id, "islands": islands,
              "interconnects": verified_interconnects,
              "approval": {"approved_by": str(approved_by).strip(),
                           "approved_utc": now, "rationale": str(rationale).strip()},
              "created_utc": now}
    _finish(result, "plan_digest")
    validate_document(result)
    return result


def _mechanical_requirement(requirement_id: str, kind: str, strategy: str,
                            inputs: dict, screening: dict, interfaces: list[str],
                            complete: bool) -> dict:
    return {"requirement_id": requirement_id, "kind": kind, "strategy": strategy,
            "inputs": inputs, "screening": screening,
            "evidence_status": "incomplete" if complete else "missing",
            "selection_status": "proposed" if complete else "blocked",
            "interfaces": interfaces}


def derive_mechanical_requirements(source: dict, *, requirements_id: str | None = None) -> dict:
    product_id = _identifier(source.get("product_id"), "product_id")
    source_digest = hashlib.sha256(_canonical(source)).hexdigest()
    requirements = []
    pcbs = list(source.get("pcb_islands") or [])
    loads = source.get("loads") or {}
    total_load = loads.get("assembly_load_n")
    safety = float(loads.get("safety_factor", 2.0))
    if pcbs:
        complete = isinstance(total_load, (int, float)) and not isinstance(total_load, bool) and total_load >= 0
        count = max(3, int(source.get("minimum_fastener_count", 4)))
        screening = ({"method": "equal-load static screening",
                      "design_load_n": float(total_load) * safety,
                      "minimum_count": count,
                      "required_capacity_per_fastener_n": float(total_load) * safety / count}
                     if complete else {"status": "incomplete", "reason": "assembly load is missing"})
        interfaces = [str(item.get("island_id")) for item in pcbs if item.get("island_id")]
        requirements.append(_mechanical_requirement("pcb-fasteners", "fastener",
            "catalog_plus_custom_interface", {"safety_factor": safety}, screening, interfaces, complete))
        requirements.append(_mechanical_requirement("pcb-carrier", "carrier", "custom_cad",
            {"island_count": len(pcbs)}, {"status": "incomplete", "reason": "B-Rep fit and stiffness check required"}, interfaces, True))
    for index, mechanism in enumerate(source.get("mechanisms") or []):
        kind = str(mechanism.get("kind") or "")
        interface = [str(value) for value in mechanism.get("interfaces") or []]
        if kind == "revolute":
            radial = mechanism.get("radial_load_n")
            speed = mechanism.get("speed_rpm")
            life = mechanism.get("life_hours")
            complete = all(isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                           for value in (radial, speed, life))
            screening = ({"method": "ISO-281 basic dynamic load screen",
                          "required_dynamic_rating_n": float(radial) *
                          ((60.0 * float(speed) * float(life) / 1_000_000.0) ** (1.0 / 3.0)) * safety}
                         if complete else {"status": "incomplete", "reason": "radial load, speed and life are required"})
            requirements.append(_mechanical_requirement(f"bearing-{index + 1}", "bearing",
                "catalog_plus_custom_interface", copy.deepcopy(mechanism), screening, interface, complete))
        elif kind == "linear":
            requirements.append(_mechanical_requirement(f"guide-{index + 1}", "bushing",
                "catalog_plus_custom_interface", copy.deepcopy(mechanism),
                {"status": "incomplete", "reason": "wear, side-load and tolerance solver required"}, interface, False))
    total_power = sum(float(item.get("power_w", 0)) for item in source.get("heat_sources") or []
                      if isinstance(item.get("power_w", 0), (int, float)))
    if total_power > 0:
        thermal = source.get("thermal") or {}
        ambient, maximum = thermal.get("ambient_c"), thermal.get("maximum_component_c")
        complete = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       for value in (ambient, maximum)) and maximum > ambient
        screening = ({"method": "steady-state thermal-resistance upper bound",
                      "maximum_total_theta_c_per_w": (float(maximum) - float(ambient)) / total_power,
                      "power_w": total_power}
                     if complete else {"status": "incomplete", "reason": "ambient and maximum temperatures are required"})
        requirements.append(_mechanical_requirement("thermal-management", "heatsink",
            "catalog_plus_custom_interface", {"power_w": total_power}, screening,
            [str(item.get("component_id")) for item in source.get("heat_sources") or [] if item.get("component_id")], complete))
    if source.get("cables"):
        requirements.append(_mechanical_requirement("cable-strain-relief", "strain_relief",
            "catalog_plus_custom_interface", {"cable_count": len(source["cables"])},
            {"status": "incomplete", "reason": "retention load and cable bend evidence required"},
            [str(item.get("cable_id")) for item in source["cables"] if item.get("cable_id")], False))
    categories = {"structural": "incomplete", "thermal": "incomplete" if total_power else "not_applicable",
                  "vibration": "incomplete", "tolerance": "incomplete", "assembly": "incomplete"}
    result = {"schema": "design-studio.mechanical-component-requirements/1",
              "requirements_id": _identifier(requirements_id or f"mechanics-{product_id}", "requirements_id"),
              "product_id": product_id, "source_digest": source_digest,
              "requirements": requirements, "analysis_summary": categories,
              "created_utc": _utc_now()}
    _finish(result, "requirements_digest")
    validate_document(result)
    return result


def write_record(path: str | Path, record: dict) -> Path:
    validate_document(record)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.replace(destination)
    return destination
