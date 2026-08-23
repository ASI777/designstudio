"""Incremental schematic state and deterministic electrical propagation.

This is the orchestration layer above bound components. Edits update the
schematic first, identify affected nets, and recompute ERC, rail operating
points, power-tree edges, stability prerequisites, and simulation readiness.
Numerical solvers remain tools; missing models produce `incomplete`, never a
fabricated pass. A safe declarative behavioral-power model can be evaluated
internally for dropout, current limit, dissipation, and junction temperature.
"""
from __future__ import annotations

import copy
import json
import math
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .component_binding import validate_binding


_GROUND = ("GND", "VSS", "AGND", "PGND", "DGND", "VSSQ")


def _ground(name: str) -> bool:
    value = (name or "").upper()
    return value in _GROUND or any(token in value for token in _GROUND)


def _power_name(name: str) -> bool:
    upper = (name or "").upper()
    return _ground(upper) or any(token in upper for token in
        ("VCC", "VDD", "VIN", "VOUT", "VBUS", "VBAT", "3V3", "5V", "12V", "1V8"))


def _merge_status(items: list[dict]) -> str:
    statuses = {item.get("status") for item in items}
    if "fail" in statuses:
        return "fail"
    if "incomplete" in statuses:
        return "incomplete"
    if statuses and statuses <= {"pass", "not_applicable"}:
        return "pass"
    return "not_applicable"


def _result(status: str, findings: list[dict], **extra) -> dict:
    return {"status": status, "findings": findings, **extra}


@dataclass
class IncrementalDesign:
    resolver: Callable[[str], dict]
    components: dict[str, dict] = field(default_factory=dict)
    connections: dict[tuple[str, str], str] = field(default_factory=dict)
    symbol_positions: dict[str, list[float]] = field(default_factory=dict)
    revision: int = 0
    _net_reports: dict[str, dict] = field(default_factory=dict)
    last_report: dict = field(default_factory=dict)
    event_log: list[dict] = field(default_factory=list)

    def add_component(self, ref: str, mpn: str, position: list[float] | None = None) -> dict:
        if ref in self.components:
            raise ValueError(f"component {ref} already exists")
        binding = self.resolver(mpn)
        if binding is None:
            raise ValueError(f"no complete bound component for {mpn}")
        validate_binding(binding, require_complete=True, verify_asset=False)
        self.components[ref] = binding
        self.symbol_positions[ref] = position or self._next_symbol_position()
        return self._changed({ref}, set())

    def remove_component(self, ref: str) -> dict:
        if ref not in self.components:
            raise ValueError(f"unknown component {ref}")
        affected = {net for (owner, _), net in self.connections.items() if owner == ref}
        self.connections = {key: net for key, net in self.connections.items() if key[0] != ref}
        self.components.pop(ref)
        self.symbol_positions.pop(ref, None)
        return self._changed({ref}, affected)

    def replace_component(self, ref: str, mpn: str) -> dict:
        if ref not in self.components:
            raise ValueError(f"unknown component {ref}")
        binding = self.resolver(mpn)
        if binding is None:
            raise ValueError(f"no complete bound component for {mpn}")
        validate_binding(binding, require_complete=True, verify_asset=False)
        valid_pins = {str(pin.get("number", "")).upper()
                      for pin in binding["symbol"].get("pins", [])}
        stale = [(key, net) for key, net in self.connections.items()
                 if key[0] == ref and key[1] not in valid_pins]
        for key, _ in stale:
            self.connections.pop(key)
        affected = {net for _, net in stale} | {
            net for (owner, _), net in self.connections.items() if owner == ref}
        self.components[ref] = binding
        return self._changed({ref}, affected)

    def connect(self, ref: str, pin: str, net: str) -> dict:
        pin_number = self._resolve_pin_number(ref, pin)
        key = (ref, pin_number)
        affected = {net}
        if key in self.connections:
            affected.add(self.connections[key])
        self.connections[key] = net
        return self._changed({ref}, affected)

    def disconnect(self, ref: str, pin: str) -> dict:
        pin_number = self._resolve_pin_number(ref, pin)
        net = self.connections.pop((ref, pin_number), None)
        return self._changed({ref}, {net} if net else set())

    def _resolve_pin_number(self, ref: str, key: str) -> str:
        binding = self.components.get(ref)
        if binding is None:
            raise ValueError(f"unknown component {ref}")
        sought = str(key).upper()
        for pin in binding["symbol"].get("pins", []):
            if str(pin.get("number", "")).upper() == sought \
                    or str(pin.get("name", "")).upper() == sought:
                return str(pin["number"]).upper()
        raise ValueError(f"{ref} has no pin {key}")

    def _next_symbol_position(self) -> list[float]:
        index = len(self.symbol_positions)
        return [20.0 + (index % 4) * 45.0, 20.0 + (index // 4) * 45.0]

    def _changed(self, refs: set[str], nets: set[str]) -> dict:
        self.revision += 1
        for ref in refs:
            nets.update(net for (owner, _), net in self.connections.items() if owner == ref)
        nets.discard(None)
        for net in sorted(nets):
            self._net_reports[net] = self._analyze_net(net)
        live_nets = set(self.connections.values())
        self._net_reports = {net: report for net, report in self._net_reports.items()
                             if net in live_nets}
        self.last_report = self._build_report(sorted(nets), sorted(refs))
        self.event_log.append({"revision": self.revision, "changed_refs": sorted(refs),
                               "recomputed_nets": sorted(nets),
                               "status": self.last_report["status"]})
        return copy.deepcopy(self.last_report)

    def _pins_on_net(self, net: str) -> list[dict]:
        result = []
        for (ref, number), assigned in self.connections.items():
            if assigned != net or ref not in self.components:
                continue
            binding = self.components[ref]
            pin = next(pin for pin in binding["symbol"]["pins"]
                       if str(pin.get("number", "")).upper() == number)
            domain = self._domain_for_pin(binding, pin)
            result.append({"ref": ref, "pin": pin, "domain": domain,
                           "binding": binding})
        return result

    @staticmethod
    def _domain_for_pin(binding: dict, pin: dict) -> dict:
        number, name = str(pin.get("number", "")).upper(), str(pin.get("name", "")).upper()
        for domain in (binding.get("electrical") or {}).get("power_domains", []) or []:
            members = {str(member).upper() for member in domain.get("pins", [])}
            if number in members or name in members:
                return domain
        return {}

    def _analyze_net(self, net: str) -> dict:
        pins = self._pins_on_net(net)
        findings: list[dict] = []
        outputs = [item for item in pins if item["pin"].get("electrical_type") == "output"]
        if len(outputs) > 1:
            findings.append({"rule": "output_conflict", "status": "fail",
                             "message": f"{net} has {len(outputs)} push-pull outputs"})
        for item in pins:
            if item["pin"].get("electrical_type") == "nc":
                findings.append({"rule": "nc_connected", "status": "fail",
                                 "message": f"{item['ref']}.{item['pin']['number']} is NC"})

        sources = [item for item in pins if item["pin"].get("electrical_type") == "power_out"]
        loads = [item for item in pins if item["pin"].get("electrical_type") == "power_in"
                 and not _ground(item["pin"].get("name", ""))]
        grounds = [item for item in pins if _ground(item["pin"].get("name", ""))]
        if sources and grounds:
            findings.append({"rule": "power_ground_short", "status": "fail",
                             "message": f"{net} contains power output and ground pins"})

        source_ranges = [self._voltage_range(item["domain"]) for item in sources]
        load_ranges = [self._voltage_range(item["domain"]) for item in loads]
        known_sources = [value for value in source_ranges if value]
        known_loads = [value for value in load_ranges if value]
        voltage = None
        if known_sources:
            lo = max(value[0] for value in known_sources)
            hi = min(value[1] for value in known_sources)
            if lo > hi:
                findings.append({"rule": "source_voltage_conflict", "status": "fail",
                                 "message": f"{net} power sources have incompatible ranges"})
            else:
                voltage = sum(value[2] for value in known_sources) / len(known_sources)
                for index, load_range in enumerate(known_loads):
                    if not load_range[0] <= voltage <= load_range[1]:
                        findings.append({"rule": "load_voltage_mismatch", "status": "fail",
                                         "message": f"{net} source {voltage:g} V is outside load range "
                                                    f"{load_range[0]:g}..{load_range[1]:g} V"})
        elif loads and _power_name(net):
            findings.append({"rule": "source_missing", "status": "incomplete",
                             "message": f"{net} has loads but no modeled power source"})

        load_current = sum(self._domain_current(item["domain"]) or 0 for item in loads)
        unknown_loads = sum(1 for item in loads if self._domain_current(item["domain"]) is None)
        if unknown_loads:
            findings.append({"rule": "load_current_missing", "status": "incomplete",
                             "message": f"{unknown_loads} load domain(s) have no max current"})
        if not findings:
            findings.append({"rule": "net_electrical", "status": "pass",
                             "message": f"{net} electrical constraints are consistent"})
        return {"net": net, "status": _merge_status(findings), "findings": findings,
                "sources": [item["ref"] for item in sources],
                "loads": [item["ref"] for item in loads], "voltage_v": voltage,
                "load_current_a": load_current,
                "load_power_w": voltage * load_current if voltage is not None else None}

    @staticmethod
    def _voltage_range(domain: dict) -> tuple[float, float, float] | None:
        if not domain:
            return None
        nominal = domain.get("vnom_v", domain.get("typ_v"))
        lo, hi = domain.get("vmin_v"), domain.get("vmax_v")
        if nominal is None and lo is not None and hi is not None:
            nominal = (lo + hi) / 2
        if nominal is None:
            return None
        lo = nominal if lo is None else lo
        hi = nominal if hi is None else hi
        values = (float(lo), float(hi), float(nominal))
        return values if all(math.isfinite(value) for value in values) else None

    @staticmethod
    def _domain_current(domain: dict) -> float | None:
        value = domain.get("max_current_a") if domain else None
        return float(value) if isinstance(value, (int, float)) and value >= 0 else None

    def _build_report(self, nets: list[str], refs: list[str]) -> dict:
        net_reports = list(self._net_reports.values())
        erc_findings = [finding for report in net_reports for finding in report["findings"]
                        if finding["rule"] in ("output_conflict", "nc_connected",
                                               "power_ground_short")]
        for ref, binding in self.components.items():
            for pin in binding["symbol"].get("pins", []):
                if pin.get("electrical_type") not in ("power_in", "power_out"):
                    continue
                number = str(pin.get("number", "")).upper()
                if (ref, number) not in self.connections:
                    erc_findings.append({"rule": "power_pin_unconnected", "status": "fail",
                        "message": f"{ref}.{pin.get('name') or number} is not connected"})
        erc = _result(_merge_status(erc_findings) if erc_findings else "pass", erc_findings)
        dc_findings = [finding for report in net_reports for finding in report["findings"]
                       if finding["rule"] in ("source_voltage_conflict", "load_voltage_mismatch",
                                              "source_missing", "load_current_missing")]
        dc = _result(_merge_status(dc_findings) if dc_findings else "pass", dc_findings,
                     rails=net_reports)
        edges = [{"net": report["net"], "source": source, "load": load}
                 for report in net_reports for source in report["sources"]
                 for load in report["loads"]]
        power_status = "incomplete" if any(report["loads"] and not report["sources"]
                                           for report in net_reports) else "pass"
        power_tree = _result(power_status, [], edges=edges)

        regulators = [(ref, binding) for ref, binding in self.components.items()
                      if str(binding.get("component", {}).get("category", "")).lower()
                      in ("regulator", "power", "pmic", "converter")]
        stability_findings = []
        for ref, binding in regulators:
            required = [external for external in binding.get("electrical", {}).get(
                        "required_externals", []) if external.get("mandatory", True)]
            for external in required:
                endpoint_nets = []
                for endpoint in external.get("connect_between", [])[:2]:
                    try:
                        number = self._resolve_pin_number(ref, endpoint)
                    except ValueError:
                        number = ""
                    endpoint_nets.append(self.connections.get((ref, number), ""))
                if len(endpoint_nets) != 2 or not all(endpoint_nets):
                    stability_findings.append({"rule": "required_external_endpoint_unconnected",
                        "status": "incomplete", "message":
                        f"{ref} {external.get('purpose', 'required external')} endpoints are not fully connected"})
                    continue
                target = set(endpoint_nets)
                support = []
                for candidate, candidate_binding in self.components.items():
                    if candidate == ref:
                        continue
                    category = str(candidate_binding.get("component", {}).get("category", "")).lower()
                    if category not in ("passive", "capacitor", "resistor", "inductor",
                                        "ferrite", "crystal", "oscillator", "diode"):
                        continue
                    candidate_nets = {net for (owner, _), net in self.connections.items()
                                      if owner == candidate}
                    if target <= candidate_nets:
                        support.append(candidate)
                if not support:
                    stability_findings.append({"rule": "required_external_missing",
                        "status": "fail", "message":
                        f"{ref} requires {external.get('value', '')} {external.get('purpose', '')} "
                        f"between {endpoint_nets[0]} and {endpoint_nets[1]}"})
            loop_model = binding.get("simulation_models", {}).get("control_loop")
            if not loop_model:
                stability_findings.append({"rule": "control_loop_model_missing",
                    "status": "incomplete",
                    "message": f"{ref} has no control-loop stability model"})
            elif loop_model.get("not_applicable") is not True:
                phase_margin = loop_model.get("phase_margin_deg")
                gain_margin = loop_model.get("gain_margin_db")
                if not isinstance(phase_margin, (int, float)) \
                        or not isinstance(gain_margin, (int, float)):
                    stability_findings.append({"rule": "control_loop_margin_missing",
                        "status": "incomplete", "message":
                        f"{ref} control-loop model lacks phase/gain margins"})
                elif float(phase_margin) < 45.0 or float(gain_margin) < 10.0:
                    stability_findings.append({"rule": "control_loop_margin_low",
                        "status": "fail", "message":
                        f"{ref} margins are {phase_margin:g} deg / {gain_margin:g} dB; "
                        "requires at least 45 deg / 10 dB"})
        stability = _result(_merge_status(stability_findings) if stability_findings
                            else ("not_applicable" if not regulators else "pass"), stability_findings)

        spice = self._simulation_analysis(regulators)

        from .component_engineering import evaluate_component_engineering
        component_checks: dict[str, list[dict]] = {}
        for ref, binding in self.components.items():
            for name, check in evaluate_component_engineering(binding).items():
                item = copy.deepcopy(check)
                for finding in item.get("findings", []):
                    finding.setdefault("ref", ref)
                component_checks.setdefault(name, []).append(item)
        engineering_categories = {}
        for name, checks in component_checks.items():
            engineering_categories[name] = _result(
                _merge_status(checks),
                [finding for check in checks for finding in check.get("findings", [])],
                critical=any(check.get("critical", True) for check in checks))

        categories = {"erc": erc, "dc_operating_point": dc, "power_tree": power_tree,
                      "stability": stability, "spice": spice, **engineering_categories}
        return {"schema": "design-studio.incremental-analysis/1", "revision": self.revision,
                "changed_refs": refs, "recomputed_nets": nets,
                "status": _merge_status(list(categories.values())),
                "categories": categories, "schematic": self.schematic()}

    def _simulation_analysis(self, active_power: list[tuple[str, dict]]) -> dict:
        if not active_power:
            return _result("not_applicable", [], engine="none")
        findings: list[dict] = []
        results: list[dict] = []
        for ref, binding in active_power:
            models = binding.get("simulation_models", {})
            behavioral = models.get("behavioral_power")
            if behavioral:
                result, component_findings = self._evaluate_behavioral_power(
                    ref, binding, behavioral)
                results.append(result)
                findings.extend(component_findings)
                continue
            if models.get("spice"):
                from .spice_simulation import run_validated_spice
                simulated = run_validated_spice(models.get("spice"))
                results.append({"ref": ref, **simulated})
                if simulated["status"] != "pass":
                    findings.append({"rule": "spice_incomplete", "status": "incomplete",
                                     "message": f"{ref}: {simulated['message']}"})
                continue
            findings.append({"rule": "simulation_model_missing", "status": "incomplete",
                "message": f"{ref} has no bound SPICE or behavioral power model"})
        return _result(_merge_status(findings) if findings else "pass", findings,
                       engine="behavioral-power/1", results=results)

    def _evaluate_behavioral_power(self, ref: str, binding: dict,
                                   model: dict) -> tuple[dict, list[dict]]:
        findings: list[dict] = []
        if model.get("schema") != "design-studio.behavioral-power/1" \
                or model.get("kind") not in ("linear_regulator", "dc_converter"):
            return ({"ref": ref, "status": "fail"}, [{
                "rule": "behavioral_model_invalid", "status": "fail",
                "message": f"{ref} behavioral power model schema/kind is invalid"}])

        def connected_net(pin_key: str) -> str:
            try:
                number = self._resolve_pin_number(ref, str(model.get(pin_key, "")))
            except ValueError:
                return ""
            return self.connections.get((ref, number), "")

        input_net = connected_net("input_pin")
        output_net = connected_net("output_pin")
        by_net = {report["net"]: report for report in self._net_reports.values()}
        output = by_net.get(output_net, {})
        vin = model.get("input_voltage_v")
        if vin is None:
            vin = (by_net.get(input_net) or {}).get("voltage_v")
        vout = output.get("voltage_v")
        load = output.get("load_current_a")
        numeric = (vin, vout, load)
        if not input_net or not output_net or any(not isinstance(value, (int, float))
                                                  for value in numeric):
            findings.append({"rule": "behavioral_operating_point_incomplete",
                "status": "incomplete", "message":
                f"{ref} needs connected input/output pins and known VIN/VOUT/load"})
            return ({"ref": ref, "input_net": input_net, "output_net": output_net,
                     "status": "incomplete"}, findings)
        vin, vout, load = map(float, numeric)
        limit = model.get("max_output_current_a")
        if isinstance(limit, (int, float)) and load > float(limit) + 1e-12:
            findings.append({"rule": "behavioral_overcurrent", "status": "fail",
                "message": f"{ref} load {load:g} A exceeds {float(limit):g} A"})
        dropout = float(model.get("dropout_v", 0.0))
        if model["kind"] == "linear_regulator" and vout > vin - dropout + 1e-12:
            findings.append({"rule": "behavioral_dropout", "status": "fail",
                "message": f"{ref} requires VIN >= {vout + dropout:g} V; has {vin:g} V"})
        efficiency = float(model.get("efficiency", 1.0))
        if not 0 < efficiency <= 1:
            findings.append({"rule": "behavioral_efficiency_invalid", "status": "fail",
                "message": f"{ref} efficiency must be in (0, 1]"})
            efficiency = 1.0
        iq = float(model.get("quiescent_current_a", 0.0))
        if model["kind"] == "linear_regulator":
            loss = max(0.0, (vin - vout) * load + vin * iq)
        else:
            loss = max(0.0, vout * load * (1.0 / efficiency - 1.0) + vin * iq)
        ambient = model.get("ambient_c")
        theta = model.get("theta_ja_c_per_w")
        max_junction = model.get("max_junction_c")
        junction = None
        if all(isinstance(value, (int, float)) for value in (ambient, theta, max_junction)):
            junction = float(ambient) + loss * float(theta)
            if junction > float(max_junction) + 1e-12:
                findings.append({"rule": "behavioral_overtemperature", "status": "fail",
                    "message": f"{ref} estimated junction {junction:.1f} C exceeds "
                               f"{float(max_junction):g} C"})
        else:
            findings.append({"rule": "behavioral_thermal_inputs_missing",
                "status": "incomplete", "message":
                f"{ref} needs ambient, theta-JA, and maximum junction temperature"})
        if not findings:
            findings.append({"rule": "behavioral_operating_point", "status": "pass",
                "message": f"{ref} behavioral operating point is within limits"})
        status = _merge_status(findings)
        return ({"ref": ref, "status": status, "input_net": input_net,
                 "output_net": output_net, "vin_v": vin, "vout_v": vout,
                 "load_current_a": load, "loss_w": loss,
                 "junction_c": junction}, findings)

    def schematic(self) -> dict:
        symbols = []
        for ref, binding in self.components.items():
            pins = []
            order = {0: 0, 1: 0, 2: 0, 3: 0}
            toggle = 0
            for pin in binding["symbol"].get("pins", []):
                number = str(pin.get("number", "")).upper()
                etype = pin.get("electrical_type", "passive")
                name = pin.get("name", "")
                if _ground(name):
                    side = 3
                elif etype == "power_in":
                    side = 2
                elif etype in ("power_out", "output"):
                    side = 1
                elif etype == "input":
                    side = 0
                else:
                    side = 0 if toggle % 2 == 0 else 1
                    toggle += 1
                pin_order = order[side]
                order[side] += 1
                pins.append({"num": pin.get("number", ""), "name": pin.get("name", ""),
                             "etype": etype, "side": side, "order": pin_order,
                             "net_name": self.connections.get((ref, number), "")})
            symbols.append({"ref": ref, "lib": binding["binding_id"],
                            "x_mm": self.symbol_positions[ref][0],
                            "y_mm": self.symbol_positions[ref][1],
                            "rot_deg": 0.0, "unit": 1, "pins": pins})
        return {"symbols": symbols, "wires": []}

    def save_report(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.last_report, indent=2) + "\n", encoding="utf-8")


def analyze_bound_design(components: list[dict], netlist: list[dict],
                         resolver: Callable[[str], dict | None] | None = None) -> dict:
    """Replay a proposed design through the incremental engine.

    Replay is intentional: it verifies that every edit boundary is analyzable and
    returns the compact event trail proving which dependency cone was recomputed.
    """
    if resolver is None:
        from .component_binding import BoundComponentStore
        store = BoundComponentStore()
        resolver = lambda mpn: store.get(mpn, require_complete=True)
    resolved: dict[str, dict] = {}
    missing = []
    invalid = []
    for component in components:
        mpn = component.get("mpn", "")
        try:
            binding = resolver(mpn)
        except Exception as exc:
            invalid.append({"ref": component.get("ref", ""), "mpn": mpn,
                            "reason": str(exc)})
            continue
        if binding is None:
            missing.append({"ref": component.get("ref", ""), "mpn": mpn,
                            "reason": "complete bound component is not installed"})
        else:
            resolved[mpn] = binding
    if invalid or missing:
        status = "fail" if invalid else "incomplete"
        return {"schema": "design-studio.incremental-analysis/1", "revision": 0,
                "status": status, "changed_refs": [], "recomputed_nets": [],
                "categories": {"component_binding": {
                    "status": status, "findings": invalid + missing}},
                "missing_bindings": missing, "invalid_bindings": invalid,
                "event_log": [], "schematic": {"symbols": [], "wires": []}}
    design = IncrementalDesign(lambda mpn: resolved.get(mpn))
    for component in components:
        design.add_component(component["ref"], component["mpn"])
    for connection in netlist:
        design.connect(connection["ref"], str(connection["pin"]), connection["net"])
    report = copy.deepcopy(design.last_report)
    reference_findings = []
    reference_applicable = False
    for component in components:
        ref, mpn = component.get("ref", ""), component.get("mpn", "")
        binding = resolved.get(mpn) or {}
        evidence = binding.get("datasheet_evidence") or {}
        category = str((binding.get("component") or {}).get("category") or "").lower()
        circuits = (binding.get("electrical") or {}).get("application_circuits") or []
        if evidence.get("mpn_match") != "exact" or category not in (
                "regulator", "mcu", "transistor", "module", "power", "pmic", "converter"):
            continue
        reference_applicable = True
        mode = str(component.get("application_mode") or "").strip().lower()
        selected = [item for item in circuits
                    if str(item.get("mode") or "").strip().lower() == mode]
        if not selected and not mode and len(circuits) == 1:
            selected = circuits
        if not selected:
            reference_findings.append({"rule": "application_mode_unresolved",
                "status": "incomplete", "ref": ref, "message":
                "Select one manufacturer application-circuit mode before engineering approval"})
            continue
        circuit = selected[0]
        if not circuit.get("datasheet_pages") or not circuit.get("evidence_summary"):
            reference_findings.append({"rule": "reference_circuit_provenance_missing",
                "status": "incomplete", "ref": ref,
                "message": "Selected application circuit lacks datasheet page/figure evidence"})
            continue
        pins = {}
        for pin in (binding.get("symbol") or {}).get("pins", []):
            number = str(pin.get("number") or "").upper()
            pins[number] = number
            pins[str(pin.get("name") or "").upper()] = number
        for required in circuit.get("connections") or []:
            source_name = str(required.get("from_pin") or "").upper()
            target_name = str(required.get("to") or "").upper()
            source_number = pins.get(source_name, source_name)
            actual = design.connections.get((ref, source_number), "")
            target_number = pins.get(target_name)
            target_actual = design.connections.get((ref, target_number), "") \
                if target_number else target_name
            if not actual or actual.upper() != target_actual.upper():
                reference_findings.append({"rule": "reference_connection_not_applied",
                    "status": "fail", "ref": ref, "from_pin": source_name, "to": target_name,
                    "message": f"{ref}.{source_name} does not match the selected manufacturer circuit"})
    reference_status = _merge_status(reference_findings) if reference_findings else \
        ("pass" if reference_applicable else "not_applicable")
    report.setdefault("categories", {})["reference_circuit_application"] = _result(
        reference_status, reference_findings, critical=True)
    report["status"] = _merge_status(list(report["categories"].values()))
    report["event_log"] = copy.deepcopy(design.event_log)
    return report


def bind_analysis_to_project(analysis: dict, project_path: str | Path,
                             output_path: str | Path | None = None) -> Path:
    """Bind an analysis report to the exact saved project bytes for release."""
    project_path = Path(project_path).resolve()
    project_bytes = project_path.read_bytes()
    project = json.loads(project_bytes)
    report = copy.deepcopy(analysis)
    report["generated_utc"] = datetime.now(timezone.utc).isoformat()
    report["project"] = {
        "document_id": project.get("document_id", ""),
        "revision": int(project.get("revision", 0)),
        "file_sha256": hashlib.sha256(project_bytes).hexdigest(),
    }
    payload = copy.deepcopy(report)
    payload.pop("report_digest", None)
    report["report_digest"] = hashlib.sha256(_canonical_report(payload)).hexdigest()
    target = Path(output_path) if output_path else project_path.with_suffix(
        ".electrical-analysis.json")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def _canonical_report(value) -> bytes:
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
