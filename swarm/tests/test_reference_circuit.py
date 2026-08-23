#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_binding import bind_component
from swarm.memory.component_expansion import expand
from swarm.memory.component_kg import ComponentKG
from swarm.memory.incremental_design import analyze_bound_design


def source_component() -> dict:
    return {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": "Acme", "mpn": "REF-REG", "category": "regulator"},
        "symbol": {"pins": [
            {"number": "1", "name": "VIN", "electrical_type": "power_in"},
            {"number": "2", "name": "GND", "electrical_type": "power_in"},
            {"number": "3", "name": "EN", "electrical_type": "input"},
            {"number": "4", "name": "VOUT", "electrical_type": "power_out"}]},
        "footprint": {"name": "REF_REG", "mount": "smd", "pads": [
            {"number": str(index + 1), "x_mm": index, "y_mm": 0,
             "width_mm": 0.5, "height_mm": 0.8} for index in range(4)]},
        "package_3d": {"height_mm": 1, "standoff_mm": 0},
        "electrical": {
            "power_domains": [], "required_externals": [],
            "application_circuits": [{"mode": "always on", "summary": "Tie EN to VIN",
                "datasheet_pages": [7], "evidence_summary": "Figure 3, typical application",
                "connections": [{"from_pin": "EN", "to": "VIN"}]}]},
        "evidence": {"mpn_match": "exact", "sha256": "a" * 64,
                     "package_pin_count": 4, "package_variant": "REF-REG-4"},
    }


def main() -> None:
    component = source_component()
    with tempfile.TemporaryDirectory(prefix="ds-reference-circuit-") as raw:
        root = Path(raw)
        kg = ComponentKG(str(root / "kg.json"))
        assert kg.ingest_json(component) == "REF-REG"
        _, seed = expand([{"ref": "U1", "mpn": "REF-REG",
                           "application_mode": "always on"}], kg)
        reference = [item for item in seed if item.get("evidence")]
        assert {item["pin"] for item in reference} == {"EN", "1"}
        assert all(item["evidence"]["pages"] == [7] for item in reference)

        step = root / "part.step"
        step.write_text("ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;\n")
        binding = bind_component(component, step, model_mpn="REF-REG",
                                 alignment_status="verified")
        components = [{"ref": "U1", "mpn": "REF-REG", "application_mode": "always on"}]
        correct = analyze_bound_design(components, [
            {"ref": "U1", "pin": "VIN", "net": "VIN_5V"},
            {"ref": "U1", "pin": "GND", "net": "GND"},
            {"ref": "U1", "pin": "EN", "net": "VIN_5V"},
            {"ref": "U1", "pin": "VOUT", "net": "VOUT"}],
            resolver=lambda _: binding)
        assert correct["categories"]["reference_circuit_application"]["status"] == "pass"
        wrong = analyze_bound_design(components, [
            {"ref": "U1", "pin": "VIN", "net": "VIN_5V"},
            {"ref": "U1", "pin": "GND", "net": "GND"},
            {"ref": "U1", "pin": "EN", "net": "GND"},
            {"ref": "U1", "pin": "VOUT", "net": "VOUT"}],
            resolver=lambda _: binding)
        assert wrong["categories"]["reference_circuit_application"]["status"] == "fail"
    print("Manufacturer reference-circuit tests passed")


if __name__ == "__main__":
    main()
