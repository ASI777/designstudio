#!/usr/bin/env python3
"""Build the deterministic DesignStudio six-axis robot electronics package.

This generator creates one coordinator PCB and six independently parameterized
joint-controller PCB documents.  It deliberately keeps manufacturing authority
separate from geometric/electrical verification: package bodies and functional
pad subsets are sufficient for native routing, DRC, and AP242 assembly export,
but the generated evidence manifest records that reviewed exact-MPN land
patterns, supplier quotes, hardware tests, and SPICE models are still required.

The output is a reference engineering package, never a safety certification or
a claim of physical measurement.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA = "design-studio.robot-electronics-package/1"
SOURCE_REVISION = "1.0.0"


def canonical(value: object) -> bytes:
    def normalize(item: object) -> object:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pin_pad(name: str, x: float, y: float, net: int, *, w: float = 1.2,
            h: float = 1.2, through: bool = False, drill: float = 0.0) -> dict:
    return {"name": name, "x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h,
            "net": net, "th": through, "drill_mm": drill,
            "shape": "circle" if through else "roundrect", "corner_r_mm": 0.15}


def functional_footprint(ref: str, mpn: str, manufacturer: str, x: float, y: float,
                         body_w: float, body_h: float, height: float,
                         pins: Iterable[tuple[str, int]], *, group: str,
                         thermal_power_w: float = 0.0,
                         thermal_clearance_mm: float = 0.5,
                         through: bool = False, rotation: float = 0.0) -> dict:
    """Create a deterministic functional-pin package model.

    These are intentionally labelled preliminary.  Exact reviewed land-pattern
    bindings are required by the release gate before fabrication.
    """
    pin_list = list(pins)
    pads: list[dict] = []
    if len(pin_list) == 1:
        positions = [(0.0, 0.0)]
    else:
        left_count = (len(pin_list) + 1) // 2
        right_count = len(pin_list) - left_count
        pitch = max(1.0, min(2.54, (body_h - 1.0) / max(left_count - 1, 1)))
        left_y = [(index - (left_count - 1) / 2) * pitch for index in range(left_count)]
        right_y = [(index - (right_count - 1) / 2) * pitch for index in range(right_count)]
        positions = [(-body_w / 2 - 0.6, value) for value in left_y]
        positions += [(body_w / 2 + 0.6, value) for value in right_y]
    for (name, net), (px, py) in zip(pin_list, positions):
        pads.append(pin_pad(name, round(px, 3), round(py, 3), net,
                            w=1.6 if through else 1.0,
                            h=1.6 if through else 0.7,
                            through=through, drill=0.8 if through else 0.0))
    margin = 0.8
    return {
        "ref": ref, "lib": mpn, "mpn": mpn, "manufacturer": manufacturer,
        # Empty evidence is intentional: ProjectModel accepts placement while
        # the industrial gate rejects the missing exact-PDF binding.
        "datasheet_evidence": {},
        "x_mm": x, "y_mm": y, "rot_deg": rotation, "side": 0,
        "h3d_mm": height, "body_w_mm": body_w, "body_h_mm": body_h,
        "body_cx_mm": 0.0, "body_cy_mm": 0.0,
        "courtyard_pts": [[-body_w / 2 - margin, -body_h / 2 - margin],
                           [body_w / 2 + margin, -body_h / 2 - margin],
                           [body_w / 2 + margin, body_h / 2 + margin],
                           [-body_w / 2 - margin, body_h / 2 + margin]],
        "placement": {"locked": False, "functional_group": group,
                      "edge_anchor": "none", "thermal_power_w": thermal_power_w,
                      "thermal_clearance_mm": thermal_clearance_mm if thermal_power_w else 0.0,
                      "test_access_required": False, "test_access_halo_mm": 0.0},
        "pads": pads, "regions": [],
        "asset_status": "preliminary_functional_package_envelope"
    }


class Nets:
    def __init__(self) -> None:
        self.ids: dict[str, int] = {}
        self.meta: dict[str, dict] = {}

    def add(self, name: str, class_id: int = 0, *, current: float = 0.0,
            voltage: float = 0.0) -> int:
        if name not in self.ids:
            self.ids[name] = len(self.ids)
            self.meta[name] = {"class": class_id, "required_current_a": current,
                               "nominal_voltage_v": voltage}
        return self.ids[name]

    def table(self) -> list[dict]:
        return [{"id": index, "name": name, **self.meta[name]}
                for name, index in self.ids.items()]


def rules() -> dict:
    blocking = [
        "TRACE_CLEARANCE", "PAD_TRACE_CLEARANCE", "PAD_CLEARANCE", "BOARD_EDGE",
        "MIN_WIDTH", "MIN_DRILL", "VIA_TRACE_CLEARANCE", "VIA_PAD_CLEARANCE",
        "VIA_CLEARANCE", "ANNULAR_RING", "DRILL_TO_DRILL", "NET_ISLAND",
        "SKEW", "UNASSIGNED_COPPER", "COPPER_TO_EDGE", "COPPER_TO_HOLE",
        "COURTYARD_OVERLAP", "RULE_AREA_VIOLATION", "HEIGHT_CONSTRAINT",
        "THERMAL_SPACING", "TEST_ACCESS", "LAYER_POLICY_VIOLATION",
        "VIA_POLICY_VIOLATION", "ZONE_VALIDITY"
    ]
    return {
        "schema_version": 1, "id": "eurocircuits-class2-b-2026-reference",
        "name": "Eurocircuits-compatible IPC Class 2 reference limits",
        "ipc_performance_class": 2, "producibility_level": "B",
        "source": "project:robot-electronics-reference",
        "source_revision": SOURCE_REVISION, "fabricator": "Eurocircuits",
        "assembler": "unselected",
        "limits_mm": {"default_clearance": 0.25, "min_trace_width": 0.15,
                      "min_mechanical_drill": 0.3, "min_annular_ring": 0.15,
                      "min_drill_to_drill": 0.5, "min_microvia_drill": 0.1,
                      "min_microvia_wall": 0.1, "min_copper_to_edge": 0.3,
                      "min_copper_to_hole": 0.25, "min_courtyard_clearance": 0.25,
                      "min_mask_sliver": 0.1, "min_silk_width": 0.12},
        "checks": {"connectivity": True, "skew": True,
                   "release_requires_native_drc": True},
        "severity": {name: "error" for name in blocking}
    }


def classes(power_width: float, copper_layers: int) -> list[dict]:
    allowed = list(range(copper_layers))
    return [
        {"id": 0, "name": "CONTROL", "clearance_mm": 0.25,
         "trace_width_mm": 0.25, "via_diameter_mm": 0.7, "via_drill_mm": 0.35,
         "diff_pair_gap_mm": 0.0, "max_skew_mm": 0.0, "microvia": False,
         "z0_ohm": 0.0, "zdiff_ohm": 0.0, "allowed_layers": allowed,
         "allowed_via_types": ["through"], "max_via_count": 0,
         "signal_frequency_hz": 20_000_000, "impedance_tolerance_pct": 15.0},
        {"id": 1, "name": "POWER", "clearance_mm": 0.35,
         "trace_width_mm": power_width, "via_diameter_mm": 1.2, "via_drill_mm": 0.6,
         "diff_pair_gap_mm": 0.0, "max_skew_mm": 0.0, "microvia": False,
         "z0_ohm": 0.0, "zdiff_ohm": 0.0, "allowed_layers": allowed,
         "allowed_via_types": ["through"], "max_via_count": 0,
         "signal_frequency_hz": 0.0, "impedance_tolerance_pct": 15.0},
        {"id": 2, "name": "CAN_SCREENING_50R", "clearance_mm": 0.25,
         "trace_width_mm": 0.367, "via_diameter_mm": 0.7, "via_drill_mm": 0.35,
         "diff_pair_gap_mm": 0.25, "max_skew_mm": 2.0, "microvia": False,
         "z0_ohm": 50.0, "zdiff_ohm": 0.0, "allowed_layers": allowed,
         "allowed_via_types": ["through"], "max_via_count": 0,
         "signal_frequency_hz": 5_000_000, "impedance_tolerance_pct": 15.0}
    ]


def schematic_for(footprints: list[dict]) -> dict:
    symbols = []
    wires = []
    for index, fp in enumerate(footprints):
        pins = []
        for pin_index, pad in enumerate(fp["pads"]):
            net = int(pad.get("net", -1))
            pins.append({"num": pad["name"], "name": pad["name"],
                         "etype": "passive", "side": pin_index % 2,
                         "order": pin_index // 2, "net": net})
        symbols.append({"ref": fp["ref"], "lib": fp["lib"],
                        "x_mm": 24.0 + (index % 6) * 55.0,
                        "y_mm": 24.0 + (index // 6) * 38.0,
                        "rot_deg": 0.0, "unit": 1, "pins": pins})
    return {"symbols": symbols, "wires": wires}


def base_project(document_id: str, width: float, height: float, nets: Nets,
                 footprints: list[dict], power_width: float, metadata: dict,
                 *, copper_layers: int = 4) -> dict:
    policies = []
    for layer in range(copper_layers):
        outer = layer in (0, copper_layers - 1)
        policies.append({
            "layer": layer,
            "name": "F.Cu" if layer == 0 else "B.Cu" if layer == copper_layers - 1
                    else f"In{layer}.Cu",
            "role": "signal" if outer else "mixed",
            "preferred_direction": "horizontal" if layer % 2 == 0 else "vertical",
            "allow_routing": True,
            "copper_thickness_mm": 0.07 if outer else 0.035,
            "source": "project-stackup", "source_revision": "1"})
    return {
        "version": 2, "document_id": document_id, "revision": 1,
        "board_width_mm": width, "board_height_mm": height,
        "board_outline_pts": [[0, 0], [width, 0], [width, height], [0, height]],
        "board_cutouts": [], "grid_mm": 0.5, "copper_layers": copper_layers,
        "dielectric_er": 4.4, "dielectric_h_mm": 0.2,
        "loss_tangent": 0.02, "copper_t_mm": 0.07,
        "pcb_rules": rules(),
        "verification_requirements": {
            "require_signal_integrity": True, "require_power_integrity": True,
            "require_thermal": True, "require_enclosure_evidence": False,
            "enclosure_evidence_path": "", "enclosure_evidence_sha256": ""
        },
        "nets": None, "net_table": nets.table(),
        "net_classes": classes(power_width, copper_layers),
        "rule_areas": [], "class_pair_rules": [],
        "layer_policies": policies,
        "copper_zones": [], "footprints": footprints, "traces": [], "vias": [],
        "unresolved_components": [],
        "placement_state": {"mode": "optimized", "status": "legalization_passed",
                            "routing": "pending_native_router"},
        "schematic": schematic_for(footprints),
        "design_metadata": metadata
    }


def coordinator_project() -> dict:
    n = Nets()
    gnd = n.add("GND", 1, current=0.5, voltage=0.0)
    bus = n.add("BUS_24V", 1, voltage=24.0)
    v5 = n.add("5V", 1, current=0.5, voltage=5.0)
    v3 = n.add("3V3", 1, current=0.25, voltage=3.3)
    can_h = n.add("CAN_H", 2); can_l = n.add("CAN_L", 2)
    can_tx = n.add("CAN_TX"); can_rx = n.add("CAN_RX")
    estop_in = n.add("ESTOP_IN"); estop_safe = n.add("ESTOP_SAFE")
    brake = n.add("BRAKE_ENABLE"); status = n.add("SYSTEM_STATUS")
    fps = [
        functional_footprint("J1", "1935161", "Phoenix Contact", 8, 12, 8, 8, 12,
                             [("24V", bus), ("GND", gnd)], group="power", through=True),
        functional_footprint("U1", "STM32G431CBT6", "STMicroelectronics", 48, 36, 9, 9, 1.6,
                             [("3V3", v3), ("GND", gnd), ("CAN_TX", can_tx), ("CAN_RX", can_rx),
                              ("ESTOP", estop_safe), ("BRAKE", brake), ("STATUS", status)],
                             group="control", thermal_power_w=0.45, thermal_clearance_mm=1.0),
        functional_footprint("U2", "ISO1042DWR", "Texas Instruments", 70, 36, 10.3, 10.3, 2.65,
                             [("VCC1", v3), ("GND1", gnd), ("TXD", can_tx), ("RXD", can_rx),
                              ("VCC2", v5), ("GND2", gnd), ("CANH", can_h), ("CANL", can_l)],
                             group="can", thermal_power_w=0.35, thermal_clearance_mm=1.0),
        functional_footprint("U3", "LM5164DDAR", "Texas Instruments", 29, 14, 5, 6.4, 1.2,
                             [("VIN", bus), ("GND", gnd), ("VOUT", v5)], group="power",
                             thermal_power_w=0.8, thermal_clearance_mm=1.5),
        functional_footprint("U4", "TPS7A2033PDBVR", "Texas Instruments", 40, 14, 3, 3, 1.45,
                             [("VIN", v5), ("GND", gnd), ("VOUT", v3)], group="power",
                             thermal_power_w=0.35, thermal_clearance_mm=1.0),
        functional_footprint("J2", "1762816", "Phoenix Contact", 70, 72, 9, 10, 13,
                             [("CANH", can_h), ("CANL", can_l), ("GND", gnd)],
                             group="can", through=True),
        functional_footprint("J3", "1935187", "Phoenix Contact", 8, 58, 10, 10, 13,
                             [("ESTOP", estop_in), ("GND", gnd)], group="safety", through=True),
        functional_footprint("U5", "ACPL-247-500E", "Broadcom", 28, 58, 10.2, 6.5, 3.6,
                             [("IN", estop_in), ("GND", gnd), ("OUT", estop_safe), ("VCC", v3)],
                             group="safety", thermal_power_w=0.25, thermal_clearance_mm=1.0),
        functional_footprint("J4", "1762861", "Phoenix Contact", 52, 60, 14, 10, 13,
                             [("STO", estop_safe), ("BRAKE", brake), ("STATUS", status), ("GND", gnd)],
                             group="safety", through=True),
    ]
    return base_project("robot:coordinator:rev1", 105, 80, n, fps, 1.0,
                        {"board_role": "coordinator", "system_voltage_v": 24,
                         "can_nodes": 7, "estop_outputs": 6,
                         "evidence_language": "calculated_or_simulated_never_measured"})


def joint_project(axis: int, continuous: float, peak: float) -> dict:
    n = Nets()
    gnd = n.add("GND", 1, current=0.3, voltage=0.0)
    bus = n.add("BUS_24V", 1, voltage=24.0)
    v5 = n.add("5V", 1, current=0.4, voltage=5.0)
    v3 = n.add("3V3", 1, current=0.3, voltage=3.3)
    can_h = n.add("CAN_H", 2); can_l = n.add("CAN_L", 2)
    can_tx = n.add("CAN_TX"); can_rx = n.add("CAN_RX")
    sto = n.add("STO"); fault = n.add("FAULT")
    pwm = [n.add(f"PWM_{phase}_{side}") for phase in "ABC" for side in ("H", "L")]
    gates = [n.add(f"GATE_{phase}_{side}") for phase in "ABC" for side in ("H", "L")]
    phases = [n.add(f"PHASE_{phase}", 1) for phase in "ABC"]
    senses = [n.add(f"ISENSE_{phase}") for phase in "ABC"]
    enc = [n.add(name) for name in ("ENC_A", "ENC_B", "ENC_Z")]
    brake_gate = n.add("BRAKE_GATE"); brake_out = n.add("BRAKE_OUT", 1)
    # The headless reference layout uses a conservative 1 mm screening width.
    # Axis phase-current copper remains a critical release blocker until a
    # fabricator stackup and allowed temperature rise are selected.
    power_width = 1.0
    fps = [
        functional_footprint("J1", "1827745", "Phoenix Contact", 8, 15, 9, 9, 12,
                             [("24V", bus), ("GND", gnd), ("CANH", can_h), ("CANL", can_l),
                              ("STO", sto)], group="interface", through=True),
        functional_footprint("U1", "STM32G431CBT6", "STMicroelectronics", 35, 25, 9, 9, 1.6,
                             [("3V3", v3), ("GND", gnd), ("CAN_TX", can_tx), ("CAN_RX", can_rx),
                              ("STO", sto), ("FAULT", fault),
                              *[(f"PWM{i+1}", net) for i, net in enumerate(pwm)],
                              *[(f"ADC{i+1}", net) for i, net in enumerate(senses)],
                              *[(f"ENC{i+1}", net) for i, net in enumerate(enc)],
                              ("BRAKE", brake_gate)], group="control",
                             thermal_power_w=0.55, thermal_clearance_mm=1.2),
        functional_footprint("U2", "ISO1042DWR", "Texas Instruments", 18, 35, 10.3, 10.3, 2.65,
                             [("VCC1", v3), ("GND1", gnd), ("TXD", can_tx), ("RXD", can_rx),
                              ("VCC2", v5), ("GND2", gnd), ("CANH", can_h), ("CANL", can_l)],
                             group="can", thermal_power_w=0.35, thermal_clearance_mm=1.0),
        functional_footprint("U3", "DRV8353RSRGZR", "Texas Instruments", 61, 31, 9, 9, 1.0,
                             [("VM", bus), ("GND", gnd), ("STO", sto), ("FAULT", fault),
                              *[(f"PWM{i+1}", net) for i, net in enumerate(pwm)],
                              *[(f"GATE{i+1}", net) for i, net in enumerate(gates)]],
                             group="gate-drive", thermal_power_w=1.1, thermal_clearance_mm=2.0),
        functional_footprint("U4", "LM5164DDAR", "Texas Instruments", 18, 62, 5, 6.4, 1.2,
                             [("VIN", bus), ("GND", gnd), ("VOUT", v5)], group="power",
                             thermal_power_w=0.8, thermal_clearance_mm=1.5),
        functional_footprint("U5", "TPS7A2033PDBVR", "Texas Instruments", 32, 62, 3, 3, 1.45,
                             [("VIN", v5), ("GND", gnd), ("VOUT", v3)], group="power",
                             thermal_power_w=0.35, thermal_clearance_mm=1.0),
        functional_footprint("J2", "1935200", "Phoenix Contact", 111, 32, 17, 9, 13,
                             [("A", phases[0]), ("B", phases[1]), ("C", phases[2])],
                             group="motor", through=True),
        functional_footprint("J3", "1762874", "Phoenix Contact", 14, 82, 15, 8, 12,
                             [("3V3", v3), ("A", enc[0]), ("B", enc[1]), ("Z", enc[2]), ("GND", gnd)],
                             group="encoder", through=True),
        functional_footprint("Q7", "BSC040N08NS5ATMA1", "Infineon", 82, 78, 6, 5, 1.0,
                             [("G", brake_gate), ("D", brake_out), ("S", gnd)], group="brake",
                             thermal_power_w=1.2, thermal_clearance_mm=2.0),
        functional_footprint("J4", "1935161", "Phoenix Contact", 111, 79, 9, 8, 12,
                             [("24V", bus), ("BRAKE", brake_out)], group="brake", through=True),
    ]
    q_positions = [(78, 14), (91, 14), (78, 29), (91, 29), (78, 44), (91, 44)]
    for index, ((x, y), gate) in enumerate(zip(q_positions, gates), 1):
        phase = phases[(index - 1) // 2]
        drain = bus if index % 2 else phase
        source = phase if index % 2 else gnd
        fps.append(functional_footprint(
            f"Q{index}", "BSC040N08NS5ATMA1", "Infineon", x, y, 6, 5, 1.0,
            [("G", gate), ("D", drain), ("S", source)], group="power-stage",
            thermal_power_w=max(0.5, continuous * continuous * 0.004 / 2),
            thermal_clearance_mm=2.0))
    for index, (phase, sense) in enumerate(zip(phases, senses), 1):
        x = 56 + index * 15
        fps.append(functional_footprint(
            f"RSH{index}", "WSLP2726R0020FEA", "Vishay", x, 59, 7, 6.7, 1.0,
            [("PHASE", phase), ("GND", gnd)], group="current-sense",
            thermal_power_w=continuous * continuous * 0.002 / 3,
            thermal_clearance_mm=2.0))
        fps.append(functional_footprint(
            f"U{5 + index}", "INA240A1PWR", "Texas Instruments", x, 69, 6.4, 3, 1.2,
            [("IN+", phase), ("IN-", gnd), ("OUT", sense), ("VCC", v3), ("GND", gnd)],
            group="current-sense", thermal_power_w=0.05, thermal_clearance_mm=0.8))
    # Leave routing/thermal channels between the high-current stage, sensing,
    # isolation, and connector groups.  The compact 122 x 92 mm draft trapped
    # the multi-terminal phase and supply nets even at the router's full budget.
    for fp in fps:
        fp["x_mm"] = round(float(fp["x_mm"]) * 1.25, 3)
        fp["y_mm"] = round(float(fp["y_mm"]) * 1.20, 3)
    project = base_project(f"robot:joint-{axis}:rev1", 155, 112, n, fps, power_width,
                           {"board_role": "joint-controller", "axis_id": f"axis-{axis}",
                            "continuous_current_a": continuous, "peak_current_a": peak,
                            "system_voltage_v": 24,
                            "evidence_language": "calculated_or_simulated_never_measured"},
                           copper_layers=8)
    return project


def axis_requirement(axis: int, continuous: float, peak: float) -> dict:
    value = {
        "axis_id": f"axis-{axis}",
        "motor": {"type": "three-phase BLDC/PMSM", "bus_voltage_v": 24,
                  "continuous_current_a": continuous, "peak_current_a": peak,
                  "stage_continuous_rating_a": continuous * 1.25,
                  "stage_peak_rating_a": peak * 1.25,
                  "stage_maximum_v": 80, "effective_phase_resistance_ohm": 0.004,
                  "switching_loss_w": 3.0},
        "encoder": {"interface": "incremental A/B/Z", "current_a": 0.08},
        "brake": {"voltage_v": 24, "current_a": 0.75},
        "thermal": {"ambient_c": 40, "theta_ja_c_per_w": 10,
                    "maximum_junction_c": 125},
        "cable": {"length_m": round(1.0 + axis * 0.25, 2),
                  "current_rating_a": peak * 1.25, "connector_rating_a": peak * 1.25},
        "engineering_evidence": {}, "spice_model": None,
        "assumptions": ["24 V regulated DC bus", "10 C PCB conductor screening rise",
                        "regenerative energy is absorbed by external bus clamp"]
    }
    value["requirements_digest"] = digest(value)
    return value


def application_contract(axis_values: list[tuple[float, float]]) -> dict:
    axes = [axis_requirement(index, continuous, peak)
            for index, (continuous, peak) in enumerate(axis_values, 1)]
    architecture = {
        "kind": "distributed_six_axis_robot",
        "coordinator": {"board_id": "coordinator", "role": "CAN/E-stop coordinator"},
        "joint_boards": [{"board_id": f"joint-{i}", "axis_id": f"axis-{i}"}
                         for i in range(1, 7)],
        "supply_bus": {"supply": {"nominal_v": 24, "maximum_v": 30,
                                      "coordinator_connector_rating_a": 60,
                                      "coordinator_peak_rating_a": 100}},
        "can": {"topology": "linear trunk with short drops", "node_count": 7,
                "termination_count": 2, "bit_rate": 1_000_000,
                "maximum_total_cable_m": 10},
        "estop": {"expectations": "dual-channel external safety relay required",
                  "propagation": [f"joint-{i}:STO" for i in range(1, 7)]},
        "braking": {"regeneration_evidence": "assumption: external 24 V bus clamp absorbs returned energy"},
        "engineering_evidence": {}, "manufacturing_profile": "Eurocircuits reference"
    }
    architecture["input_digest"] = digest({"axes": [a["requirements_digest"] for a in axes]})
    architecture["output_digest"] = digest(architecture)
    return {"schema": "design-studio.application-configuration/1",
            "family": "robotic_joint_capstone", "axis_count": 6,
            "axis_requirements": axes, "system_architecture": architecture,
            "assumption_notice": "Values are engineering assumptions, not physical measurements"}


def cad_commands(project: dict) -> str:
    width = float(project["board_width_mm"]); height = float(project["board_height_mm"])
    lines = ["# DesignStudio AP242 board assembly command stream",
             f"box PCB {width / 2:.4f} {height / 2:.4f} 0.8000 {width:.4f} {height:.4f} 1.6000 0 0 0"]
    for fp in project["footprints"]:
        z = 1.6 + float(fp["h3d_mm"]) / 2
        lines.append("box {id} {x:.4f} {y:.4f} {z:.4f} {w:.4f} {h:.4f} {d:.4f} 0 0 {r:.4f}".format(
            id=fp["ref"], x=fp["x_mm"], y=fp["y_mm"], z=z,
            w=fp["body_w_mm"], h=fp["body_h_mm"], d=fp["h3d_mm"], r=fp["rot_deg"]))
    return "\n".join(lines) + "\n"


def evidence_manifest(root: Path, projects: list[tuple[str, Path]], application: Path) -> dict:
    manifest = {
        "schema": SCHEMA, "generator": "DesignStudio robot electronics builder",
        "generator_version": SOURCE_REVISION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "architecture": "one coordinator plus six isolated joint-controller configurations",
        "application_contract": {"path": str(application.relative_to(root)),
                                 "sha256": file_digest(application)},
        "board_projects": [{"board_id": board_id, "path": str(path.relative_to(root)),
                            "sha256": file_digest(path)} for board_id, path in projects],
        "evidence_state": {
            "native_routing": "pending", "native_drc": "pending",
            "connectivity": "pending", "power_integrity": "pending",
            "thermal": "pending", "mechanical_envelopes": "pending",
            "ap242_round_trip": "pending", "spice": "incomplete",
            "exact_component_bindings": "incomplete", "supplier_quotes": "incomplete",
            "hardware_measurements": "not_performed", "safety_certification": "not_performed"
        },
        "release_blockers": [
            "reviewed exact-MPN datasheet and land-pattern bindings are absent",
            "approved per-component STEP bindings are absent",
            "DigiKey/Mouser exact-MPN stock quotes are absent",
            "validated component SPICE models and a supported solver are absent",
            "dual-channel E-stop safety validation requires external hardware testing",
            "braking/regeneration clamp energy rating is an assumption",
            "engineering, electrical, mechanical, and manufacturing approvals are absent"
        ],
        "claims": {"physical_measurement": False, "manufacturing_ready": False,
                   "safety_certified": False}
    }
    manifest["manifest_digest"] = digest(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve(); projects_dir = root / "boards"
    root.mkdir(parents=True, exist_ok=True); projects_dir.mkdir(parents=True, exist_ok=True)
    axis_values = [(12.0, 24.0), (10.0, 20.0), (8.0, 16.0),
                   (5.0, 10.0), (4.0, 8.0), (3.0, 6.0)]
    application = application_contract(axis_values)
    application_path = root / "application-configuration.json"
    write_json(application_path, application)

    projects: list[tuple[str, Path]] = []
    coordinator = coordinator_project()
    coordinator_path = projects_dir / "coordinator.dsproj"
    write_json(coordinator_path, coordinator)
    (projects_dir / "coordinator.cad.txt").write_text(cad_commands(coordinator), encoding="utf-8")
    projects.append(("coordinator", coordinator_path))
    for axis, (continuous, peak) in enumerate(axis_values, 1):
        project = joint_project(axis, continuous, peak)
        path = projects_dir / f"joint-{axis}.dsproj"
        write_json(path, project)
        (projects_dir / f"joint-{axis}.cad.txt").write_text(cad_commands(project), encoding="utf-8")
        projects.append((f"joint-{axis}", path))

    manifest = evidence_manifest(root, projects, application_path)
    write_json(root / "evidence-manifest.json", manifest)
    print(json.dumps({"ok": True, "output": str(root), "boards": len(projects),
                      "application_digest": file_digest(application_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
