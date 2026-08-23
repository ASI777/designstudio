#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_binding import bind_component  # noqa: E402
from swarm.memory.incremental_design import IncrementalDesign, analyze_bound_design  # noqa: E402


def make_component(mpn, category, pins, domains, required=None):
    pads = []
    for index, pin in enumerate(pins):
        pads.append({"number": pin[0], "x_mm": float(index), "y_mm": 0.0,
                     "width_mm": 0.5, "height_mm": 0.8})
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Fixture", "mpn": mpn,
                      "category": category, "description": mpn},
        "symbol": {"ref_des_prefix": "U", "pins": [
            {"number": number, "name": name, "electrical_type": etype}
            for number, name, etype in pins]},
        "electrical": {"power_domains": domains,
                       "required_externals": required or []},
        "footprint": {"name": mpn + "_FP", "mount": "smd", "pads": pads,
                      "body": {"width_mm": max(1, len(pins)), "length_mm": 2,
                               "height_mm": 1}},
        "package_3d": {"height_mm": 1, "standoff_mm": 0, "shape": "box"},
        "evidence": {"package_pin_count": len(pins),
                     "package_variant": f"{mpn}-PACKAGE"},
    }


def fixtures(tmp: Path):
    step = tmp / "fixture.step"
    step.write_text("ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;\n")
    regulator = make_component("REG3V3", "regulator", [
        ("1", "IN", "power_in"), ("2", "GND", "power_in"),
        ("3", "OUT", "power_out")], [
        {"name": "VIN", "vmin_v": 4.5, "vnom_v": 5.0, "vmax_v": 5.5,
         "max_current_a": 0.25, "pins": ["1"]},
        {"name": "VOUT", "vmin_v": 3.25, "vnom_v": 3.3, "vmax_v": 3.35,
         "max_current_a": 1.0, "pins": ["3"]}], [
        {"purpose": "Output capacitor", "value": "1uF", "mandatory": True,
         "connect_between": ["OUT", "GND"]}])
    load = make_component("LOAD3V3", "mcu", [
        ("1", "VDD", "power_in"), ("2", "GND", "power_in")], [
        {"name": "VDD", "vmin_v": 3.0, "vnom_v": 3.3, "vmax_v": 3.6,
         "max_current_a": 0.2, "pins": ["1"]}])
    bad_load = make_component("LOAD5V", "mcu", [
        ("1", "VDD", "power_in"), ("2", "GND", "power_in")], [
        {"name": "VDD", "vmin_v": 4.75, "vnom_v": 5.0, "vmax_v": 5.25,
         "max_current_a": 0.15, "pins": ["1"]}])
    capacitor = make_component("CAP1UF", "capacitor", [
        ("1", "1", "passive"), ("2", "2", "passive")], [])
    library = {component["component"]["mpn"]: bind_component(
        component, step, model_mpn=component["component"]["mpn"],
        alignment_status="verified")
        for component in (regulator, load, bad_load, capacitor)}
    regulator_model = library["REG3V3"]
    regulator_model["simulation_models"] = {
        "control_loop": {"phase_margin_deg": 58.0, "gain_margin_db": 16.0},
        "behavioral_power": {
            "schema": "design-studio.behavioral-power/1",
            "kind": "linear_regulator", "input_pin": "IN", "output_pin": "OUT",
            "input_voltage_v": 5.0, "dropout_v": 0.25,
            "max_output_current_a": 1.0, "quiescent_current_a": 0.0001,
            "ambient_c": 40.0, "theta_ja_c_per_w": 45.0,
            "max_junction_c": 125.0}}
    from swarm.memory.component_binding import binding_digest
    regulator_model["binding_digest"] = binding_digest(regulator_model)
    return library


def test_incremental_schematic_and_dependency_cone():
    with tempfile.TemporaryDirectory() as tmp:
        library = fixtures(Path(tmp))
        design = IncrementalDesign(lambda mpn: library.get(mpn))
        design.add_component("U1", "REG3V3", [20, 20])
        design.add_component("U2", "LOAD3V3", [80, 20])
        design.add_component("C1", "CAP1UF", [50, 40])
        original_position = list(design.symbol_positions["U2"])
        design.connect("U1", "IN", "VIN_5V")
        design.connect("U1", "GND", "GND")
        design.connect("U1", "OUT", "3V3")
        design.connect("U2", "GND", "GND")
        design.connect("U2", "VDD", "3V3")
        design.connect("C1", "1", "3V3")
        report = design.connect("C1", "2", "GND")
        assert report["recomputed_nets"] == ["3V3", "GND"] or \
            report["recomputed_nets"] == ["3V3"]
        rail = next(item for item in report["categories"]["dc_operating_point"]["rails"]
                    if item["net"] == "3V3")
        assert rail["status"] == "pass"
        assert abs(rail["voltage_v"] - 3.3) < 1e-9
        assert abs(rail["load_current_a"] - 0.2) < 1e-9
        assert report["categories"]["erc"]["status"] == "pass"
        assert report["categories"]["stability"]["status"] == "pass"
        assert report["categories"]["spice"]["status"] == "pass"
        sim = report["categories"]["spice"]["results"][0]
        assert abs(sim["loss_w"] - 0.3405) < 1e-9
        assert sim["junction_c"] < 125.0
        assert design.symbol_positions["U2"] == original_position
        u2_symbol = next(symbol for symbol in report["schematic"]["symbols"]
                         if symbol["ref"] == "U2")
        assert all("side" in pin and "order" in pin and "net_name" in pin
                   for pin in u2_symbol["pins"])

        removed_cap = design.remove_component("C1")
        assert removed_cap["categories"]["stability"]["status"] == "fail"
        assert any(finding["rule"] == "required_external_missing"
                   for finding in removed_cap["categories"]["stability"]["findings"])

        changed = design.replace_component("U2", "LOAD5V")
        assert "3V3" in changed["recomputed_nets"]
        mismatches = [finding for finding in
                      changed["categories"]["dc_operating_point"]["findings"]
                      if finding["rule"] == "load_voltage_mismatch"]
        assert mismatches and changed["status"] == "fail"
        assert design.symbol_positions["U2"] == original_position

        disconnected = design.disconnect("U2", "VDD")
        assert disconnected["categories"]["erc"]["status"] == "fail"
        assert any(finding["rule"] == "power_pin_unconnected"
                   for finding in disconnected["categories"]["erc"]["findings"])

        replay = analyze_bound_design(
            [{"ref": "U1", "mpn": "REG3V3"}, {"ref": "U2", "mpn": "LOAD3V3"},
             {"ref": "C1", "mpn": "CAP1UF"}],
            [{"ref": "U1", "pin": "IN", "net": "VIN_5V"},
             {"ref": "U1", "pin": "GND", "net": "GND"},
             {"ref": "U1", "pin": "OUT", "net": "3V3"},
             {"ref": "U2", "pin": "GND", "net": "GND"},
             {"ref": "U2", "pin": "VDD", "net": "3V3"},
             {"ref": "C1", "pin": "1", "net": "3V3"},
             {"ref": "C1", "pin": "2", "net": "GND"}],
            resolver=lambda mpn: library.get(mpn))
        assert len(replay["event_log"]) == 10  # three component adds + seven connections
        assert replay["categories"]["erc"]["status"] == "pass"

        incomplete = analyze_bound_design(
            [{"ref": "U9", "mpn": "MISSING"}], [], resolver=lambda _: None)
        assert incomplete["status"] == "incomplete"
        assert incomplete["categories"]["component_binding"]["status"] == "incomplete"


if __name__ == "__main__":
    test_incremental_schematic_and_dependency_cone()
    print("Incremental design tests passed")
